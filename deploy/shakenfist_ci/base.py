import datetime
import random
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
        x = PrettyTable()
        x.field_names = ['timestamp', 'node',
                         'operation', 'phase', 'duration', 'message']
        for e in self.system_client.get_instance_events(instance_uuid):
            e['timestamp'] = datetime.datetime.fromtimestamp(e['timestamp'])
            x.add_row([e['timestamp'], e['fqdn'], e['operation'], e['phase'],
                       e['duration'], e['message']])

        sys.stderr.write(
            '----------------------- start %s events -----------------------\n'
            % instance_uuid)
        sys.stderr.write(str(x))
        sys.stderr.write('\n')
        sys.stderr.write(
            '----------------------- end %s events -----------------------\n'
            % instance_uuid)

    def _await_power_off(self, instance_uuid, after=None):
        return self._await_event(
            instance_uuid, 'detected poweroff', after=after)

    def _await_login_prompt(self, instance_uuid, after=None):
        return self._await_event(
            instance_uuid, 'trigger', 'login prompt', after)

    def _await_event(self, instance_uuid, operation, message=None, after=None):
        # Wait up to two minutes for the instance to be created.
        start_time = time.time()
        created = False
        while time.time() - start_time < 120:
            i = self.system_client.get_instance(instance_uuid)
            if i['state'] == 'created':
                created = True
                break
            time.sleep(5)

        if not created:
            raise TimeoutException(
                'Instance %s was not created in a reasonable time'
                % instance_uuid)

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

        self._log_console(instance_uuid)
        self._log_instance_events(instance_uuid)
        raise TimeoutException(
            'Instance %s never triggered a login prompt after %s' % (instance_uuid, after))

    def _test_ping(self, instance_uuid, network_uuid, ip, expected, attempts=1):
        while attempts:
            sys.stderr.write('    _test_ping()  attempts=%s\n' % attempts)
            attempts -= 1
            out, err = processutils.execute(
                'sudo ip netns exec %s ping -c 10 %s' % (network_uuid, ip),
                shell=True, check_exit_code=[0, 1])

            actual = out.find(' 0% packet loss') != -1
            if actual == expected:
                break

            time.sleep(1)  # Almost unnecessary due to the slowness of execute()

        if expected != actual:
            self._log_console(instance_uuid)
            self._log_instance_events(instance_uuid)
            self.fail('Ping test failed. Expected %s != actual %s.\nout: %s\nerr: %s\n'
                      % (expected, actual, out, err))


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

        for net in self.test_client.get_networks():
            self.test_client.delete_network(net['uuid'])
        self._remove_namespace(self.namespace)


class LoggingSocket(object):
    ctrlc = '\x03'

    def __init__(self, host, port):
        self.s = telnetlib.Telnet(host, port, 30)

    def await_login_prompt(self):
        start_time = time.time()
        while True:
            for line in self.recv().split('\n'):
                if line.rstrip('\r\n ').endswith(' login:'):
                    return

            time.sleep(0.5)
            if time.time() - start_time > 120.0:
                return

    def ensure_fresh(self):
        for d in [self.ctrlc, self.ctrlc, '\nexit\n', 'cirros\n', 'gocubsgo\n']:
            self.send(d)
            time.sleep(0.5)
            self.recv()

    def send(self, data):
        # print('>> %s' % data.replace('\n', '\\n').replace('\r', '\\r'))
        self.s.write(data.encode('ascii'))

    def recv(self):
        data = self.s.read_eager().decode('ascii')
        # for line in data.split('\n'):
        #    print('<< %s' % line.replace('\n', '\\n').replace('\r', '\\r'))
        return data

    def execute(self, cmd):
        self.ensure_fresh()
        self.send(cmd + '\n')
        time.sleep(5)
        d = ''
        while not d.endswith('\n$ '):
            d += self.recv()
        return d
