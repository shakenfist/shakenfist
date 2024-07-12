import base64
import copy
import datetime
import json
import logging
import os
import random
import re
import string
import sys
import testtools
import time

from oslo_concurrency import processutils
from prettytable import PrettyTable
from shakenfist_client import apiclient


logging.basicConfig(level=logging.INFO, format='%(message)s')
LOG = logging.getLogger()
TRACE_PATH = '/srv/ci/traces'


class TimeoutException(Exception):
    pass


class StartException(Exception):
    pass


class WrongEventException(Exception):
    pass


# NOTE(mikal): this is a hack to "turn up the knob" on how slow image
# downloads can be while my ISP is congested because of COVID. We should
# turn this back down as things improve.
NETWORK_PATIENCE_FACTOR = 3


def load_userdata(name):
    test_dir = os.path.dirname(os.path.abspath(__file__))
    with open(f'{test_dir}/tests/files/{name}_userdata') as f:
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
        self._remove_namespace(name)

        self.system_client.create_namespace(name)
        self.system_client.add_namespace_key(name, 'test', key)
        return apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=name, key=key,
            async_strategy=apiclient.ASYNC_PAUSE)

    def _remove_namespace(self, name):
        ns = self.system_client.get_namespaces()
        if name in ns:
            self.system_client.delete_namespace(name)

    def _uniquifier(self):
        return ''.join(random.choice(string.ascii_lowercase) for i in range(8))

    def _emit_tracing_event(self, event):
        # This method implements a simple tracing scheme to help work out what
        # the slow bits of a unit test are. This isn't really a complete thing,
        # its more of a proof of concept at this point.
        os.makedirs(TRACE_PATH, exist_ok=True)
        with open(os.path.join(TRACE_PATH, f'{self._testMethodName}.json'), 'a') as f:
            event['ts'] = time.time()
            event['ts_pretty'] = str(datetime.datetime.now())
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

    def _await_power_off(self, instance_uuid, after=None):
        return self._await_instance_event(
            instance_uuid, 'detected poweroff', after=after)

    def _await_instance_ready(self, instance_uuid, timeout=7):
        self._await_agent_state(instance_uuid, ready=True, timeout=timeout)
        self._emit_tracing_event({
            'msg': 'Instance ready (agent ready state)',
            'instance_uuid': instance_uuid
        })

        self.system_client.await_agent_command(
            instance_uuid, 'cloud-init status --wait --long')
        self._emit_tracing_event({
            'msg': 'Instance ready (cloud-init status)',
            'instance_uuid': instance_uuid
        })

    def _await_instance_not_ready(self, instance_uuid):
        self._await_agent_state(instance_uuid, ready=False)

    def _await_instance_deleted(self, instance_uuid):
        start_time = time.time()
        while time.time() - start_time < 300:
            if instance_uuid not in self.system_client.get_instances():
                return
            time.sleep(5)
        self.fail('Failed to delete instance: %s' % instance_uuid)

    def _await_agent_state(self, instance_uuid, ready=True, timeout=7):
        # Wait up to 5 minutes for the instance to be created and enter
        # the desired agent running state
        if ready:
            desired = 'ready'
        else:
            desired = 'not ready'

        start_time = time.time()
        while time.time() - start_time < timeout * 60 * NETWORK_PATIENCE_FACTOR:
            i = self.system_client.get_instance(instance_uuid)
            if i['state'] == 'error':
                raise StartException(
                    'Instance %s failed to start (marked as error state)'
                    % instance_uuid)

            if i['agent_state'] and i['agent_state'].startswith(desired):
                return
            time.sleep(5)

        raise TimeoutException(
            'Instance %s failed to start and enter the agent %s state '
            'in %d minutes. Agent state is %s.'
            % (instance_uuid, desired, timeout, i['agent_state']))

    def _await_instance_create(self, instance_uuid):
        # Wait up to 5 minutes for the instance to be created. On a slow
        # morning it can take over 2 minutes to download a Ubuntu image.
        start_time = time.time()
        final = False
        while time.time() - start_time < 5 * 60 * NETWORK_PATIENCE_FACTOR:
            i = self.system_client.get_instance(instance_uuid)
            if i['state'] in ['created', 'error']:
                final = True
                break
            time.sleep(5)

        if i['state'] == 'error':
            raise StartException(
                'Instance %s failed to start (marked as error state, %s)'
                % (instance_uuid, i))

        if not final:
            raise TimeoutException(
                'Instance %s was not created in a reasonable time (%s)'
                % (instance_uuid, i))

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
            'After time {}, instance {} had no event "{}:{}"'.format(
                after, instance_uuid, operation, message))

    def _await_image_download_success(self, image_uuid, after=None):
        return self._await_image_event(image_uuid, 'fetch', 'success', after)

    def _await_image_event(
            self, image_uuid, operation, message=None, after=None):
        start_time = time.time()
        while time.time() - start_time < 5 * 60 * NETWORK_PATIENCE_FACTOR:
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

    def _await_objects_ready(self, callback, items):
        waiting_for = list(enumerate(items))
        start_time = time.time()
        results = [None] * len(items)

        while waiting_for:
            for idx, item in copy.copy(waiting_for):
                try:
                    n = callback(item)
                    if n.get('state') in ['created', 'deleted', 'error']:
                        waiting_for.remove((idx, item))
                        results[idx] = n

                except apiclient.ResourceNotFoundException:
                    # Its likely this exception can be removed once PR #1314 (or
                    # equivalent) is merged. The issue right now is that blobs
                    # aren't created in the database until they're ready on disk,
                    # which means they initially 404 here.
                    pass

            if waiting_for:
                time.sleep(5)

            if waiting_for and time.time() - start_time > 300:
                remaining = []
                for _, item in waiting_for:
                    remaining.append(item)

                raise TimeoutException(
                    'Items %s never became ready (waited 5 mins)' % ', '.join(remaining))

        return results

    def _await_networks_ready(self, network_uuids):
        return self._await_objects_ready(
            self.system_client.get_network, network_uuids)

    def _await_instances_ready(self, instance_uuids):
        res = self._await_objects_ready(
            self.system_client.get_instance, instance_uuids)

        for instance_uuid in instance_uuids:
            self.assertInstanceOk(instance_uuid)

        return res

    def _await_artifacts_ready(self, artifact_uuids):
        return self._await_objects_ready(
            self.system_client.get_artifact, artifact_uuids)

    def _await_blobs_ready(self, blob_uuids):
        return self._await_objects_ready(
            self.system_client.get_blob, blob_uuids)

    def _await_command(self, instance_ref, command):
        aop = self.system_client.instance_execute(instance_ref, command)
        while aop['state'] != 'complete':
            time.sleep(1)
            aop = self.system_client.get_agent_operation(aop['uuid'])

        return aop['results']['0']

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
        self._await_instances_ready([instance_uuid])

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
                pass

        start_time = time.time()
        while time.time() - start_time < 5 * 60:
            if not list(non_blocking_client.get_instances()):
                break
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
                    pass

            time.sleep(5)

            if not list(non_blocking_client.get_networks()):
                break
            time.sleep(5)

        remaining_networks = list(non_blocking_client.get_networks())
        if remaining_networks:
            self.fail('Failed to delete networks: %s'
                      % remaining_networks)

        self._remove_namespace(self.namespace)


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
