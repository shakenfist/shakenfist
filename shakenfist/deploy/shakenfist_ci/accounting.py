# Copyright 2019 Michael Still and contributors
"""Reconcile a hypervisor's published accounting with what libvirt is running.

``/admin/resources`` publishes, per node, three figures measured from
libvirt rather than from any ledger: ``instances_active``,
``cpu_measured`` and ``ram_measured``. ``sf-resources`` builds all three
in one pass over ``get_active_sf_domains()`` --
``instances_active`` counts the domains, ``cpu_measured`` sums
``domain.info()[3]`` and ``ram_measured`` sums ``domain.info()[2]``
(KiB) and converts the sum to MB (``shakenfist/daemons/resources/
main.py``). Those three are what the CPU and memory pre-filters measure a
node against, so a node whose measurement has drifted from its
hypervisor refuses or admits work on a figure which is not true -- which
is the diagnosis #3772 reaches each time it is looked at.

``cluster_ci_tests/test_nodes.py`` is the caller, against a deployed
cluster: it samples each hypervisor's domains over the mesh with
``virsh`` and compares them with that node's entry. The arithmetic and
the parsing live here rather than in that test for the reason
``sizing.py`` gives for itself: this module imports nothing from the
rest of the suite and nothing from ``shakenfist_client``, so
``shakenfist/tests/test_ci_node_accounting.py`` can load it by path and
exercise it on every pull request. Cluster CI only runs in the merge
queue, where a bug in a comparison which fails jobs on clusters that are
fine would be expensive to find.

The sample is deliberately one command per node rather than a ``virsh
list`` followed by a ``dominfo`` per domain: a reading is only worth
comparing if nothing changed while it was taken, and each extra ssh
round trip is another second in which a sibling test's instance can
start or stop on the node being read. It carries the node's own clock
so that a disagreement can be reported as "the publish is older than
this sample" (which resolves itself) rather than as drift.
"""


# What the sample's per-domain stanzas are introduced by. Distinctive
# rather than minimal so that nothing ``virsh dominfo`` prints can be
# mistaken for one.
DOMAIN_MARKER = 'SFCI-DOMAIN '

# The ``virsh dominfo`` labels the two per-domain figures come from.
# "Used memory" is ``virDomainGetInfo``'s memory field, which is
# ``domain.info()[2]``: the balloon target, so a requested figure and
# not a resident one. That is the same field sf-resources sums, which is
# the whole point -- this compares the two readings of one number, not
# two different numbers.
VCPUS_LABEL = 'CPU(s):'
MEMORY_LABEL = 'Used memory:'

# One sample of a node's active Shaken Fist domains, for sh -c. The
# timestamp comes first and is taken before the enumeration, so it is
# never later than the state the stanzas below it describe.
#
# ``virsh list`` without --all lists the domains libvirt considers
# active, which is the same set ``get_active_sf_domains()`` returns
# (running, paused or PM suspended; a powered off domain is excluded).
# Foreign domains are listed too and are dropped by the parser rather
# than by a filter here, so that the dropping is unit tested.
#
# A dominfo which fails because its domain stopped between the list and
# the read leaves a stanza with no figures in it, which the parser
# reports as unreadable -- so the caller must run this with
# check_exit_code=False, since that failure is also the exit code of the
# loop's last command.
DOMAIN_SAMPLE_COMMAND = (
    'date +%s.%N; '
    'for d in $(virsh list --name); do '
    'echo "' + DOMAIN_MARKER + '$d"; virsh dominfo "$d"; done')

# The figures compared, and where each comes from in a per_node entry.
# instances_total is deliberately not here: sf-resources increments it
# only for an active domain, so it is a second copy of
# instances_active rather than an independent figure, and pinning that
# would pin an implementation detail of the daemon rather than the
# accounting.
MEASURED_FIGURES = ('instances_active', 'cpu_measured', 'ram_measured')


def parse_domain_sample(output):
    """One node's domain sample, as a dict, from the command's output.

    Returns ``{'sampled_at': float, 'domains': {instance_uuid: {'vcpus':
    int, 'memory_kib': int}}, 'unreadable': [domain name, ...]}``.

    ``unreadable`` holds the Shaken Fist domains which were listed but
    whose figures could not be read -- almost always a domain which
    stopped between the list and its ``dominfo``, which makes the whole
    sample one to retry rather than one to compare, because the published
    figures may legitimately describe either side of that change.

    Raises ValueError when the output has no leading timestamp, which
    means the command did not run as written (no shell, no ``date``, or
    output captured from something else entirely) and no part of it can
    be trusted.
    """
    sampled_at = None
    domains = {}
    unreadable = []
    current = None
    figures = {}

    def _close(name, found):
        if name is None:
            return
        if set(found) == {'vcpus', 'memory_kib'}:
            domains[name] = found
        else:
            unreadable.append(name)

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        if sampled_at is None:
            try:
                sampled_at = float(line)
            except ValueError:
                raise ValueError(
                    'A domain sample must start with the node clock '
                    'date +%%s.%%N printed, but it starts with %r' % line)
            continue

        if line.startswith(DOMAIN_MARKER):
            _close(current, figures)
            current = line[len(DOMAIN_MARKER):].strip()
            figures = {}
            # Only our own domains are accounted for: the measurement
            # this is compared with filters on the "sf:" prefix too, and
            # a node running a foreign domain must not read as drifted
            # because of it. The uuid is the key because that is what a
            # caller holds -- an instance uuid, not a domain name.
            if current.startswith('sf:'):
                current = current[len('sf:'):]
            else:
                current = None
            continue

        if current is None:
            continue

        if line.startswith(VCPUS_LABEL):
            value = line[len(VCPUS_LABEL):].strip()
            if value.isdigit():
                figures['vcpus'] = int(value)
        elif line.startswith(MEMORY_LABEL):
            # "131072 KiB", or "no limit" from a domain libvirt reports
            # as using no memory at all -- which an active domain does
            # not, so that reading is treated as unreadable rather than
            # as zero.
            value = line[len(MEMORY_LABEL):].strip().split()
            if value and value[0].isdigit():
                figures['memory_kib'] = int(value[0])

    _close(current, figures)
    if sampled_at is None:
        # Output with no line in it at all: the command did not run
        # either, so this is the same ignorance as output which does not
        # start with the clock, and not an idle node.
        raise ValueError(
            'A domain sample must start with the node clock date +%s.%N '
            'printed, but it is empty')
    return {'sampled_at': sampled_at, 'domains': domains,
            'unreadable': unreadable}


def measured_from_domains(domains):
    """What a node publishing this domain set must be measuring.

    The memory sum is taken in KiB and converted once, not per domain,
    because that is what the daemon does
    (``total_instance_actual_memory // 1024`` after the loop). Two
    domains of 1536 KiB are 3 MB here and 2 MB if each is floored first,
    so the order matters and is pinned by a unit test.
    """
    return {
        'instances_active': len(domains),
        'cpu_measured': sum(d['vcpus'] for d in domains.values()),
        'ram_measured': sum(
            d['memory_kib'] for d in domains.values()) // 1024,
    }


def accounting_mismatches(domains, per_node_entry):
    """How a node's libvirt state and its per_node entry disagree.

    ``domains`` is the ``domains`` member of a sample; ``per_node_entry``
    is that node's entry in ``/admin/resources``. Returns one dict per
    disagreeing figure (``figure``, ``libvirt``, ``published``), in the
    order ``MEASURED_FIGURES`` names them, and an empty list when the two
    readings agree.

    A figure the entry does not carry at all counts as a disagreement
    rather than being skipped: a node which has stopped publishing one of
    these is a node whose accounting cannot be checked, which is the
    thing this exists to notice.
    """
    expected = measured_from_domains(domains)
    mismatches = []
    for figure in MEASURED_FIGURES:
        published = per_node_entry.get(figure)
        if published != expected[figure]:
            mismatches.append({
                'figure': figure,
                'libvirt': expected[figure],
                'published': published,
            })
    return mismatches
