import datetime
import random
import string
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

    def _await_login_prompt(self, instance_uuid, after=None):
        start_time = time.time()

        while time.time() - start_time < 300:
            for event in self.system_client.get_instance_events(instance_uuid):
                if after and event['timestamp'] <= after:
                    continue

                if (event['operation'] == 'trigger' and
                        event['message'] == 'login prompt'):
                    return event['timestamp']

            time.sleep(5)

        # If we've failed, log all events and then raise an exception
        x = PrettyTable()
        x.field_names = ['timestamp', 'node',
                         'operation', 'phase', 'duration', 'message']
        for e in self.system_client.get_instance_events(instance_uuid):
            e['timestamp'] = datetime.datetime.fromtimestamp(e['timestamp'])
            x.add_row([e['timestamp'], e['fqdn'], e['operation'], e['phase'],
                       e['duration'], e['message']])
        print(x)

        raise TimeoutException(
            'Instance %s never triggered a login prompt after %s' % (instance_uuid, after))

    def _test_ping(self, instance_uuid, network_uuid, ip, expected):
        out, err = processutils.execute(
            'sudo ip netns exec %s ping -c 1 %s' % (network_uuid, ip),
            shell=True, check_exit_code=[0, 1])

        actual = out.find(' 0% packet loss') != -1
        if expected != actual:
            # Log the console of the instance so that we can debug
            print('------------------------------------------------------------------')
            for line in self.system_client.get_console_data(instance_uuid).split('\n'):
                print('Instance console: %s' % line)
            print('------------------------------------------------------------------')

            self.fail('Ping test failed. Expected %s != actual %s.\nout: %s\nerr: %s\n'
                      % (expected, actual, out, err))


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
