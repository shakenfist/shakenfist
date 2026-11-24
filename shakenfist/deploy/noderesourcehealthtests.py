# Copyright 2026 Michael Still and contributors

"""Assertions for node resource health, against a real storage fault.

Issue 4960: the probe, the evaluator, the cluster cascade and ``sf-ctl
clear-node-error`` all have unit coverage, but nothing had ever made a
node's storage actually fail and watched the cluster react. The part
worth proving is the one that was broken during the sf-6 incident this
feature came from: the scheduler kept placing instances onto a node
whose blob store had been dead for hours. A unit test cannot see that,
because the thing under test is the composition of five mechanisms --
the probe thread, the node state write, the active-node prefilter, the
create handler's node guard, and the operator's recovery command --
running in four different processes.

This runs from nodelifecycletests.sh for the same reason
apivalidationrollbacktests.py does: the stestr cluster suite runs many
tests concurrently against one shared cluster, and taking a hypervisor
out of scheduling inside it would fail unrelated tests. It is not a
theoretical worry. ``slim-tier``'s three nodes are all hypervisors and
sum to a cluster ledger of exactly ``MINIMUM_HYPERVISOR_LEDGER``, so
erroring any one of them puts the cluster below the floor
``cluster_ci_tests/test_nodes.py`` asserts, and the two non-network
hypervisors ``test_network_lifecycle.py`` pins instances onto are
exactly the two a fault here could choose from. This job disturbs its
cluster by design and runs nothing else while it does, which is what
makes the fault safe to inject at all. The non-invasive half -- that
every node publishes a healthy gauge and a probed heartbeat -- lives in
the cluster suite as ``cluster_ci_tests/test_node_health.py``, so the
merge queue still notices a health thread which stopped running.

The fault is a read-only bind mount over ``STORAGE_PATH/uploads``.
Three things make that the narrowest fault which still proves the
point: ``ST_RDONLY`` is one of the two conditions the cheap statvfs
tier detects, so detection is bounded by NODE_HEALTH_CHECK_INTERVAL
rather than by the write heartbeat's much longer interval; ``uploads``
is declared only by Upload, so the cluster cascade errors no instances
and drops no blob replicas; and ``umount`` restores the directory
exactly as it was, with no filesystem left in a state a later exercise
in this job could inherit. The node still goes to ``error`` and still
leaves scheduling, because the blast radius follows the dependency
lists while the node state does not.

What is deliberately NOT asserted here is the other half of issue
4960's step 3: that the node's blob replicas stop counting and the
replicator rebuilds them elsewhere. That needs the ``blobs`` path
faulted, which drops the node's blob location rows -- including, on this
topology, the only replica of the cached base image the lifecycle tests
which follow then boot from. It is a test for a cluster that can be
thrown away afterwards, not for this one.

Only shakenfist_client and requests are imported, because this runs from
the deployed /srv/shakenfist/venv, which does not carry the test tree's
dependencies. The pure helpers are exercised by
shakenfist/tests/test_noderesourcehealth_script.py.
"""

import json
import os
import shlex
import subprocess
import sys
import time

import requests
from shakenfist_client import apiclient


# shakenfist.config.STORAGE_PATH, which the deployer does not move, and
# upload.Upload.health_dependencies' one entry. Repeated rather than
# imported, like every other constant in the CI tree: this runs against a
# deployed cluster from a venv with no server test tree in it, and the
# unit tests above check each of these against the server's own value.
STORAGE_PATH = '/srv/shakenfist'
FAULT_SUBDIR = 'uploads'
FAULT_PATH = os.path.join(STORAGE_PATH, FAULT_SUBDIR)

# shakenfist.resource_health.HEARTBEAT_FILENAME, and the status string
# PathCheck returns for a read-only mount.
HEARTBEAT_FILENAME = '_heartbeat'
READONLY_STATUS = 'readonly'

# shakenfist.config.RESOURCES_METRICS_PORT, where the node-health thread
# publishes node_resource_health.
RESOURCES_METRICS_PORT = 13001
METRICS_TIMEOUT = 5

# shakenfist.constants.NODE_ACTIVE_STATES, which is also what the
# scheduler's prefilter='active' resolves to.
NODE_ACTIVE_STATES = frozenset({'initial', 'created', 'degraded'})

# The systemd EnvironmentFile the daemons read. SHAKENFIST_NODE_NAME in
# it is the SF-level node name, which is not necessarily the kernel
# hostname -- the two diverged in this environment once already and a
# guard which compared the wrong one silently never fired, see
# nodelifecycletests.sh.
SF_CONFIG = '/etc/sf/config'
SF_CTL = '/srv/shakenfist/venv/bin/sf-ctl'

# Detection is bounded by NODE_HEALTH_CHECK_INTERVAL (60s by default)
# plus one probe timeout, and recovery by the same interval plus the
# scheduler's own metrics cache. Three intervals of slack each: these
# are failure deadlines, not settle waits, so a healthy cluster never
# spends them.
DETECT_TIMEOUT = 240
RECOVER_TIMEOUT = 240
DELETE_TIMEOUT = 300
POLL_INTERVAL = 5

# A create which names the faulted node, used in both directions: it
# must land before the fault and be refused after it. One vCPU, 128 MB,
# no base image and a single empty 1 GB disk -- the cheapest create the
# suite knows how to make (see test_saturation.py's node fill). The
# landing one is admitted and queued to start on the node as a real
# domain, and is deleted straight away, so it costs a short-lived
# domain as well as the database writes.
PIN_PROBE_CPUS = 1
PIN_PROBE_MEMORY_MB = 128
PIN_PROBE_DISK = [{'size': 1, 'type': 'disk'}]


def log(msg):
    print(msg, flush=True)


def fail(msg):
    log('FAIL: %s' % msg)
    sys.exit(1)


def sf_node_name(config_text):
    """The SF node name from an /etc/sf/config body, or None.

    A tiny parser rather than sourcing the file, because learning this
    process' own name is no reason to inherit the whole daemon
    environment -- the database credentials in it included. The one
    child which does need them gets them in a subshell of its own
    below. Values may be quoted; a commented or absent assignment reads
    as absent, which the caller treats as fatal rather than guessing.
    """
    found = None
    for line in config_text.splitlines():
        line = line.strip()
        if line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        if key.strip() != 'SHAKENFIST_NODE_NAME':
            continue
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        # Last assignment wins, which is what sourcing the file would do.
        found = value or None
    return found


def mount_is_readonly(findmnt_output):
    """Whether ``findmnt -n -o OPTIONS --target`` reported a read-only mount.

    Matches whole options rather than testing for the substring 'ro':
    'relatime', 'errors=remount-ro' and 'rw' all contain it, so a
    substring test would call every ordinary mount read-only and the
    fault would read as injected when it was not.
    """
    for line in findmnt_output.splitlines():
        line = line.strip()
        if not line:
            continue
        return 'ro' in {o.strip() for o in line.split(',')}
    return False


def parse_gauge(text, name):
    """The value of one unlabelled Prometheus gauge, or None.

    The gauges sf-resources publishes carry no labels, so a sample line
    is the metric name, whitespace and a value, and anything carrying
    this name as a prefix is a different metric. Returns None rather
    than zero when the gauge is absent, because "this node reports
    unhealthy" and "this node does not publish that gauge at all" are
    different findings and this script has something to say about both.
    A third field is the optional sample timestamp the exposition format
    allows; the value is the second either way.
    """
    for line in text.splitlines():
        fields = line.split()
        if len(fields) not in (2, 3) or fields[0] != name:
            continue
        try:
            return float(fields[1])
        except ValueError:
            continue
    return None


def health_diagnosis(events):
    """The newest resource-health diagnosis in a node's event list, or None.

    apply_result() writes one ``health`` event carrying ``affected_types``
    and ``failed`` on each transition into error, on a channel distinct
    from the audit log precisely so the cascade's own events cannot crowd
    it out. Events come back newest first, so the first such row is the
    current diagnosis. A ``health`` row without that payload is some
    other health event and is skipped rather than returned empty.
    """
    for event in events or []:
        if event.get('type') != 'health':
            continue
        extra = event.get('extra')
        if not isinstance(extra, dict):
            continue
        if not isinstance(extra.get('affected_types'), (list, tuple)):
            continue
        return event
    return None


def choose_victim(nodes, script_host, network_node):
    """Which node to fault, or None.

    A hypervisor, because the assertion that matters is about placement,
    and never one of two nodes. Not this script's own host: its
    ``uploads`` path is where every artifact upload in this job lands,
    and erroring the node the job is driving from would take the API the
    assertions talk to out of the active set it is asserting about. Not
    the network node either: the cluster-wide networknode-* queues are
    drained only by the node holding that role and there is no failover
    for it, which is the same carve-out nodelifecycletests.sh makes
    before it picks a node to kill.
    """
    for node in nodes:
        if not node.get('is_hypervisor'):
            continue
        if node.get('name') in (script_host, network_node):
            continue
        if node.get('state') not in NODE_ACTIVE_STATES:
            continue
        return node
    return None


def run(args, check=True):
    """Run a command locally, returning its stdout."""
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        fail('%s exited %d\n  stdout: %s\n  stderr: %s'
             % (' '.join(args), result.returncode, result.stdout,
                result.stderr))
    return result.stdout


def node_run(node_name, command, check=True):
    """Run a shell command as root on another cluster node.

    ``sudo ssh`` as the local root, reaching ``debian@<node>`` over the
    management mesh -- the same channel and the same key
    nodelifecycletests.sh already uses to stop Shaken Fist on its victim.
    """
    return run([
        'sudo', 'ssh', '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
        '-o', 'ConnectTimeout=10', 'debian@%s' % node_name, '--',
        'sudo bash -c %s' % shlex.quote(command)], check=check)


def scrape_health_gauge(node):
    """node_resource_health as the node itself publishes it, or None.

    A refused scrape reports as absent rather than raising: every caller
    polls to a deadline, and one failed HTTP GET says nothing about the
    property under test.
    """
    try:
        resp = requests.get(
            'http://%s:%d/metrics' % (node['ip'], RESOURCES_METRICS_PORT),
            timeout=METRICS_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        log('  ... could not scrape %s: %s' % (node['name'], e))
        return None
    return parse_gauge(resp.text, 'node_resource_health')


def fault_storage(node_name):
    """Make the node's uploads path read-only, in place.

    Two steps because a bind mount does not take its own options: the
    bind is created first and then remounted read-only, which is the
    documented util-linux sequence. Nothing underneath is modified, so
    ``umount`` is a complete undo.
    """
    node_run(node_name, 'mount --bind %s %s' % (FAULT_PATH, FAULT_PATH))
    node_run(node_name, 'mount -o remount,ro,bind %s' % FAULT_PATH)


def clear_fault(node_name):
    """Remove the read-only bind mount, if it is still there.

    Idempotent: this is the undo path, reached from the happy path and
    from a failure half way through, and a directory which is no longer
    a mount point is the state it wants rather than an error. A umount
    which fails on a mount that is still there (EBUSY, say) falls back
    to a lazy one, and is logged either way: left silent, the health
    thread re-errors the node and the job fails four minutes later on
    "did not return to an active state", naming the wrong cause.
    """
    outcome = node_run(
        node_name,
        'if ! findmnt -n %(p)s >/dev/null; then echo clear; exit 0; fi; '
        'if umount %(p)s; then echo clear; exit 0; fi; '
        'if umount -l %(p)s; then echo lazy; exit 0; fi; '
        'echo stuck' % {'p': FAULT_PATH},
        check=False).strip()
    if outcome == 'lazy':
        log('WARNING: %s on %s needed a lazy umount; it is detached '
            'but may still be busy' % (FAULT_PATH, node_name))
    elif outcome != 'clear':
        log('WARNING: could not remove the read-only bind mount over %s '
            'on %s (%r); the node will be re-errored by its health '
            'thread' % (FAULT_PATH, node_name, outcome or 'no answer'))


def clear_node_error(node_name, check=True):
    """Run the operator's documented recovery command.

    In a subshell with /etc/sf/config sourced, because sf-ctl reads the
    database gateway address from the environment the daemons' systemd
    EnvironmentFile carries -- the same wrapper nodelifecycletests.sh
    uses for its own sf-ctl calls.
    """
    return run([
        'bash', '-c',
        'set -a; . %s; set +a; exec %s clear-node-error --node-name %s'
        % (shlex.quote(SF_CONFIG), shlex.quote(SF_CTL),
           shlex.quote(node_name))], check=check)


def await_node_state(client, node_name, predicate, deadline_seconds, what):
    """Poll a node's state until predicate accepts it. Never returns None."""
    deadline = time.time() + deadline_seconds
    last = None
    while time.time() < deadline:
        try:
            last = client.get_node(node_name).get('state')
        except apiclient.APIException as e:
            # One refused request is not an answer about the node. The
            # deadline is, and the last thing seen goes in its message.
            last = '%s: %s' % (e.__class__.__name__, e)
        else:
            if predicate(last):
                return last
        time.sleep(POLL_INTERVAL)
    fail('Node %s did not %s within %d seconds; the last reading was %r'
         % (node_name, what, deadline_seconds, last))


def await_gauge(node, expected, deadline_seconds):
    """Poll node_resource_health until it reads expected."""
    deadline = time.time() + deadline_seconds
    last = None
    while time.time() < deadline:
        last = scrape_health_gauge(node)
        if last == expected:
            return
        time.sleep(POLL_INTERVAL)
    fail('node_resource_health on %s is %r, expected %r, after %d seconds'
         % (node['name'], last, expected, deadline_seconds))


def in_scheduler_view(client, node):
    """Whether the node is a placement candidate, as the scheduler reads it.

    ``/admin/resources``' ``per_node`` mapping is built by
    ``summarize_cluster_resources()`` from ``get_active_node_metrics()``
    -- the same read the placement pre-filter makes, not a second view of
    it -- so a node's presence there is its candidacy. Keyed by node uuid.
    """
    per_node = client.get_cluster_resources().get('per_node') or {}
    return node['uuid'] in per_node


def pinned_create(client, name, node_name, created):
    """Create the cheapest possible instance, naming one node.

    Raw rather than through any wrapper, because both callers want to
    see the refusal rather than have it waited out: one asserts the
    create lands, the other asserts it is refused. Every uuid this
    returns is recorded in ``created`` so that the cluster is left as
    empty as the lifecycle exercises which follow assume.
    """
    inst = client._request_url('POST', '/instances', data={
        'name': name,
        'cpus': PIN_PROBE_CPUS,
        'memory': PIN_PROBE_MEMORY_MB,
        'disk': PIN_PROBE_DISK,
        'placed_on': node_name,
    }).json()
    created.append(inst['uuid'])
    return inst


def await_instances_gone(client, created, deadline_seconds=DELETE_TIMEOUT):
    """Wait for every instance this script made to be gone.

    The deletes are asynchronous, and the lifecycle exercises after this
    one count the instances they create -- the same handover
    apivalidationrollbacktests.py makes for the same reason.
    """
    if not created:
        return
    deadline = time.time() + deadline_seconds
    remaining = list(created)
    while time.time() < deadline:
        try:
            listed = {i['uuid'] for i in client.get_instances()}
        except apiclient.APIException as e:
            log('  ... could not list instances: %s' % e)
            time.sleep(POLL_INTERVAL)
            continue
        remaining = [u for u in created if u in listed]
        if not remaining:
            return
        time.sleep(POLL_INTERVAL)
    fail('These instances did not delete within %d seconds, so the cluster '
         'is not as empty as the exercises after this one assume: %s'
         % (deadline_seconds, remaining))


def delete_quietly(client, uuid):
    """Ask for a delete without waiting for it, and without raising."""
    try:
        client.delete_instance(uuid, async_request=True)
    except apiclient.APIException as e:
        log('  ... could not delete %s: %s' % (uuid, e))


def assert_pin_lands(client, node, name, created, deadline_seconds):
    """A create naming the node is accepted and placed there.

    The control for the refusal below: without it a 404 after the fault
    proves only that something answered 404. A 507 is retried under a
    fresh name because a node can briefly refuse work it has room for,
    and because the refused create holds the old name until it
    error-deletes.
    """
    deadline = time.time() + deadline_seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            inst = pinned_create(client, '%s-%d' % (name, attempt),
                                 node['name'], created)
            break
        except apiclient.InsufficientResourcesException as e:
            if time.time() > deadline:
                fail('A create naming the healthy node %s could not be '
                     'placed within %d seconds: %s'
                     % (node['name'], deadline_seconds, e.text))
            log('  ... create refused with a 507, retrying: %s' % e.text)
            time.sleep(POLL_INTERVAL)
        except apiclient.ResourceNotFoundException as e:
            fail('A create naming the healthy node %s was refused as if the '
                 'node were not there: %s' % (node['name'], e.text))

    placed_on = inst.get('node')
    delete_quietly(client, inst['uuid'])
    if placed_on not in (node['name'], node.get('uuid')):
        fail('A create naming %s was placed on %r instead, so this control '
             'proves nothing about that node' % (node['name'], placed_on))
    log('OK: a create naming %s lands there while it is healthy'
        % node['name'])


def assert_pin_refused(client, node_name, name, created, deadline_seconds):
    """A create which names the errored node is refused with a 404.

    Two independent guards can produce this, and which one does is worth
    knowing rather than papering over. The create handler checks the
    named node is in ``created`` before it builds anything and answers
    "not ready"; were that guard ever removed the scheduler would still
    refuse, because an errored node is absent from the active node list
    and a forced candidate which is not in it raises
    CandidateNodeNotFoundException. Either answer is the cluster
    declining to place on a node with dead storage, so both are accepted
    and the one which fired is logged.

    Polled to a deadline because both guards read state the API caches,
    so for a few seconds after the node errors a create can still be
    admitted.
    """
    deadline = time.time() + deadline_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        inst = None
        try:
            inst = pinned_create(client, '%s-%d' % (name, attempt), node_name,
                                 created)
        except apiclient.ResourceNotFoundException as e:
            log('OK: a create naming the errored %s is refused: %s'
                % (node_name, e.text))
            return
        except apiclient.InsufficientResourcesException:
            # Still a candidate, but with no room. Not the answer being
            # asserted, so keep polling.
            pass

        if inst:
            delete_quietly(client, inst['uuid'])
        time.sleep(POLL_INTERVAL)

    fail('A create naming %s was still being accepted %d seconds after the '
         'node reached error, so a dead storage path does not stop placement '
         '-- this is the sf-6 regression' % (node_name, deadline_seconds))


def assert_absent_from_scheduler_view(client, node, deadline_seconds):
    """The node leaves the scheduler's candidate set.

    This, not the 404 above, is issue 4960's step 3 and the sf-6
    regression itself: what went wrong there was the scheduler choosing a
    node whose store was dead, and an unpinned create cannot prove that
    on a five hypervisor cluster because it would rarely have chosen the
    faulted node anyway.
    """
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        try:
            if not in_scheduler_view(client, node):
                log('OK: %s is no longer a placement candidate' % node['name'])
                return
        except apiclient.APIException as e:
            log('  ... could not read /admin/resources: %s' % e)
        time.sleep(POLL_INTERVAL)
    fail('%s is still a placement candidate %d seconds after it reached '
         'error' % (node['name'], deadline_seconds))


def assert_diagnosis(client, node):
    """The error names the affected type, the check, the status and the detail."""
    event = health_diagnosis(client.get_node_events(node['name'], limit=100))
    if not event:
        fail('No resource health event was recorded against %s, so the '
             'cluster cascade has no blast radius to read' % node['name'])

    log('  diagnosis: %s' % json.dumps(event, sort_keys=True, default=str))
    extra = event['extra']

    if sorted(extra['affected_types']) != ['upload']:
        fail('The diagnosis blames %r, but only Upload declares %s -- the '
             'blast radius does not follow the dependency lists'
             % (extra['affected_types'], FAULT_PATH))

    failed = {f.get('path'): f for f in extra.get('failed') or []}
    if FAULT_PATH not in failed:
        fail('The diagnosis names failed checks %r, which does not include '
             'the path that was faulted (%s)' % (sorted(failed), FAULT_PATH))
    if failed[FAULT_PATH].get('status') != READONLY_STATUS:
        fail('The check on %s reported status %r, not %r, so the statvfs '
             'tier did not see the read-only mount'
             % (FAULT_PATH, failed[FAULT_PATH].get('status'), READONLY_STATUS))

    message = str(event.get('message') or '')
    for fragment in ('upload', FAULT_PATH, READONLY_STATUS):
        if fragment not in message:
            fail('The node error reason %r does not name %r, so an operator '
                 'reading it cannot tell what failed' % (message, fragment))
    log('OK: the diagnosis names the type, the check, the status and the detail')


def assert_controls(client, victim, created):
    """Everything the fault is supposed to change is true beforehand.

    Each of these is a control rather than a sanity check: an assertion
    that something is absent, refused or zero after the fault says
    nothing unless it was present, accepted or one before it.
    """
    if victim['state'] not in NODE_ACTIVE_STATES:
        fail('%s is in state %r before the fault'
             % (victim['name'], victim['state']))
    await_gauge(victim, 1.0, DETECT_TIMEOUT)
    log('OK: %s publishes node_resource_health 1 while healthy' % victim['name'])

    heartbeat = node_run(
        victim['name'],
        'test -f %s && echo present'
        % os.path.join(FAULT_PATH, HEARTBEAT_FILENAME))
    if 'present' not in heartbeat:
        fail('%s has no %s heartbeat, so the write tier never probed it'
             % (victim['name'], FAULT_PATH))
    log('OK: the write heartbeat is present in %s' % FAULT_PATH)

    if not in_scheduler_view(client, victim):
        fail('%s is not a placement candidate before the fault, so its '
             'absence afterwards would prove nothing' % victim['name'])
    log('OK: %s is a placement candidate while healthy' % victim['name'])

    assert_pin_lands(client, victim, 'health-control', created, RECOVER_TIMEOUT)


def main():
    if not os.access(SF_CONFIG, os.R_OK):
        fail('%s is not readable, so this script cannot tell which node it '
             'is running on' % SF_CONFIG)
    with open(SF_CONFIG) as f:
        script_host = sf_node_name(f.read())
    if not script_host:
        fail('SHAKENFIST_NODE_NAME is not set in %s' % SF_CONFIG)

    client = apiclient.Client(async_strategy=apiclient.ASYNC_PAUSE)
    nodes = client.get_nodes()
    network_node = ([n['name'] for n in nodes if n.get('is_network_node')]
                    or [None])[0]
    log('Script host is %s; network node is %s' % (script_host, network_node))

    victim = choose_victim(nodes, script_host, network_node)
    if not victim:
        # A failure, not a skip: every topology this lane runs against
        # has five hypervisors, so having nowhere to inject the fault
        # means the topology changed under us, and the assertions below
        # would otherwise read as a pass having checked nothing.
        fail('No hypervisor other than this host (%s) and the network node '
             '(%s) is active, so the fault has nowhere to go: %s'
             % (script_host, network_node,
                json.dumps(nodes, sort_keys=True, default=str)))
    log('Will fault %s on %s (%s)' % (FAULT_PATH, victim['name'], victim['ip']))

    created = []
    assert_controls(client, victim, created)

    try:
        log('=== Faulting %s on %s ===' % (FAULT_PATH, victim['name']))
        fault_storage(victim['name'])
        options = node_run(
            victim['name'], 'findmnt -n -o OPTIONS --target %s' % FAULT_PATH)
        if not mount_is_readonly(options):
            fail('%s on %s is not read-only after the bind remount; findmnt '
                 'reports %r' % (FAULT_PATH, victim['name'], options.strip()))
        log('OK: %s is mounted read-only' % FAULT_PATH)

        state = await_node_state(
            client, victim['name'], lambda s: s == 'error', DETECT_TIMEOUT,
            'reach the error state')
        log('OK: %s reached state %s within one check interval'
            % (victim['name'], state))

        assert_diagnosis(client, victim)
        await_gauge(victim, 0.0, DETECT_TIMEOUT)
        log('OK: node_resource_health on %s reads 0' % victim['name'])

        assert_absent_from_scheduler_view(client, victim, DETECT_TIMEOUT)
        assert_pin_refused(client, victim['name'], 'health-refused', created,
                           DETECT_TIMEOUT)

    finally:
        # The undo runs whatever happened above, because the exercises
        # which follow in this job share this cluster and a node left
        # errored with a read-only store would fail all of them for a
        # reason that named none of this. Nothing here may raise or exit:
        # on the failure path it must not replace the diagnosis above
        # with its own, and the assertions below are where a failed undo
        # is reported.
        log('=== Recovering %s ===' % victim['name'])
        clear_fault(victim['name'])
        # Recovery is an operator action by design: a resource health
        # error never clears on its own, however healthy the storage
        # becomes, so this command is the only way back.
        clear_node_error(victim['name'], check=False)
        for uuid in created:
            delete_quietly(client, uuid)

    state = await_node_state(
        client, victim['name'], lambda s: s in NODE_ACTIVE_STATES,
        RECOVER_TIMEOUT, 'return to an active state')
    log('OK: sf-ctl clear-node-error returned %s to state %s'
        % (victim['name'], state))

    await_gauge(victim, 1.0, RECOVER_TIMEOUT)
    log('OK: node_resource_health on %s is back to 1' % victim['name'])

    if not in_scheduler_view(client, victim):
        fail('%s is active again but is not a placement candidate, so '
             'clearing the error did not return it to service' % victim['name'])
    log('OK: %s is a placement candidate again' % victim['name'])
    assert_pin_lands(client, victim, 'health-recovered', created,
                     RECOVER_TIMEOUT)

    await_instances_gone(client, created)
    log('Node resource health assertions passed')


if __name__ == '__main__':
    main()
