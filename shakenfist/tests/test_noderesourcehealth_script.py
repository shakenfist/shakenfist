# Copyright 2026 Michael Still and contributors

"""The node resource health fault injection in shakenfist/deploy/.

``noderesourcehealthtests.py`` makes a node's storage really fail and
asserts the cluster takes it out of scheduling (issue 4960). It runs
once a week on the node lifecycle lane's own cluster, so a mistake in
its parsing or in its choice of victim would be discovered a long way
from the change that caused it -- and choosing the wrong victim is not a
failed assertion, it is this script halting the host it is running on or
stranding the network node's work queues, which is why
``nodelifecycletests.sh`` carries the same two carve-outs.

The decisions worth pinning are therefore the ones that are pure: which
node is eligible, whether a mount really came back read-only, what the
gauge reads, and what the recorded diagnosis says. Each is exercised
against the real function here. The script imports ``shakenfist_client``,
which is not a test dependency of this repository, so it is loaded by
path with that one import stubbed -- the same treatment
``test_ci_power_state.py`` gives ``shakenfist_ci/base.py``.

The constants both this script and
``cluster_ci_tests/test_node_health.py`` repeat from the server are also
checked against the server's own declarations, because a duplicated
constant which silently stops matching is how a check comes to measure
the wrong thing while still passing.
"""

import ast
import importlib.util
import os
import sys
import types

from shakenfist import blob
from shakenfist import instance
from shakenfist import resource_health
from shakenfist import upload
from shakenfist.config import SFConfig
from shakenfist.tests import base


DEPLOY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'deploy')
SCRIPT = os.path.join(DEPLOY, 'noderesourcehealthtests.py')
SHELL = os.path.join(DEPLOY, 'nodelifecycletests.sh')
CLUSTER_TEST = os.path.join(
    DEPLOY, 'shakenfist_ci', 'cluster_ci_tests', 'test_node_health.py')


def _load_script():
    """Import the deploy-time script without shakenfist_client installed.

    Everything mutated here is put back: stestr runs the whole unit
    suite in one process, and a stubbed shakenfist_client left in
    sys.modules would be a trap for a later test rather than a
    convenience for this one.
    """
    stub_names = ('shakenfist_client', 'shakenfist_client.apiclient',
                  'noderesourcehealthtests')
    saved = {name: sys.modules.get(name) for name in stub_names}

    try:
        client = types.ModuleType('shakenfist_client')
        apiclient = types.ModuleType('shakenfist_client.apiclient')
        apiclient.APIException = type('APIException', (Exception,), {})
        for name in ('InsufficientResourcesException',
                     'ResourceNotFoundException'):
            setattr(apiclient, name,
                    type(name, (apiclient.APIException,), {}))
        apiclient.ASYNC_PAUSE = 'pause'
        apiclient.Client = type(
            'Client', (), {'__init__': lambda self, *a, **kw: None})
        client.apiclient = apiclient
        sys.modules['shakenfist_client'] = client
        sys.modules['shakenfist_client.apiclient'] = apiclient

        spec = importlib.util.spec_from_file_location(
            'noderesourcehealthtests', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules['noderesourcehealthtests'] = module
        spec.loader.exec_module(module)
        return module

    finally:
        for name, saved_module in saved.items():
            if saved_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved_module


script = _load_script()


def _module_constants(path, names):
    """Read module level literal assignments without importing the module.

    The cluster suite imports shakenfist_client, so a test in this
    repository cannot import it; ``test_ci_raw_creates.py`` reads that
    tree with ``ast`` for the same reason.
    """
    with open(path) as f:
        tree = ast.parse(f.read())

    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(node.value)

    missing = sorted(set(names) - set(found))
    if missing:
        raise AssertionError(
            '%s no longer defines %s' % (path, ', '.join(missing)))
    return found


class SfNodeNameTestCase(base.ShakenFistTestCase):
    """The SF node name is read from /etc/sf/config, not from the hostname.

    The two diverged in this very environment and a guard which compared
    the kernel hostname silently never fired, which is how the lifecycle
    script came to halt its own host (see its own comment on this).
    """

    def test_plain_assignment(self):
        self.assertEqual(
            'sf2', script.sf_node_name('SHAKENFIST_NODE_NAME=sf2\n'))

    def test_quoted_assignment(self):
        self.assertEqual(
            'sf2', script.sf_node_name('SHAKENFIST_NODE_NAME="sf2"\n'))
        self.assertEqual(
            'sf2', script.sf_node_name("SHAKENFIST_NODE_NAME='sf2'\n"))

    def test_found_among_other_settings(self):
        body = ('# A comment\n'
                'SHAKENFIST_STORAGE_PATH=/srv/shakenfist\n'
                'SHAKENFIST_NODE_NAME=sf4\n'
                'MARIADB_PASSWORD=hunter2\n')
        self.assertEqual('sf4', script.sf_node_name(body))

    def test_a_commented_assignment_is_not_one(self):
        self.assertIsNone(script.sf_node_name(
            '#SHAKENFIST_NODE_NAME=sf2\n'))

    def test_an_empty_value_reads_as_absent(self):
        # Rather than as the empty string, which would then be compared
        # against every node name and match none of them, quietly making
        # this host eligible as its own victim.
        self.assertIsNone(script.sf_node_name('SHAKENFIST_NODE_NAME=\n'))

    def test_absent_assignment(self):
        self.assertIsNone(script.sf_node_name('OTHER=1\n'))

    def test_last_assignment_wins(self):
        # Which is what sourcing the file would do.
        self.assertEqual('sf5', script.sf_node_name(
            'SHAKENFIST_NODE_NAME=sf4\nSHAKENFIST_NODE_NAME=sf5\n'))


class ChooseVictimTestCase(base.ShakenFistTestCase):
    """Who may be faulted: a third-party active hypervisor, or nobody."""

    def _nodes(self):
        return [
            {'name': 'primary', 'is_hypervisor': False, 'state': 'created'},
            {'name': 'sf1', 'is_hypervisor': True, 'state': 'created',
             'is_network_node': True},
            {'name': 'sf2', 'is_hypervisor': True, 'state': 'created'},
            {'name': 'sf3', 'is_hypervisor': True, 'state': 'created'},
        ]

    def test_picks_an_eligible_hypervisor(self):
        victim = script.choose_victim(self._nodes(), 'primary', 'sf1')
        self.assertEqual('sf2', victim['name'])

    def test_never_the_script_host(self):
        # Erroring the node the job drives from would take the API the
        # assertions talk to out of the set they assert about.
        nodes = [n for n in self._nodes() if n['name'] in ('primary', 'sf2')]
        nodes[0]['is_hypervisor'] = True
        self.assertEqual(
            'sf2', script.choose_victim(nodes, 'primary', 'sf1')['name'])
        self.assertIsNone(script.choose_victim(nodes[:1], 'primary', 'sf1'))

    def test_never_the_network_node(self):
        nodes = [n for n in self._nodes() if n['name'] == 'sf1']
        self.assertIsNone(script.choose_victim(nodes, 'primary', 'sf1'))

    def test_never_a_non_hypervisor(self):
        nodes = [n for n in self._nodes() if n['name'] == 'primary']
        self.assertIsNone(script.choose_victim(nodes, 'other', 'sf1'))

    def test_never_an_inactive_node(self):
        # A node which is already missing, stopped or errored has nothing
        # to prove and cannot be recovered by clearing a health error.
        nodes = self._nodes()
        for node in nodes:
            if node['name'] in ('sf2', 'sf3'):
                node['state'] = 'missing'
        self.assertIsNone(script.choose_victim(nodes, 'primary', 'sf1'))

    def test_no_network_node_reported(self):
        # network_node resolves to None when no node claims the role; that
        # must not accidentally exclude a node whose name is missing.
        nodes = [{'is_hypervisor': True, 'state': 'created'}]
        self.assertIsNone(script.choose_victim(nodes, 'primary', None))

    def test_returns_none_on_an_empty_roster(self):
        self.assertIsNone(script.choose_victim([], 'primary', 'sf1'))


class MountIsReadonlyTestCase(base.ShakenFistTestCase):
    """findmnt's option list is matched whole, never as a substring.

    'relatime', 'rw' and 'errors=remount-ro' all contain the letters
    'ro', so a substring test would report every ordinary mount as
    read-only -- and the fault would read as injected when it was not,
    which is the one failure this check cannot be allowed to have.
    """

    def test_read_only_mount(self):
        self.assertTrue(script.mount_is_readonly('ro,relatime\n'))

    def test_read_only_not_first(self):
        self.assertTrue(script.mount_is_readonly('rw,ro,relatime\n'))

    def test_writable_mount(self):
        self.assertFalse(script.mount_is_readonly('rw,relatime\n'))

    def test_relatime_alone_is_not_read_only(self):
        self.assertFalse(script.mount_is_readonly('relatime\n'))

    def test_errors_remount_ro_is_not_read_only(self):
        self.assertFalse(
            script.mount_is_readonly('rw,relatime,errors=remount-ro\n'))

    def test_no_output(self):
        # findmnt prints nothing and exits non-zero for a path it cannot
        # resolve, which must not read as read-only.
        self.assertFalse(script.mount_is_readonly(''))
        self.assertFalse(script.mount_is_readonly('\n\n'))


class ParseGaugeTestCase(base.ShakenFistTestCase):
    """Only an exact, unlabelled sample of this gauge is its value."""

    EXPOSITION = (
        '# HELP node_resource_health 1 if all checks pass\n'
        '# TYPE node_resource_health gauge\n'
        'node_resource_health 0.0\n'
        'node_resource_health_extra 1.0\n'
        'instances_active 3.0\n')

    def test_reads_the_gauge(self):
        self.assertEqual(
            0.0, script.parse_gauge(self.EXPOSITION, 'node_resource_health'))

    def test_absent_gauge_is_none_not_zero(self):
        # Zero means "this node says its storage is broken", absent means
        # "the health thread never started". The script reports them
        # differently, so they must not collapse here.
        self.assertIsNone(script.parse_gauge(
            'instances_active 3.0\n', 'node_resource_health'))

    def test_a_prefix_match_is_a_different_metric(self):
        self.assertEqual(
            1.0, script.parse_gauge(
                self.EXPOSITION, 'node_resource_health_extra'))

    def test_help_and_type_lines_are_not_samples(self):
        self.assertIsNone(script.parse_gauge(
            '# HELP node_resource_health 1 if all checks pass\n'
            '# TYPE node_resource_health gauge\n', 'node_resource_health'))

    def test_a_trailing_timestamp_is_not_the_value(self):
        self.assertEqual(1.0, script.parse_gauge(
            'node_resource_health 1.0 1700000000000\n',
            'node_resource_health'))


class HealthDiagnosisTestCase(base.ShakenFistTestCase):
    """The diagnosis is the newest health event carrying affected_types."""

    def _health(self, types, failed=None, message='failed'):
        return {'type': 'health', 'message': message,
                'extra': {'affected_types': types,
                          'failed': failed or []}}

    def test_finds_the_newest_health_event(self):
        events = [self._health(['upload'], message='newest'),
                  self._health(['blob'], message='older')]
        self.assertEqual('newest', script.health_diagnosis(events)['message'])

    def test_ignores_other_event_types(self):
        events = [{'type': 'audit', 'extra': {'affected_types': ['blob']}},
                  self._health(['upload'])]
        self.assertEqual(
            ['upload'],
            script.health_diagnosis(events)['extra']['affected_types'])

    def test_a_health_event_without_a_payload_is_skipped(self):
        # apply_result() is not the only writer on the health channel, so
        # a health row with no blast radius in it is not the diagnosis.
        events = [{'type': 'health', 'message': 'something else'},
                  {'type': 'health', 'extra': 'not a dict'},
                  self._health(['upload'])]
        self.assertEqual(
            ['upload'],
            script.health_diagnosis(events)['extra']['affected_types'])

    def test_none_when_there_is_no_diagnosis(self):
        self.assertIsNone(script.health_diagnosis([]))
        self.assertIsNone(script.health_diagnosis(None))
        self.assertIsNone(script.health_diagnosis(
            [{'type': 'audit', 'extra': {}}]))


class DuplicatedConstantsTestCase(base.ShakenFistTestCase):
    """What the CI tree repeats from the server still matches the server.

    Both files run against a remote cluster from a venv with no server
    tree in it, so each writes these down rather than importing them.
    That is the established convention here, and its failure mode is
    silent: a constant which stops matching leaves a check measuring
    something nobody asked about while still passing. This is where it
    stops being silent.
    """

    @staticmethod
    def _default(name):
        """The declared default of a config field, not its resolved value.

        What the CI tree hardcodes is the shipped default -- "which the
        deployer does not move" -- so an environment variable set where
        these tests happen to run must not be able to fail them, nor to
        make them pass against a value the deployed cluster will not
        have.
        """
        return SFConfig.model_fields[name].default

    def test_script_repeats_the_servers_values(self):
        self.assertEqual(self._default('STORAGE_PATH'), script.STORAGE_PATH)
        self.assertEqual(
            resource_health.HEARTBEAT_FILENAME, script.HEARTBEAT_FILENAME)
        self.assertEqual(
            resource_health.HealthStatus.READONLY, script.READONLY_STATUS)
        self.assertEqual(
            self._default('RESOURCES_METRICS_PORT'),
            script.RESOURCES_METRICS_PORT)

    def test_the_faulted_path_is_declared_by_upload_alone(self):
        # The whole reason this fault is safe to inject on a cluster the
        # rest of the job still needs: uploads is declared by Upload and
        # by nothing else, so the cascade errors no instance and drops no
        # blob replica. A dependency list which grows to include it makes
        # the fault invasive without the script noticing.
        self.assertIn(script.FAULT_SUBDIR, upload.Upload.health_dependencies)
        for declaring in (blob.Blob, instance.Instance):
            self.assertNotIn(
                script.FAULT_SUBDIR, declaring.health_dependencies,
                '%s now depends on %s, so faulting it is no longer the '
                'narrowest fault that proves the point'
                % (declaring.__name__, script.FAULT_SUBDIR))

    def test_cluster_test_expects_the_declared_dependencies(self):
        # node_health.node_object_types() gives Blob and Upload to every
        # node and Instance to hypervisors only; the cluster suite's two
        # path tuples have to be exactly that, or a node probing fewer
        # paths than it declares passes a check written to catch it.
        found = _module_constants(
            CLUSTER_TEST,
            ('EVERY_NODE_PATHS', 'HYPERVISOR_PATHS', 'STORAGE_PATH',
             'HEARTBEAT_FILENAME'))

        self.assertEqual(
            sorted(set(blob.Blob.health_dependencies +
                       upload.Upload.health_dependencies)),
            sorted(set(found['EVERY_NODE_PATHS'])))
        self.assertEqual(
            sorted(set(instance.Instance.health_dependencies)),
            sorted(set(found['HYPERVISOR_PATHS'])))
        self.assertEqual(
            self._default('STORAGE_PATH'), found['STORAGE_PATH'])
        self.assertEqual(
            resource_health.HEARTBEAT_FILENAME, found['HEARTBEAT_FILENAME'])


class ShellInvocationTestCase(base.ShakenFistTestCase):
    """The lifecycle script actually runs the assertions.

    A deploy-time assertion script which nothing invokes reads as
    coverage while proving nothing, and the only place that would be
    noticed is a weekly lane nobody watches. Checked the same way
    test_ci_cluster_nodes_seams.py checks its callers: by reading the
    file that is supposed to call it.
    """

    def test_the_script_is_invoked_and_its_failure_is_fatal(self):
        with open(SHELL) as f:
            body = f.read()

        self.assertIn('python3 noderesourcehealthtests.py', body)
        self.assertIn(
            'if ! python3 noderesourcehealthtests.py; then', body,
            'The lifecycle script must stop on a failed node health '
            'assertion; an unchecked invocation would let it pass.')

    def test_it_runs_before_the_lane_creates_instances(self):
        # The fault errors a hypervisor. Running it while the lane's own
        # instances are placed would error instances this job then
        # asserts the state of, and the failure would name none of this.
        with open(SHELL) as f:
            body = f.read()

        invocation = body.index('python3 noderesourcehealthtests.py')
        creates = body.index('sf-client instance create')
        self.assertLess(
            invocation, creates,
            'The node health fault must be injected and recovered before '
            'the lifecycle lane places any instances.')
