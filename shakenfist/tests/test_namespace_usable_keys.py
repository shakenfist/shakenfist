# Copyright 2026 Michael Still and contributors

"""The health signal for a namespace with no key which can authenticate.

Nothing in the write path can refuse this condition: an operator may
delete the last key by hand, and the expiry sweep soft deletes the last
key if nothing replaced it. For the system namespace the result is that
every deploy, every sf-ctl call and every piece of cluster automation
loses its credential at the same moment, which in #3836 was discovered
as a 66 minute outage rather than as an alert (#5015).

So what is tested here is the three layers of the signal: the count
itself, the gauge the elected cluster daemon publishes from it, and the
shipped alert which reads that gauge. The one property they all turn on
is that "we could not tell" is never reported as "nobody can
authenticate" -- an alert which cries wolf on a database blip is an
alert somebody switches off, which is how the cluster ends up back
where it started.
"""

import os
import shutil
import subprocess
import sys
from unittest import mock

import yaml
from click.testing import CliRunner
from prometheus_client import REGISTRY

from shakenfist import namespace
from shakenfist.daemons.cluster import scheduled_tasks as st
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.schema.namespace_key_attributes import NamespaceKeyAttributesData
from shakenfist.schema.object_types import ObjectType
from shakenfist.namespace_key import NamespaceKey
from shakenfist.tests import base


ALERTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))),
    'examples', 'prometheus-namespace-key-alert.yaml')


KEY_UUIDS = ['%d%d%d%d%d%d%d%d-1111-4111-8111-111111111111' % ((i,) * 8)
             for i in range(1, 10)]


class FakeKey:
    """Just enough of a NamespaceKey for the count to run over it."""

    def __init__(self, uuid, name='k'):
        self.uuid = uuid
        self.name = name


def _attrs(uuid, expiry=None):
    return NamespaceKeyAttributesData(
        uuid=uuid, key='aGFzaA==', nonce='noncenonce', expiry=expiry)


def _pair(index, expiry=None):
    """One (key, attributes) pair as the joined accessor returns it."""
    uuid = KEY_UUIDS[index]
    return (FakeKey(uuid), _attrs(uuid, expiry=expiry))


class UsableKeyCountsTestCase(base.ShakenFistTestCase):
    """namespace.usable_key_counts(), the count behind both surfaces."""

    def _counts(self, keys, active='derive', now=1000.0):
        """Count over ``keys``, a dict of namespace name to key pairs.

        ``active`` is what the bulk object_states query answers: by
        default every key listed, so a test which says nothing about
        state gets keys the cluster considers usable.
        """
        if active == 'derive':
            active = [str(key.uuid)
                      for pairs in keys.values() for key, _ in pairs]

        namespaces = [mock.MagicMock(uuid=name) for name in keys]
        self.listed = []

        def _keys_with_attributes(ns, include_expired=False, now=None):
            self.listed.append((ns, include_expired, now))
            return keys[ns]

        with mock.patch('shakenfist.namespace.Namespaces',
                        return_value=namespaces), \
            mock.patch('shakenfist.namespace.mariadb.get_objects_by_state',
                       return_value=active) as self.state_query, \
            mock.patch(
                'shakenfist.namespace.namespace_key.keys_with_attributes',
                side_effect=_keys_with_attributes):
            return namespace.usable_key_counts(now=now)

    def test_counts_a_key_which_satisfies_every_term(self):
        counts = self._counts({'system': [_pair(0)]})

        self.assertEqual({'system': 1}, counts)

    def test_a_namespace_with_no_keys_counts_zero(self):
        # Published rather than omitted, because an alert on this gauge
        # can only rely on a series which exists.
        counts = self._counts({'system': [_pair(0)], 'banana': []})

        self.assertEqual({'system': 1, 'banana': 0}, counts)

    def test_a_key_in_no_active_state_does_not_count(self):
        # A key the expiry sweep has soft deleted, or a stateless zombie
        # with no object_states row at all. The read paths honour a
        # zombie today, but it is not a key an operator should be told
        # they can rely on -- the orphan reconciler repairs it, and if
        # it is also expired the sweep then deletes it.
        counts = self._counts({'system': [_pair(0)]}, active=[])

        self.assertEqual({'system': 0}, counts)

    def test_the_state_query_is_asked_once_for_the_whole_cluster(self):
        # One bulk query, never a point read per key: reading .state per
        # key is what made the expiry sweep expensive enough to matter
        # (issue 3814), and this walks the same keys.
        self._counts({'a': [_pair(0), _pair(1)],
                      'b': [_pair(2)]})

        self.assertEqual(1, self.state_query.call_count)
        self.assertEqual(
            (ObjectType.NAMESPACE_KEY, sorted(NamespaceKey.ACTIVE_STATES)),
            self.state_query.call_args[0])

    def test_expiry_is_filtered_by_the_accessor_at_our_clock(self):
        # The third term is the expiry filter inside
        # keys_with_attributes(), which is pushed into SQL. Counting
        # expired keys in Python as well would be a second clock which
        # can disagree with the one the authentication path uses, so the
        # test is that the caller's "now" reaches the accessor and that
        # expired keys are not asked for.
        self._counts({'system': [_pair(0)]}, now=4321.0)

        self.assertEqual([('system', False, 4321.0)], self.listed)

    def test_an_unreadable_state_listing_is_not_an_empty_one(self):
        # The property the whole signal rests on. None means the query
        # failed, and reporting that as "no namespace has a usable key"
        # would fire the system alert during any database blip.
        counts = self._counts({'system': [_pair(0)]}, active=None)

        self.assertIsNone(counts)


class PublishNamespaceKeyHealthTestCase(base.ShakenFistTestCase):
    """The gauge the elected cluster daemon publishes from that count."""

    def setUp(self):
        super().setUp()
        st.clear_namespace_key_health_metrics()
        self.addCleanup(st.clear_namespace_key_health_metrics)

    def _gauge(self, ns):
        return REGISTRY.get_sample_value(
            'namespace_usable_keys', {'namespace': ns})

    def _publish(self, counts):
        with mock.patch(
                'shakenfist.daemons.cluster.scheduled_tasks.'
                'usable_key_counts', return_value=counts), \
                mock.patch.object(st.LOG, 'warning') as mock_warning:
            st.publish_namespace_key_health()
        return mock_warning

    def test_publishes_a_series_per_namespace_including_zero(self):
        self._publish({'system': 2, 'banana': 0})

        self.assertEqual(2, self._gauge('system'))
        self.assertEqual(0, self._gauge('banana'))

    def test_warns_when_the_system_namespace_is_locked_out(self):
        # The log line is not decoration: an operator who is locked out
        # of the API is reading logs, and this is the sentence which
        # tells them what happened and how to recover.
        mock_warning = self._publish({'system': 0, 'banana': 3})

        self.assertEqual(1, mock_warning.call_count)
        message = str(mock_warning.call_args[0][0])
        self.assertIn('system namespace has no key', message)
        self.assertIn('bootstrap-system-key', message)

    def test_a_namespace_with_keys_and_an_empty_one_do_not_warn(self):
        # A namespace with no keys at all is unremarkable -- a federated
        # one mints them per login -- so only system is worth a line, or
        # the warning is one nobody reads.
        mock_warning = self._publish({'system': 1, 'banana': 0})

        mock_warning.assert_not_called()

    def test_a_missing_system_namespace_warns_too(self):
        # Absent counts the same as zero here: either way nothing in
        # system can authenticate.
        mock_warning = self._publish({'banana': 1})

        self.assertEqual(1, mock_warning.call_count)
        self.assertIsNone(self._gauge('system'))

    def test_a_deleted_namespace_loses_its_series(self):
        self._publish({'system': 1, 'banana': 1})
        self._publish({'system': 1})

        self.assertEqual(1, self._gauge('system'))
        self.assertIsNone(self._gauge('banana'))

    def test_a_failed_count_drops_the_series_rather_than_zeroing_it(self):
        # Zero is the condition the alert fires on, so a failed count
        # must not publish one. Dropping the series instead is what
        # publish_deleted_object_backlog() does for the same reason, and
        # the shipped rules alert on the absence.
        self._publish({'system': 1})
        mock_warning = self._publish(None)

        self.assertIsNone(self._gauge('system'))
        self.assertIn('dropping the usable namespace key gauge',
                      str(mock_warning.call_args[0][0]))

    def test_demotion_stops_this_node_answering(self):
        # Leader-only work, so a demoted node which kept exporting its
        # last reading would contradict whichever node took over.
        self._publish({'system': 1})

        st.clear_namespace_key_health_metrics()

        self.assertIsNone(self._gauge('system'))
        self.assertEqual(set(), st._NAMESPACE_KEY_HEALTH_EXPORTED)


class NamespaceKeyHealthCommandTestCase(base.ShakenFistTestCase):
    """sf-ctl namespace-key-health, for an operator who cannot use the API.

    The condition takes the API away, so the recovery path cannot go
    through it. This command reads the database directly, which is why
    it exists in addition to the gauge.
    """

    @classmethod
    def setUpClass(cls):
        cls.verify_config_patcher = mock.patch(
            'shakenfist.config.verify_config', mock.MagicMock())
        cls.verify_config_patcher.start()
        if 'shakenfist.client.ctl' in sys.modules:
            del sys.modules['shakenfist.client.ctl']

    @classmethod
    def tearDownClass(cls):
        cls.verify_config_patcher.stop()

    def setUp(self):
        super().setUp()
        self.runner = CliRunner()

    def _run(self, counts):
        from shakenfist.client import ctl

        with mock.patch.object(ctl, 'usable_key_counts',
                               return_value=counts):
            return self.runner.invoke(ctl.namespace_key_health)

    def test_reports_every_namespace_and_exits_zero(self):
        result = self._run({'system': 2, 'banana': 0})

        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(['banana: 0', 'system: 2'],
                         result.output.strip().splitlines())

    def test_exits_non_zero_when_system_has_no_usable_key(self):
        result = self._run({'system': 0})

        self.assertEqual(1, result.exit_code)
        self.assertIn('bootstrap-system-key', result.output)

    def test_a_failed_read_is_an_error_not_a_clean_bill_of_health(self):
        result = self._run(None)

        self.assertEqual(1, result.exit_code)
        self.assertIn('could not list active namespace keys', result.output)

    def test_an_unreachable_tier_is_reported_rather_than_traced(self):
        from shakenfist.client import ctl

        with mock.patch.object(
                ctl, 'usable_key_counts',
                side_effect=DatabaseUnavailable('tier is down')):
            result = self.runner.invoke(ctl.namespace_key_health)

        self.assertEqual(1, result.exit_code)
        self.assertIn('database tier is unavailable', result.output)


class NamespaceKeyAlertRulesTestCase(base.ShakenFistTestCase):
    """The shipped alert must name the series the daemon publishes."""

    def _rules(self):
        with open(ALERTS_PATH) as f:
            loaded = yaml.safe_load(f)
        return [r for g in loaded['groups'] for r in g['rules']]

    def test_the_alerts_read_the_gauge_the_code_publishes(self):
        # The drift which would matter: rename the gauge, and the rules
        # load without error and never fire, which looks exactly like a
        # cluster with nothing wrong.
        metric = st.NAMESPACE_USABLE_KEYS._name
        alerts = {r['alert']: r['expr'] for r in self._rules()}
        self.assertEqual(2, len(alerts))
        for expr in alerts.values():
            self.assertIn(metric, expr)
            self.assertIn('namespace="system"', expr)

        # One alert on the condition, one on not being able to see it.
        absent = [name for name, expr in alerts.items()
                  if expr.startswith('absent(')]
        self.assertEqual(1, len(absent))

    def test_the_promql_parses(self):
        # Read as data above, which a mismatched paren survives. A rule
        # group an operator's Prometheus refuses to load reports nothing
        # at all.
        promtool = shutil.which('promtool')
        if not promtool:
            self.skipTest(
                'promtool is not on PATH, so the PromQL in %s has been '
                'checked for shape but not parsed. Install it with '
                'tools/ci-install-promtool.sh.' % ALERTS_PATH)

        result = subprocess.run(
            [promtool, 'check', 'rules', ALERTS_PATH],
            capture_output=True, text=True)
        self.assertEqual(
            0, result.returncode,
            'promtool rejected %s:\n%s\n%s'
            % (ALERTS_PATH, result.stdout, result.stderr))
