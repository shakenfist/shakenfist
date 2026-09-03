import base64
import copy
import datetime
import json
import logging
import os
import random
import re
import shlex
import string
import sys
import time

import testtools
from oslo_concurrency import processutils
from prettytable import PrettyTable
from shakenfist_client import apiclient


logging.basicConfig(level=logging.INFO, format='%(message)s')
LOG = logging.getLogger()
TRACE_PATH = '/srv/ci/traces'


CLUSTER_CI_IMAGE = 'sf://upload/system/debian-12'


# An agent operation which ends in a terminal state other than "complete"
# reaches the caller as one of these. Historically the client had no way to
# say "this operation definitively failed": every await loop spun out its
# whole budget and then raised AgentCommandError, so that is what the suite
# caught and asserted. client-python#380 (agent operation deadlines phase 6)
# teaches those loops to recognise a terminal state on the first poll and
# raise the new, more precise AgentOperationFailed instead.
#
# Both client versions are in circulation while that lands: this repository's
# CI builds the client from client-python's develop, and client-python's CI
# builds the server from this repository's develop, so neither side can
# switch first. Accepting either exception is what breaks that deadlock. The
# getattr() is what makes an old client work -- AgentOperationFailed simply
# does not exist there -- and can be narrowed to a plain reference once the
# client change has merged. Phase 7 owns that cleanup, along with the rest of
# this suite's await work.
AGENT_OPERATION_FAILURES = (
    apiclient.AgentCommandError,
    getattr(apiclient, 'AgentOperationFailed', apiclient.AgentCommandError))


# Some functional assertions need to observe real host state (network
# namespaces, links, iptables, libvirt) on a *specific* cluster node, which is
# not necessarily the node running the suite. The node-exec helpers on
# BaseTestCase reach those nodes over the management mesh as this user with
# this key. See docs/plans/PLAN-ci-node-exec-assertions.md.
SF_CI_SSH_USER = os.environ.get('SF_CI_SSH_USER', 'debian')
SF_CI_SSH_KEY = os.environ.get(
    'SF_CI_SSH_KEY', os.path.expanduser('~/.ssh/id_rsa'))


# The delete endpoint refuses a namespace which still owns a live
# instance or network with one of "you cannot delete a namespace with
# instances" or "...with networks". Both are 400s, and so is every other
# way a request can be malformed, so the prefix is what distinguishes a
# refusal which will clear from one which will not.
NAMESPACE_NOT_EMPTY = 'you cannot delete a namespace with'


class TimeoutException(Exception):
    pass


class StartException(Exception):
    pass


class WrongEventException(Exception):
    pass


class RetryException(Exception):
    pass


def namespace_names(namespaces):
    """The name of each namespace in a get_namespaces() result.

    get_namespaces() returns a list of external_view() dicts, not a list
    of names, so asking whether a name is ``in`` its result is always
    false. That mistake had been made independently in four places, one
    of which meant no namespaced functional test deleted its namespace
    between 2020 and 2026. Everything asking the question goes through
    here now, so there is one place to be wrong.
    """
    return [ns['name'] for ns in namespaces]


def load_userdata(suite, name):
    test_dir = os.path.dirname(os.path.abspath(__file__))
    with open(f'{test_dir}/{suite}/files/{name}_userdata') as f:
        return base64.b64encode(f.read().encode('utf-8')).decode('utf-8')


class BaseTestCase(testtools.TestCase):
    def setUp(self):
        super().setUp()

        self.system_client = apiclient.Client(async_strategy=apiclient.ASYNC_PAUSE)
        self._emit_tracing_event({
            'msg': 'Test starts'
        })

    def tearDown(self):
        super().tearDown()
        self._emit_tracing_event({
            'msg': 'Test ends'
        })

    def _make_namespace(self, name, key):
        # This deliberately does not delete an existing namespace of the
        # same name first, though it used to appear to. Namespace
        # deletion is a soft delete and the create endpoint refuses any
        # name whose row still exists whatever its state, so
        # delete-then-create would answer 403 "namespace exists" -- which
        # reads as though the delete had failed. Removing the call changes
        # nothing in practice: it could never fire, because the name it
        # was given was always compared against dicts. Every caller
        # uniquifies, so a collision here is something the author of the
        # test needs to be shown rather than have tidied away.
        self.system_client.create_namespace(name)
        self.system_client.add_namespace_key(name, 'test', key)
        return apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=name, key=key,
            async_strategy=apiclient.ASYNC_PAUSE)

    def _remove_namespace(self, name, timeout=120):
        if name not in namespace_names(self.system_client.get_namespaces()):
            return

        # The API refuses to delete a namespace which still owns a live
        # instance or network, and our callers delete those through a
        # non-blocking client -- so a namespace whose objects have
        # stopped being listed can still be a few seconds short of
        # deletable. Retry rather than either failing the test on a
        # timing artefact or ignoring a refusal that will not clear.
        #
        # Only those two refusals, though. This runs at the end of a
        # teardown which has already spent up to ten minutes waiting for
        # instances and networks, so retrying a 400 which will never
        # clear -- a malformed request, a client and server which
        # disagree -- would add two silent minutes to it and then report
        # the same error anyway.
        start_time = time.time()
        reported = False
        while True:
            try:
                self.system_client.delete_namespace(name)
                return
            except apiclient.ResourceNotFoundException:
                # Gone between the listing and the delete, which is the
                # outcome we wanted.
                return
            except apiclient.RequestMalformedException as e:
                if NAMESPACE_NOT_EMPTY not in str(e.text):
                    raise
                if time.time() - start_time > timeout:
                    raise
                if not reported:
                    # Say why on the first retry rather than only at the
                    # timeout, so a teardown which is going to be slow
                    # says so while it is happening.
                    LOG.info('Namespace %s is not yet deletable, retrying '
                             'for up to %ds: %s' % (name, timeout, e.text))
                    reported = True
                time.sleep(5)

    def _uniquifier(self):
        return ''.join(random.choice(string.ascii_lowercase) for i in range(8))

    def _emit_tracing_event(self, event):
        # This method implements a simple tracing scheme to help work out what
        # the slow bits of a unit test are. This isn't really a complete thing,
        # its more of a proof of concept at this point.
        event['ts'] = time.time()
        event['ts_pretty'] = str(datetime.datetime.now())

        if 'error' in event:
            try:
                json.dumps(event['error'])
            except TypeError:
                # Exception classes are not JSON serializable for example...
                event['error_type'] = type(event['error']).__name__
                event['error'] = str(event['error'])

        os.makedirs(TRACE_PATH, exist_ok=True)
        with open(os.path.join(TRACE_PATH, f'{self._testMethodName}.json'),
                  'a') as f:
            f.write(json.dumps(event))
            f.write('\n')

    def _log_console(self, instance_uuid):
        """ Log the console of the instance so that we can debug. """
        sys.stderr.write(
            '----------------------- start %s console -----------------------\n'
            % instance_uuid)
        for line in self.system_client.get_console_data(instance_uuid, -1).split('\n')[-20:]:
            sys.stderr.write('Instance console: %s\n' % line)
        sys.stderr.write(
            '----------------------- end %s console -----------------------\n'
            % instance_uuid)

    def _log_instance_events(self, instance_uuid):
        # If we've failed, log all events and then raise an exception
        self._log_events(instance_uuid,
                         self.system_client.get_instance_events(instance_uuid))

    def _log_image_events(self, image_uuid):
        self._log_events(
            image_uuid, self.system_client.get_artifact_events(image_uuid))

    def _log_events(self, uuid, event_source):
        x = PrettyTable()
        x.field_names = ['timestamp', 'node', 'duration', 'message']
        for e in event_source:
            e['timestamp'] = datetime.datetime.fromtimestamp(e['timestamp'])
            x.add_row([e['timestamp'], e['fqdn'], e['duration'], e['message']])

        sys.stderr.write(
            '----------------------- start %s events -----------------------\n'
            % uuid)
        sys.stderr.write(str(x))
        sys.stderr.write('\n')
        sys.stderr.write(
            '----------------------- end %s events -----------------------\n'
            % uuid)

    def _log_netns(self):
        """Log the current net namespaces."""
        sys.stderr.write(
            '----------------------- netns -----------------------\n')
        out, err = processutils.execute('sudo ip netns', shell=True,
                                        check_exit_code=[0, 1])
        for line in out:
            sys.stderr.write(line)
        sys.stderr.write(
            '----------------------- end netns -----------------------\n')

    # -----------------------------------------------------------------
    # Cluster node discovery and remote command execution.
    #
    # The suite is not necessarily running on the node whose host state a
    # test needs to inspect (the network node, or a particular hypervisor).
    # These helpers discover the cluster's nodes from the API -- so tests
    # carry no knowledge of the CI IP plan or node names -- and run a
    # command on a chosen node, directly if it is this host and otherwise
    # over ssh on the management mesh. See
    # docs/plans/PLAN-ci-node-exec-assertions.md.
    # -----------------------------------------------------------------
    def _get_cluster_nodes(self):
        """Return the cluster's nodes as reported by the API."""
        return self.system_client.get_nodes()

    def _network_node(self):
        """Return the node dict for the cluster's network node, or None."""
        for n in self._get_cluster_nodes():
            if n.get('is_network_node'):
                return n
        return None

    def _hypervisor_nodes(self, exclude_network_node=False):
        """Return node dicts for hypervisors.

        When exclude_network_node is set, the network node is omitted even
        if it is also a hypervisor. This is what lets a caller reason about
        network plumbing that is present *only* because an instance is
        hosted there, uncontaminated by the network node's always-present
        DHCP/NAT namespace.
        """
        nodes = []
        for n in self._get_cluster_nodes():
            if not n.get('is_hypervisor'):
                continue
            if exclude_network_node and n.get('is_network_node'):
                continue
            nodes.append(n)
        return nodes

    def _local_ipv4_addresses(self):
        """The set of IPv4 addresses configured on the local host."""
        out, _ = processutils.execute('ip', '-json', 'addr', 'show')
        addresses = set()
        for link in json.loads(out):
            for addr in link.get('addr_info', []):
                if addr.get('family') == 'inet' and addr.get('local'):
                    addresses.add(addr['local'])
        return addresses

    def _node_is_local(self, node):
        """Whether node is the host the suite is running on.

        Decided by mesh IP rather than name, to sidestep the SF node name
        (config.NODE_NAME, e.g. sf1) versus OS fqdn (e.g. t-6dFds-1)
        mismatch.
        """
        return node.get('ip') in self._local_ipv4_addresses()

    def _node_exec(self, node, args, sudo=False, check_exit_code=True):
        """Run a command on a cluster node and return (stdout, stderr).

        args is a list of command arguments. The command runs directly when
        node is this host, otherwise over ssh to the node's mesh IP. Raises
        processutils.ProcessExecutionError on an unexpected exit code;
        check_exit_code may be True (only 0), False (any), or a list of
        acceptable codes.
        """
        if sudo:
            args = ['sudo', *args]

        if check_exit_code is True:
            acceptable = [0]
        elif check_exit_code is False:
            acceptable = list(range(256))
        else:
            acceptable = check_exit_code

        if self._node_is_local(node):
            return processutils.execute(*args, check_exit_code=acceptable)

        remote = ' '.join(shlex.quote(a) for a in args)
        return processutils.execute(
            'ssh', '-i', SF_CI_SSH_KEY,
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', 'LogLevel=ERROR',
            '-o', 'ConnectTimeout=10',
            '%s@%s' % (SF_CI_SSH_USER, node['ip']), '--', remote,
            check_exit_code=acceptable)

    def _require_node_exec(self, node):
        """Skip the test loudly if commands cannot be run on node.

        A visible skip -- not a silent no-op -- is deliberate: silently
        skipping host assertions is exactly what let the floating lifecycle
        test look healthy while asserting nothing.
        """
        if node is None:
            self.skipTest(
                'No network node reported by the API; cannot run host-level '
                'assertions.')

        try:
            self._node_exec(node, ['true'])
        except processutils.ProcessExecutionError as e:
            self.skipTest(
                'Cannot exec on node %s (%s) over the mesh: %s. See '
                'docs/plans/PLAN-ci-node-exec-assertions.md for the deploy '
                'prerequisite.' % (node.get('name'), node.get('ip'), e))

    def _node_link_names(self, node):
        """The names of the network links in node's root namespace."""
        out, _ = self._node_exec(node, ['ip', '-json', 'link', 'show'])
        return [link['ifname'] for link in json.loads(out) if link]

    def _await_power_off(self, instance_uuid, after=None):
        return self._await_instance_event(
            instance_uuid, 'detected poweroff', after=after)

    def _cloud_init_health_check_json(self, instance_uuid):
        exit_code, data = self.system_client.await_agent_command(
            instance_uuid, 'cloud-init status --wait --format json',
            exit_codes=[0, 1, 2])

        j = json.loads(data)

        if exit_code == 0:
            if j.get('status') == 'done':
                self._emit_tracing_event({
                    'msg': 'Instance ready (cloud-init status)',
                    'instance_uuid': instance_uuid,
                    'data': data
                })
                return
            else:
                raise RetryException()

        # Older versions of cloud-init have a top level "error" key...
        # https://discourse.ubuntu.com/t/spec-improve-error-and-warning-visibility/39765
        if len(j.get('errors', [])) > 0:
            self._emit_tracing_event({
                'msg': 'cloud-init reports fatal errors',
                'instance_uuid': instance_uuid,
                'data': data
            })
            self.fail('cloud-init reports fatal errors')

        # Newer versions of cloud-init have it per module.
        for module in ['init-local', 'init', 'modules-config',
                       'modules-final']:
            if len(j.get(module, {}).get('errors', [])) > 0:
                self._emit_tracing_event({
                    'msg': f'cloud-init {module} reports fatal errors',
                    'instance_uuid': instance_uuid,
                    'data': data
                })
                self.fail(
                    f'cloud-init {module} reports fatal errors')

            if len(j.get(module, {}).get('recoverable_errors', [])) > 0:
                self._emit_tracing_event({
                    'msg': f'cloud-init {module} reports recoverable errors',
                    'instance_uuid': instance_uuid,
                    'data': data
                })

    def _cloud_init_health_check_older(self, instance_uuid):
        exit_code, data = self.system_client.await_agent_command(
            instance_uuid, 'cloud-init status --wait --long',
            exit_codes=[0, 1, 2])
        if exit_code == 0:
            self._emit_tracing_event({
                'msg': 'Instance ready (cloud-init status)',
                'instance_uuid': instance_uuid,
                'data': data
            })
            return

        if data.find('status: error') != -1:
            self._emit_tracing_event({
                'msg': 'cloud-init reports fatal errors',
                'instance_uuid': instance_uuid,
                'data': data
            })
            self.fail('cloud-init reports fatal errors')

        if data.find('status: done') != -1:
            return

        raise RetryException()

    def _await_instance_ready(self, instance_uuid):
        self._await_agent_state(instance_uuid, ready=True)
        self._emit_tracing_event({
            'msg': 'Instance ready (agent ready state)',
            'instance_uuid': instance_uuid
        })

        # Probe to determine if cloud-init supports JSON output...
        exit_code, data = self.system_client.await_agent_command(
            instance_uuid, 'cloud-init status --format json 2> /dev/null',
            exit_codes=[0, 1, 2])
        has_json = exit_code == 0
        self._emit_tracing_event({
            'msg': 'cloud-init JSON support probe',
            'instance_uuid': instance_uuid,
            'has_json': has_json
        })

        retries = 0
        while retries < 3:
            try:
                if has_json:
                    return self._cloud_init_health_check_json(instance_uuid)
                else:
                    return self._cloud_init_health_check_older(instance_uuid)

            except RetryException:
                time.sleep(30)
                retries += 1

            except AGENT_OPERATION_FAILURES as e:
                self._emit_tracing_event({
                    'msg': 'Instance ready (cloud-init status) attempt failed',
                    'instance_uuid': instance_uuid,
                    'attempt': retries,
                    'error': e
                })

                time.sleep(30)
                retries += 1

        # Gather debug information when this fails
        self._emit_tracing_event({
            'msg': ('Instance ready (cloud-init status) attempt failed. '
                    'Gathering debug information and then raising '
                    'TimeoutException'),
            'instance_uuid': instance_uuid
        })

        for log_file in ['/var/log/cloud-init.log',
                         '/var/log/cloud-init-output.log',
                         '/var/log/syslog']:
            try:
                _, data = self.system_client.await_agent_command(
                    instance_uuid, f'tail -50 {log_file}')
                self._emit_tracing_event({
                    'msg': f'Debug data from {log_file}',
                    'data': data
                })
            except AGENT_OPERATION_FAILURES as e:
                self._emit_tracing_event({
                    'msg': f'Failed to gather debug data from {log_file}',
                    'error': e
                })

        raise TimeoutException(
            'repeated attempts to detect cloud-init completion for '
            f'instance {instance_uuid} failed')

    def _await_instance_not_ready(self, instance_uuid):
        self._await_agent_state(instance_uuid, ready=False)

    def _await_instance_deleted(self, instance_uuid):
        start_time = time.time()
        while time.time() - start_time < 300:
            i = self.system_client.get_instance(instance_uuid)
            if not i:
                return
            if i['state'] in ('deleted', 'error'):
                return
            time.sleep(5)
        self.fail(f'Failed to delete instance after 5 minutes {instance_uuid}')

    def _await_agent_state(self, instance_uuid, ready=True):
        # Wait the instance to be created and enter the desired agent running state
        if ready:
            desired = 'ready'
        else:
            desired = 'not ready'

        last_event = None
        time_since_last_progress = time.time()
        while time.time() - time_since_last_progress < 500:
            i = self.system_client.get_instance(instance_uuid)
            if i['state'] == 'error':
                raise StartException(
                    f'Instance {instance_uuid} failed to start (marked as '
                    'error state)')

            if i['agent_state'] and i['agent_state'].startswith(desired):
                return

            events = self.system_client.get_instance_events(
                instance_uuid, limit=1)
            if events:
                last_event = events[0]
                time_since_last_progress = last_event['timestamp']

            time.sleep(5)

        cd = self.system_client.get_console_data(instance_uuid)
        cd = '\n'.join(cd.split('\n')[-10:])
        raise TimeoutException(
            f'Instance {instance_uuid} failed to start and enter the agent '
            f'{desired} state and has seen no progress in 5 minutes. Agent '
            f'state is {i["agent_state"]} and the last recorded event was '
            f'{last_event}. Last console lines were:\n\n{cd}\n...END...')

    def _await_instance_create(self, instance_uuid):
        # Wait for the instance to be created
        last_event = None
        time_since_last_progress = time.time()
        while time.time() - time_since_last_progress < 180:
            i = self.system_client.get_instance(instance_uuid)
            if i['state'] == 'error':
                raise StartException(
                    f'Instance {instance_uuid} failed to start (marked as '
                    'error state)')

            if i['state'] == 'created':
                # Issue 3897: an instance allocated the same port for two of
                # its consoles cannot start, because libvirt refuses to
                # reserve the port twice. Assert distinctness here so any
                # recurrence fails with a clear message rather than an
                # opaque start failure.
                ports = [i.get(p) for p in
                         ('console_port', 'vdi_port', 'vdi_tls_port')
                         if i.get(p)]
                self.assertEqual(
                    len(ports), len(set(ports)),
                    f'Instance {instance_uuid} was allocated duplicate '
                    f'console ports: {ports}')
                return

            events = self.system_client.get_instance_events(
                instance_uuid, limit=1)
            if events:
                last_event = events[0]
                time_since_last_progress = last_event['timestamp']

            time.sleep(5)

        raise TimeoutException(
            f'Instance {instance_uuid} failed to start and enter the created '
            f'state and has seen no progress in 3 minutes. Instance '
            f'state is {i["state"]} and the last recorded event was '
            f'{last_event}.')

    def _await_instance_event(
            self, instance_uuid, operation, message=None, after=None):
        self._await_instance_create(instance_uuid)

        # Once created, we shouldn't need more than another 5 minutes for boot.
        start_time = time.time()
        while time.time() - start_time < 5 * 60:
            for event in self.system_client.get_instance_events(instance_uuid):
                if after and event['timestamp'] <= after:
                    continue

                if (event['operation'] == operation and
                        (not message or event['message'] == message)):
                    return event['timestamp']

            time.sleep(5)

        self._log_console(instance_uuid)
        self._log_instance_events(instance_uuid)
        raise TimeoutException(
            f'After time {after}, instance {instance_uuid} had no event '
            f'"{operation}:{message}"')

    def _await_object_operations_complete(
            self, object_type, object_uuid, poll_interval, label):
        # Enumerate *every* cluster operation targeting the object rather
        # than following its ``last_cluster_operation`` pointer. That
        # single pointer is racy: a later terminal op (a routine
        # billing/health sweep, say) reaching a terminal state between
        # polls masks an earlier still-queued op, so a pointer-following
        # helper returns before the operation under test has run. This
        # bit test_interface_plug_and_exec_reboot -- see
        # docs/plans/PLAN-cluster-op-visibility.md.
        terminal_states = ['complete', 'deleted', 'abort']

        # Only operations we have actually watched go in-flight during
        # this wait may fail the test on error. A stale error op from
        # earlier in the object's history must not poison every
        # subsequent await.
        observed_outstanding = set()

        start_time = time.time()
        while time.time() - start_time < 5 * 60:
            ops = self.system_client.list_cluster_operations_for_target(
                object_type, object_uuid)

            outstanding = False
            for op in ops:
                state = op['state']
                if state == 'error':
                    if op['uuid'] in observed_outstanding:
                        self.fail(
                            f'Cluster operation {op["uuid"]} for {label} '
                            f'{object_uuid} ended in error state')
                    continue
                if state not in terminal_states:
                    observed_outstanding.add(op['uuid'])
                    outstanding = True

            if not outstanding:
                return

            time.sleep(poll_interval)

        self.fail(
            f'{label.capitalize()} operations did not complete within 5 '
            f'minutes for {object_uuid}')

    def _await_instance_operations_complete(self, instance_uuid):
        self._await_object_operations_complete(
            'instance', instance_uuid, 10, 'instance')

    def _await_network_operations_complete(self, network_uuid):
        # Network mutation API endpoints (DNS updates, route updates, NAT
        # changes) write the static DB state synchronously but enqueue
        # the actual network-node-side reconcile as a cluster op that
        # runs later. Tests that assert on the post-mutation network
        # node state must wait for that op to drain or they will race
        # the dnsmasq/iptables/etc reload.
        self._await_object_operations_complete(
            'network', network_uuid, 5, 'network')

    def _await_image_download_success(self, image_uuid, after=None):
        return self._await_image_event(image_uuid, 'fetch', 'success', after)

    def _await_image_event(
            self, image_uuid, operation, message=None, after=None):
        start_time = time.time()
        while time.time() - start_time < 900:
            for event in self.system_client.get_artifact_events(image_uuid):
                if after and event['timestamp'] <= after:
                    continue

                if event['operation'] == operation:
                    if message in str(event['message']):
                        return event['timestamp']

                    self._log_image_events(image_uuid)
                    raise WrongEventException(
                        'After time %s, image %s expected event "%s:%s" got %s'
                        % (after, image_uuid, operation, message, event['message']))

            time.sleep(5)

        self._log_image_events(image_uuid)
        raise TimeoutException(
            'After time %s, image %s had no event type "%s" (waited 5 mins)'
            % (after, image_uuid, operation))

    def _await_objects_ready(self, get_callback, event_callback, items):
        waiting_for = list(enumerate(items))
        results = [None] * len(items)

        last_event = None
        time_since_last_progress = time.time()
        while waiting_for:
            for idx, item in copy.copy(waiting_for):
                try:
                    n = get_callback(item)
                    if n.get('state') in ['created', 'deleted', 'error']:
                        waiting_for.remove((idx, item))
                        results[idx] = n
                        time_since_last_progress = time.time()
                    else:
                        events = event_callback(item, limit=1)
                        if events:
                            last_event = events[0]
                            time_since_last_progress = last_event['timestamp']

                except apiclient.ResourceNotFoundException:
                    # Its likely this exception can be removed once PR #1314 (or
                    # equivalent) is merged. The issue right now is that blobs
                    # aren't created in the database until they're ready on disk,
                    # which means they initially 404 here.
                    pass

            if waiting_for:
                time.sleep(5)

            if waiting_for and time.time() - time_since_last_progress > 300:
                remaining = []
                for _, item in waiting_for:
                    remaining.append(item)
                remaining_string = ', '.join(remaining)

                raise TimeoutException(
                    f'Items {remaining_string} never became ready, and no '
                    f'progress has been made in at least five minutes. The last  '
                    f'recorded event was {last_event}.')

        return results

    def _await_networks_ready(self, network_uuids):
        return self._await_objects_ready(
            self.system_client.get_network,
            self.system_client.get_network_events,
            network_uuids)

    def _await_artifacts_ready(self, artifact_uuids):
        return self._await_objects_ready(
            self.system_client.get_artifact,
            self.system_client.get_artifact_events,
            artifact_uuids)

    def _await_blobs_ready(self, blob_uuids):
        return self._await_objects_ready(
            self.system_client.get_blob,
            self.system_client.get_blob_events,
            blob_uuids)

    # How long _await_command() will wait for an agent operation. It
    # only has to exceed the server's own budget: an operation which
    # runs out of one lands in expired and is caught by the terminal
    # state check below long before this fires. This is the backstop
    # for the operation never reaching a terminal state at all.
    AGENT_OPERATION_TIMEOUT = 900

    # Everything an agent operation can end up in which is not
    # success. expired is new as of the deadline enforcement work, and
    # is why this loop grew a terminal state check: before it, an
    # operation which was never going to complete simply never
    # completed, and there was no state to notice.
    AGENT_OPERATION_FAILED_STATES = ('error', 'expired', 'deleted')

    def _await_command(self, instance_ref, command):
        aop = self.system_client.instance_execute(instance_ref, command)
        start_time = time.time()

        while aop['state'] != 'complete':
            if aop['state'] in self.AGENT_OPERATION_FAILED_STATES:
                self._raise_agent_operation_failure(
                    instance_ref, command, aop,
                    f"agent operation {aop['uuid']} for command "
                    f"'{command}' finished in state {aop['state']} "
                    'instead of completing')

            if time.time() - start_time > self.AGENT_OPERATION_TIMEOUT:
                self._raise_agent_operation_failure(
                    instance_ref, command, aop,
                    f"agent operation {aop['uuid']} for command "
                    f"'{command}' was still in state {aop['state']} after "
                    f'{self.AGENT_OPERATION_TIMEOUT} seconds')

            time.sleep(1)
            aop = self.system_client.get_agent_operation(aop['uuid'])

        return aop['results']['0']

    def _raise_agent_operation_failure(self, instance_ref, command, aop,
                                       message):
        """Emit what is known about a failed agent operation, then fail.

        The operation's own view carries its error_message, results,
        attempts and last_progress. The reason an expired operation
        expired is not on that view -- it is the state row's message --
        but expire() also audits it against the instance, so the
        instance's recent events are where the "deadline passed while
        queued" versus "no progress from the agent" distinction is
        readable.
        """
        self._emit_tracing_event({
            'msg': 'Agent operation failed',
            'instance_uuid': instance_ref,
            'command': command,
            'agent_operation': aop
        })

        try:
            events = self.system_client.get_instance_events(
                instance_ref, limit=50)
            self._emit_tracing_event({
                'msg': 'Recent instance events',
                'instance_uuid': instance_ref,
                'events': events
            })
        except Exception as e:
            self._emit_tracing_event({
                'msg': 'Failed to gather instance events',
                'instance_uuid': instance_ref,
                'error': str(e)
            })

        raise TimeoutException(
            f'{message}. The operation was: '
            f'{json.dumps(aop, indent=4, sort_keys=True, default=str)}')

    def _test_ping(self, instance_uuid, network_uuid, ip, expected):
        # NOTE(mikal): each call to client.ping() sends 10 ICMP packets
        packet_loss_re = re.compile(r'.* ([0-9\.]+)% packet loss.*')
        self._emit_tracing_event({
            'msg': 'Executing test ping',
            'instance_uuid': instance_uuid,
            'network_uuid': network_uuid,
            'ip': ip,
            'expected': expected
        })

        packet_loss = None
        output = self.system_client.ping(network_uuid, ip)
        for line in output.get('stdout', []):
            m = packet_loss_re.match(line)
            if m:
                packet_loss = int(m.group(1))
                break

        failed = False
        if expected and packet_loss > 10:
            failed = True
        elif not expected and packet_loss != 100:
            failed = True

        self._emit_tracing_event({
            'msg': 'Executed test ping',
            'output': output,
            'packet_loss': packet_loss,
            'failed': failed,
            'expected': {
                True: 'pass',
                False: 'failure'
            }
        })

        if failed:
            self._log_console(instance_uuid)
            self._log_instance_events(instance_uuid)
            self._log_netns()
            sys.stderr.write('Current time: '+time.ctime()+'\n')
            self.fail('Ping test failed. Expected %s != actual %s.\nout: %s\nerr: %s\n'
                      % (expected, packet_loss, output['stdout'], output['stderr']))

    def assertInstanceOk(self, instance_uuid):
        inst = self.system_client.get_instance(instance_uuid)
        self.assertTrue(inst['state'] == 'created')

    def assertInstanceConsoleAfterBoot(self, instance_uuid, contains):
        self.assertIsNotNone(instance_uuid)
        LOG.info('Waiting for %s to be ready' % instance_uuid)
        self._await_instance_ready(instance_uuid)

        # Wait for the console log to have any data (i.e. boot commenced)
        start_time = time.time()
        while True:
            LOG.info('Waiting for console of %s' % instance_uuid)
            console = self.test_client.get_console_data(instance_uuid, 100)
            if len(console) > 0:
                break

            if time.time() - start_time > 300:
                raise TimeoutException(
                    'Instance %s console never became ready' % instance_uuid)
            time.sleep(30)

        # And then ensure that what we're expecting is in the console log
        start_time = time.time()
        while True:
            LOG.info('Verifying console log of %s' % instance_uuid)
            console = self.test_client.get_console_data(instance_uuid, 100000)
            if console.find(contains) != -1:
                return
            LOG.info('Console of %s did not match. We searched for %s in:'
                     '\n\n-----\n%s\n-----\n'
                     % (instance_uuid, contains, console))

            if time.time() - start_time > 300:
                LOG.info('Instance %s: \n%s'
                         % (instance_uuid,
                            json.dumps(self.test_client.get_instance(instance_uuid),
                                       indent=4, sort_keys=True)))
                raise TimeoutException(
                    'Instance %s never became ready. We searched for %s in:'
                    '\n\n-----\n%s\n-----\n'
                    % (instance_uuid, contains, console))
            time.sleep(30)


class BaseNamespacedTestCase(BaseTestCase):
    def __init__(self, *args, **kwargs):
        namespace_prefix = kwargs.get('namespace_prefix')
        del kwargs['namespace_prefix']
        self.namespace = f'ci-{namespace_prefix}-{self._uniquifier()}'
        self.namespace_key = self._uniquifier()

        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.test_client = self._make_namespace(
            self.namespace, self.namespace_key)

    def tearDown(self):
        super().tearDown()

        non_blocking_client = apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)
        for inst in non_blocking_client.get_instances():
            try:
                non_blocking_client.delete_instance(inst['uuid'])
            except apiclient.ResourceNotFoundException:
                ...

        start_time = time.time()
        last_retry = start_time
        while time.time() - start_time < 5 * 60:
            remaining = list(self.test_client.get_instances())
            if not remaining:
                break

            if time.time() - last_retry > 60:
                last_retry = time.time()
                for inst in remaining:
                    try:
                        non_blocking_client.delete_instance(inst['uuid'])
                    except apiclient.ResourceNotFoundException:
                        ...

            time.sleep(5)

        remaining_instances = list(non_blocking_client.get_instances())
        if remaining_instances:
            self.fail('Failed to delete instances: %s' % remaining_instances)

        start_time = time.time()
        while time.time() - start_time < 5 * 60:
            for net in non_blocking_client.get_networks():
                try:
                    non_blocking_client.delete_network(net['uuid'])
                except (apiclient.ResourceStateConflictException,
                        apiclient.ResourceNotFoundException):
                    ...

            time.sleep(5)

            if not list(non_blocking_client.get_networks()):
                break
            time.sleep(5)

        remaining_networks = list(non_blocking_client.get_networks())
        if remaining_networks:
            self.fail('Failed to delete networks: %s'
                      % remaining_networks)

        self._remove_namespace(self.namespace)

    def _await_agentop_complete(self, instance_uuid, aop, timeout,
                                description='agent operation'):
        # Poll a single agent operation to completion with its own independent
        # timeout window. Previously test_instance_put_and_get_blob shared one
        # start_time across three sequential operations, so the last and
        # heaviest of them (get-file, which reads, hashes and uploads a blob
        # back to the cluster) was left only whatever budget the earlier
        # operations had not already consumed. Under under-cloud contention
        # that remainder collapsed and get-file "timed out" while still
        # legitimately executing -- the intermittent merge-queue flake.
        #
        # Hoisted here from guest_ci_tests/test_agentops.py in agent
        # operation deadlines phase 7: a0cc243ad fixed this bug in that
        # file's copy of test_instance_put_and_get_blob but never in the
        # smoke suite's near-identical copy, which runs on every pull
        # request and kept the flake alive. A shared implementation is what
        # stops a fix from half-landing again. This is also where the
        # terminal-state check lives -- an operation which reaches expired
        # or error now fails immediately instead of burning the rest of its
        # window and then reporting a bare timeout.
        #
        # It sits on BaseNamespacedTestCase rather than beside _await_command
        # on BaseTestCase because it polls as self.test_client, which only
        # the namespaced class creates. Polling as the system client instead
        # would work -- it is admin -- but it would be a different assertion
        # about what a namespaced caller can see, which is not this step's
        # to make.
        start_time = time.time()
        while aop['state'] != 'complete':
            if aop['state'] in self.AGENT_OPERATION_FAILED_STATES:
                self._raise_agent_operation_failure(
                    instance_uuid, description, aop,
                    f"agent operation {aop['uuid']} for {description} "
                    f"finished in state {aop['state']} instead of "
                    'completing')

            if time.time() - start_time > timeout:
                self._raise_agent_operation_failure(
                    instance_uuid, description, aop,
                    f"agent operation {aop['uuid']} for {description} was "
                    f"still in state {aop['state']} after {timeout} "
                    'seconds')

            time.sleep(5)
            aop = self.test_client.get_agent_operation(aop['uuid'])

        return aop


class TestDistroBoots(BaseNamespacedTestCase):
    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def _test_distro_boot(self, base_image):
        inst = self.test_client.create_instance(
            base_image.replace(':', '-').replace('.', ''), 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base_image,
                    'type': 'disk'
                }
            ], None, None)

        self._await_instance_ready(inst['uuid'])

        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)
