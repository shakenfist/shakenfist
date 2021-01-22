import datetime
import random
from socket import error as socket_error
import string
import sys
import testtools
import telnetlib
import time

from oslo_concurrency import processutils
from prettytable import PrettyTable
from shakenfist_client import apiclient


class TimeoutException(Exception):
    pass


class StartException(Exception):
    pass


class WrongEventException(Exception):
    pass


class BaseTestCase(testtools.TestCase):
    def setUp(self):
        super(BaseTestCase, self).setUp()

        self.system_client = apiclient.Client()

    def _make_namespace(self, name, key):
        self._remove_namespace(name)

        self.system_client.create_namespace(name)
        self.system_client.add_namespace_key(name, 'test', key)
        return apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=name,
            key=key)

    def _remove_namespace(self, name):
        ns = self.system_client.get_namespaces()
        if name in ns:
            self.system_client.delete_namespace(name)

    def _uniquifier(self):
        return ''.join(random.choice(string.ascii_lowercase) for i in range(8))

    def _log_console(self, instance_uuid):
        """ Log the console of the instance so that we can debug. """
        sys.stderr.write(
            '----------------------- start %s console -----------------------\n'
            % instance_uuid)
        for line in self.system_client.get_console_data(instance_uuid, -1).split('\n'):
            sys.stderr.write('Instance console: %s\n' % line)
        sys.stderr.write(
            '----------------------- end %s console -----------------------\n'
            % instance_uuid)

    def _log_instance_events(self, instance_uuid):
        # If we've failed, log all events and then raise an exception
        self._log_events(instance_uuid,
                         self.system_client.get_instance_events(instance_uuid))

    def _log_image_events(self, url):
        self._log_events(url, self.system_client.get_image_events(url))

    def _log_events(self, uuid, event_source):
        x = PrettyTable()
        x.field_names = ['timestamp', 'node',
                         'operation', 'phase', 'duration', 'message']
        for e in event_source:
            e['timestamp'] = datetime.datetime.fromtimestamp(e['timestamp'])
            x.add_row([e['timestamp'], e['fqdn'], e['operation'], e['phase'],
                       e['duration'], e['message']])

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

    def _await_login_prompt(self, instance_uuid, after=None):
        return self._await_instance_event(
            instance_uuid, 'trigger', 'login prompt', after)

    def _await_instance_event(
            self, instance_uuid, operation, message=None, after=None):
        # Wait up to 5 minutes for the instance to be created. On a slow
        # morning it can take over 2 minutes to download a Ubuntu image.
        start_time = time.time()
        final = False
        while time.time() - start_time < 300:
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

        # Once created, we shouldn't need more than five minutes for boot.
        start_time = time.time()
        while time.time() - start_time < 300:
            for event in self.system_client.get_instance_events(instance_uuid):
                if after and event['timestamp'] <= after:
                    continue

                if (event['operation'] == operation and
                        (not message or event['message'] == message)):
                    return event['timestamp']

            time.sleep(5)

            # If this is a login prompt, then try mashing the console keyboard
            if message == 'login prompt':
                try:
                    s = telnetlib.Telnet(i['node'], i['console_port'], 30)
                    s.write('\n'.encode('ascii'))
                    s.close()
                except socket_error:
                    pass

        self._log_console(instance_uuid)
        self._log_instance_events(instance_uuid)
        raise TimeoutException(
            'After time %s, instance %s had no event "%s:%s" (waited 5 mins)' % (
                after, instance_uuid, operation, message))

    def _await_image_download_success(self, url, after=None):
        return self._await_image_event(url, 'fetch', 'success', after)

    def _await_image_download_error(self, url, after=None):
        return self._await_image_event(
            url, 'fetch', 'Name or service not known', after)

    def _await_image_event(
            self, url, operation, message=None, after=None):
        start_time = time.time()
        while time.time() - start_time < 300:
            for event in self.system_client.get_image_events(url):
                if after and event['timestamp'] <= after:
                    continue

                if event['operation'] == operation:
                    if message in str(event['message']):
                        return event['timestamp']

                    self._log_image_events(url)
                    raise WrongEventException(
                        'After time %s, image %s expected event "%s:%s" got %s' % (
                            after, url, operation, message, event['message']))

            time.sleep(5)

        self._log_image_events(url)
        raise TimeoutException(
            'After time %s, image %s had no event type "%s" (waited 5 mins)' % (
                after, url, operation))

    def _test_ping(self, instance_uuid, network_uuid, ip, expected, attempts=1):
        while attempts:
            sys.stderr.write('    _test_ping()  attempts=%s\n' % attempts)
            attempts -= 1
            output = self.system_client.ping(network_uuid, ip)

            actual = output.get('stdout').find(' 0% packet loss') != -1
            if actual == expected:
                break

            # Almost unnecessary due to the slowness of execute()
            time.sleep(1)

        if expected != actual:
            self._log_console(instance_uuid)
            self._log_instance_events(instance_uuid)
            self._log_netns()
            sys.stderr.write('Current time: '+time.ctime()+'\n')
            self.fail('Ping test failed. Expected %s != actual %s.\nout: %s\nerr: %s\n'
                      % (expected, actual, output['stdout'], output['stderr']))


class BaseNamespacedTestCase(BaseTestCase):
    def __init__(self, *args, **kwargs):
        namespace_prefix = kwargs.get('namespace_prefix')
        del kwargs['namespace_prefix']
        self.namespace = 'ci-%s-%s' % (namespace_prefix,
                                       self._uniquifier())
        self.namespace_key = self._uniquifier()

        super(BaseNamespacedTestCase, self).__init__(*args, **kwargs)

    def setUp(self):
        super(BaseNamespacedTestCase, self).setUp()
        self.test_client = self._make_namespace(
            self.namespace, self.namespace_key)

    def tearDown(self):
        super(BaseNamespacedTestCase, self).tearDown()
        for inst in self.test_client.get_instances():
            self.test_client.delete_instance(inst['uuid'])

        start_time = time.time()
        while time.time() - start_time < 300:
            if not list(self.test_client.get_instances()):
                break
            time.sleep(5)

        remaining_instances = list(self.test_client.get_instances())
        if remaining_instances:
            self.fail('Failed to delete instances: %s'
                      % remaining_instances)

        for net in self.test_client.get_networks():
            self.test_client.delete_network(net['uuid'])
        self._remove_namespace(self.namespace)


class LoggingSocket(object):
    ctrlc = '\x03'

    def __init__(self, host, port):
        attempts = 5
        while attempts:
            try:
                attempts -= 1
                self.s = telnetlib.Telnet(host, port, 30)
                return

            except ConnectionRefusedError:
                print('!! Connection refused, retrying')
                time.sleep(5)

        raise ConnectionRefusedError(
            'Repeated telnet connection attempts failed: host=%s port=%s' %
            (host, port))

    def ensure_fresh(self):
        for d in [self.ctrlc, self.ctrlc, '\nexit\n', 'cirros\n', 'gocubsgo\n']:
            self.send(d)
            time.sleep(0.5)
            self.recv()

    def send(self, data):
        print('>> %s' % data.replace('\n', '\\n').replace('\r', '\\r'))
        self.s.write(data.encode('ascii'))

    def recv(self):
        data = self.s.read_eager().decode('ascii')
        for line in data.split('\n'):
            print('<< %s' % line.replace('\n', '\\n').replace('\r', '\\r'))
        return data

    def execute(self, cmd):
        self.ensure_fresh()
        self.send(cmd + '\n')
        time.sleep(5)
        d = ''

        reads = 0
        while not d.endswith('\n$ '):
            d += self.recv()
            reads += 1

            if reads > 10:
                break
        return d
