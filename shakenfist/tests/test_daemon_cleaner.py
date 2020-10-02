import mock
import testtools


from shakenfist.daemons import cleaner


class FakeLibvirt(object):
    VIR_DOMAIN_BLOCKED = 1
    VIR_DOMAIN_CRASHED = 2
    VIR_DOMAIN_NOSTATE = 3
    VIR_DOMAIN_PAUSED = 4
    VIR_DOMAIN_RUNNING = 5
    VIR_DOMAIN_SHUTDOWN = 6
    VIR_DOMAIN_SHUTOFF = 7
    VIR_DOMAIN_PMSUSPENDED = 8

    def open(self, _ignored):
        return FakeLibvirtConnection()


class FakeLibvirtConnection(object):
    def listDomainsID(self):
        return ['id1', 'id2', 'id3', 'id4', 'id5', 'id6']

    def listDefinedDomains(self):
        return ['sf:foo', 'sf:bar', 'docker', 'sf:crashed', 'sf:nofiles']

    def lookupByID(self, id):
        args = {
            'id1': ('sf:running', FakeLibvirt.VIR_DOMAIN_RUNNING),
            'id2': ('apache2', FakeLibvirt.VIR_DOMAIN_RUNNING),
            'id3': ('sf:shutoff', FakeLibvirt.VIR_DOMAIN_SHUTOFF),
            'id4': ('sf:crashed', FakeLibvirt.VIR_DOMAIN_CRASHED),
            'id5': ('sf:paused', FakeLibvirt.VIR_DOMAIN_PAUSED),
            'id6': ('sf:suspended', FakeLibvirt.VIR_DOMAIN_PMSUSPENDED),
        }

        return FakeLibvirtDomain(*args.get(id))


class FakeLibvirtDomain(object):
    def __init__(self, name, state):
        self._name = name
        self._state = state

    def name(self):
        return self._name

    def state(self):
        return [self._state, 1]


def fake_exists(path):
    if path == '/srv/shakenfist/instances/nofiles':
        return False
    return True


def fake_config(key):
    fc = {
        'NODE_NAME': 'abigcomputer',
        'STORAGE_PATH': '/srv/shakenfist',
        'LOGLEVEL_CLEANER': 'debug',
        'LOG_METHOD_TRACE': 1,
    }

    if key in fc:
        return fc[key]
    raise Exception('Unknown config key %s' % key)


FAKE_ETCD_STATE = {}


def fake_get(objecttype, subtype, name):
    global FAKE_ETCD_STATE
    return FAKE_ETCD_STATE.get(
        '%s/%s/%s' % (objecttype, subtype, name),
        {'uuid': name, 'node': 'abigcomputer'})


def fake_put(objecttype, subtype, name, v):
    global FAKE_ETCD_STATE
    FAKE_ETCD_STATE['%s/%s/%s' % (objecttype, subtype, name)] = v


class CleanerTestCase(testtools.TestCase):
    def setUp(self):
        super(CleanerTestCase, self).setUp()

        self.libvirt = mock.patch(
            'shakenfist.util.get_libvirt',
            return_value=FakeLibvirt())
        self.mock_libvirt = self.libvirt.start()
        self.addCleanup(self.libvirt.stop)

        self.proctitle = mock.patch('setproctitle.setproctitle')
        self.mock_proctitle = self.proctitle.start()
        self.addCleanup(self.proctitle.stop)

        self.config = mock.patch('shakenfist.config.parsed.get',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.db.see_this_node')
    @mock.patch('shakenfist.db.add_event')
    @mock.patch('shakenfist.etcd.get', side_effect=fake_get)
    @mock.patch('shakenfist.etcd.put', side_effect=fake_put)
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    def test_update_power_states(self, mock_time, mock_exists, mock_put,
                                 mock_get, mock_event, mock_see):
        m = cleaner.Monitor('cleaner')
        m._update_power_states()

        self.assertEqual(
            [
                mock.call('instance', None, 'running',
                          {
                              'uuid': 'running',
                              'node': 'abigcomputer',
                              'power_state_previous': 'unknown',
                              'power_state': 'on',
                              'power_state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          }),
                mock.call('instance', None, 'shutoff',
                          {
                              'uuid': 'shutoff',
                              'node': 'abigcomputer',
                              'power_state_previous': 'unknown',
                              'power_state': 'off',
                              'power_state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          }),
                mock.call('instance', None, 'crashed',
                          {
                              'uuid': 'crashed',
                              'node': 'abigcomputer',
                              'power_state_previous': 'unknown',
                              'power_state': 'crashed',
                              'power_state_updated': 7,
                              'state': 'error',
                              'state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          }),
                mock.call('instance', None, 'crashed',
                          {
                              'uuid': 'crashed',
                              'node': 'abigcomputer',
                              'power_state_previous': 'unknown',
                              'power_state': 'crashed',
                              'power_state_updated': 7,
                              'state': 'error',
                              'state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          }),
                mock.call('instance', None, 'paused',
                          {
                              'uuid': 'paused',
                              'node': 'abigcomputer',
                              'power_state_previous': 'unknown',
                              'power_state': 'paused',
                              'power_state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          }),
                mock.call('instance', None, 'suspended',
                          {
                              'uuid': 'suspended',
                              'node': 'abigcomputer',
                              'power_state_previous': 'unknown',
                              'power_state': 'paused',
                              'power_state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          }),
                mock.call('instance', None, 'foo',
                          {
                              'uuid': 'foo',
                              'node': 'abigcomputer',
                              'power_state_previous': 'unknown',
                              'power_state': 'off',
                              'power_state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          }),
                mock.call('instance', None, 'bar',
                          {
                              'uuid': 'bar',
                              'node': 'abigcomputer',
                              'power_state_previous': 'unknown',
                              'power_state': 'off',
                              'power_state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          }),
                mock.call('instance', None, 'nofiles',
                          {
                              'uuid': 'nofiles',
                              'node': 'abigcomputer',
                              'state': 'error',
                              'state_updated': 7,
                              'video': {'memory': 16384, 'model': 'cirrus'},
                              'error_message': None,
                          })
            ],
            mock_put.mock_calls)
