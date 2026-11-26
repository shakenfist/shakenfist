# Copyright 2019 Michael Still and contributors
"""The libvirt-versus-ledger comparison, checked without a cluster.

``cluster_ci_tests/test_nodes.py``'s
``test_node_accounting_agrees_with_libvirt`` compares each hypervisor's
published ``instances_active``, ``cpu_measured`` and ``ram_measured``
with what ``virsh`` reports for the same domains. Cluster CI only runs in
the merge queue, so nothing in that file is checkable on a
``pull_request`` -- and a comparison which is itself wrong is the worst
kind to discover there, because it fails jobs on clusters which are fine.

The parsing and the arithmetic therefore live in
``shakenfist_ci/accounting.py``, a module deliberately free of suite and
``shakenfist_client`` imports so this file can load it by path, exactly
as ``test_ci_saturation.py`` and ``test_ci_structural_minimum.py``
already do.

The sample fixtures below are ``virsh dominfo`` output as libvirt
actually prints it, including the fields the parser has to step over:
``Max memory`` beside ``Used memory``, and ``CPU time`` beside
``CPU(s)``.
"""

import importlib.util
import os

from shakenfist.tests import base


ACCOUNTING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci', 'accounting.py')


def _load_accounting():
    spec = importlib.util.spec_from_file_location(
        'shakenfist_ci_accounting_under_test', ACCOUNTING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


accounting = _load_accounting()


def _dominfo(name, vcpus, memory_kib, state='running'):
    """One domain's stanza, as the sample command emits it."""
    return (
        '%s%s\n'
        'Id:             7\n'
        'Name:           %s\n'
        'UUID:           4a3f3f4e-0b0b-4f2a-9f4e-1a2b3c4d5e6f\n'
        'OS Type:        hvm\n'
        'State:          %s\n'
        'CPU(s):         %d\n'
        'CPU time:       123.4s\n'
        'Max memory:     %d KiB\n'
        'Used memory:    %d KiB\n'
        'Persistent:     yes\n'
        'Autostart:      disable\n'
        'Managed save:   no\n'
        'Security model: apparmor\n'
        'Security DOI:   0\n'
        % (accounting.DOMAIN_MARKER, name, name, state, vcpus, memory_kib,
           memory_kib))


def _sample(stanzas, sampled_at='1792886390.123456789'):
    return '%s\n%s' % (sampled_at, ''.join(stanzas))


ONE = '11111111-1111-4111-8111-111111111111'
TWO = '22222222-2222-4222-8222-222222222222'


class ParseDomainSampleTestCase(base.ShakenFistTestCase):
    def test_a_sample_of_two_domains_is_read_in_full(self):
        parsed = accounting.parse_domain_sample(_sample([
            _dominfo('sf:%s' % ONE, 2, 262144),
            _dominfo('sf:%s' % TWO, 1, 131072, state='paused')]))

        self.assertEqual(1792886390.123456789, parsed['sampled_at'])
        self.assertEqual(
            {ONE: {'vcpus': 2, 'memory_kib': 262144},
             TWO: {'vcpus': 1, 'memory_kib': 131072}},
            parsed['domains'])
        self.assertEqual([], parsed['unreadable'])

    def test_a_node_with_no_domains_reads_as_empty_not_unknown(self):
        # An idle hypervisor is a comparison which must still happen: a
        # node measuring instances it is not running is precisely the
        # drift this is for.
        parsed = accounting.parse_domain_sample(_sample([]))
        self.assertEqual({}, parsed['domains'])
        self.assertEqual([], parsed['unreadable'])
        self.assertEqual(
            {'instances_active': 0, 'cpu_measured': 0, 'ram_measured': 0},
            accounting.measured_from_domains(parsed['domains']))

    def test_a_foreign_domain_is_not_counted(self):
        # The measurement this is compared with filters on the "sf:"
        # prefix, so a node running something else must not read as
        # drifted because of it.
        parsed = accounting.parse_domain_sample(_sample([
            _dominfo('not-ours', 8, 8388608),
            _dominfo('sf:%s' % ONE, 1, 131072)]))

        self.assertEqual({ONE: {'vcpus': 1, 'memory_kib': 131072}},
                         parsed['domains'])
        self.assertEqual([], parsed['unreadable'])

    def test_a_domain_which_stopped_mid_sample_is_unreadable(self):
        # virsh dominfo writes its failure to stderr and prints no
        # figures, leaving the stanza empty. Counting that domain as zero
        # vCPUs would make the sample disagree with a correct
        # measurement, so it is reported instead.
        parsed = accounting.parse_domain_sample(_sample([
            '%ssf:%s\n' % (accounting.DOMAIN_MARKER, ONE),
            _dominfo('sf:%s' % TWO, 1, 131072)]))

        self.assertEqual({TWO: {'vcpus': 1, 'memory_kib': 131072}},
                         parsed['domains'])
        self.assertEqual([ONE], parsed['unreadable'])

    def test_a_domain_missing_one_figure_is_unreadable_not_half_counted(self):
        stanza = _dominfo('sf:%s' % ONE, 1, 131072).replace(
            'Used memory:    131072 KiB\n', '')
        parsed = accounting.parse_domain_sample(_sample([stanza]))

        self.assertEqual({}, parsed['domains'])
        self.assertEqual([ONE], parsed['unreadable'])

    def test_memory_libvirt_declines_to_report_is_unreadable(self):
        # virsh prints "no limit" rather than a figure when the domain
        # reports no memory in use, which an active domain does not do.
        # Reading that as zero would understate the sample.
        stanza = _dominfo('sf:%s' % ONE, 1, 131072).replace(
            'Used memory:    131072 KiB', 'Used memory:    no limit')
        parsed = accounting.parse_domain_sample(_sample([stanza]))

        self.assertEqual({}, parsed['domains'])
        self.assertEqual([ONE], parsed['unreadable'])

    def test_a_sample_with_no_node_clock_is_rejected(self):
        # No timestamp means the command did not run as written, so
        # nothing in the output can be trusted -- including an empty
        # domain list, which would otherwise read as an idle node.
        self.assertRaises(
            ValueError, accounting.parse_domain_sample,
            'sudo: a password is required\n')
        self.assertRaises(ValueError, accounting.parse_domain_sample, '')


class MeasuredFromDomainsTestCase(base.ShakenFistTestCase):
    def test_memory_is_summed_in_kib_and_converted_once(self):
        # sf-resources sums domain.info()[2] in KiB and divides the sum
        # by 1024 after the loop, so flooring each domain first would
        # lose a megabyte per domain. Two domains of 1536 KiB are 3 MB,
        # not 2.
        self.assertEqual(
            3,
            accounting.measured_from_domains({
                ONE: {'vcpus': 1, 'memory_kib': 1536},
                TWO: {'vcpus': 1, 'memory_kib': 1536}})['ram_measured'])

    def test_the_three_figures_come_from_the_one_domain_set(self):
        measured = accounting.measured_from_domains({
            ONE: {'vcpus': 2, 'memory_kib': 262144},
            TWO: {'vcpus': 1, 'memory_kib': 131072}})
        self.assertEqual(
            {'instances_active': 2, 'cpu_measured': 3,
             'ram_measured': 384}, measured)
        self.assertEqual(set(accounting.MEASURED_FIGURES), set(measured))


class AccountingMismatchesTestCase(base.ShakenFistTestCase):
    DOMAINS = {
        ONE: {'vcpus': 2, 'memory_kib': 262144},
        TWO: {'vcpus': 1, 'memory_kib': 131072},
    }

    def _entry(self, **overrides):
        """A per_node entry which agrees with DOMAINS, less any override."""
        entry = {
            'instances_active': 2,
            'instances_total': 2,
            'cpu_measured': 3,
            'ram_measured': 384,
            # Figures the comparison must ignore: these are ledger
            # entries, not measurements of the hypervisor.
            'cpu_committed': 17,
            'ram_committed': 4096,
            'cpu_available': 11.0,
        }
        entry.update(overrides)
        return entry

    def test_an_agreeing_node_reports_nothing(self):
        self.assertEqual(
            [], accounting.accounting_mismatches(self.DOMAINS, self._entry()))

    def test_an_integer_figure_published_as_a_float_still_agrees(self):
        # The metrics row's columns are numeric, and a node publishing
        # 3.0 vCPUs measured is publishing three.
        self.assertEqual(
            [],
            accounting.accounting_mismatches(
                self.DOMAINS, self._entry(cpu_measured=3.0,
                                          ram_measured=384.0)))

    def test_each_measured_figure_is_compared(self):
        for figure, wrong in (('instances_active', 1), ('cpu_measured', 4),
                              ('ram_measured', 256)):
            mismatches = accounting.accounting_mismatches(
                self.DOMAINS, self._entry(**{figure: wrong}))
            self.assertEqual(
                [{'figure': figure, 'libvirt': self._entry()[figure],
                  'published': wrong}],
                mismatches)

    def test_every_disagreement_is_reported_not_just_the_first(self):
        # A node which has lost a whole domain from its measurement is
        # wrong in all three, and a reader deciding whether this is drift
        # or a stale publish wants to see that it is all three.
        mismatches = accounting.accounting_mismatches(
            self.DOMAINS,
            self._entry(instances_active=1, cpu_measured=1, ram_measured=128))
        self.assertEqual(
            list(accounting.MEASURED_FIGURES),
            [m['figure'] for m in mismatches])

    def test_a_figure_the_node_does_not_publish_is_a_disagreement(self):
        # Not a skip: a node which has stopped publishing one of these is
        # a node whose accounting cannot be checked at all, which is the
        # thing this exists to notice.
        entry = self._entry()
        del entry['ram_measured']
        self.assertEqual(
            [{'figure': 'ram_measured', 'libvirt': 384, 'published': None}],
            accounting.accounting_mismatches(self.DOMAINS, entry))

    def test_an_idle_node_measuring_an_instance_disagrees(self):
        # The drift that refuses creates while the cluster has room
        # (issue 3772): nothing is running, but the node is charged for
        # something.
        self.assertEqual(
            [{'figure': 'instances_active', 'libvirt': 0, 'published': 1},
             {'figure': 'cpu_measured', 'libvirt': 0, 'published': 2},
             {'figure': 'ram_measured', 'libvirt': 0, 'published': 256}],
            accounting.accounting_mismatches(
                {}, self._entry(instances_active=1, cpu_measured=2,
                                ram_measured=256)))
