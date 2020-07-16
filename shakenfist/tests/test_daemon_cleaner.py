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
        return self._state


def fake_exists(path):
    if path == '/srv/shakenfist/instances/nofiles':
        return False
    return True


FAKE_ETCD_STATE = {}


def fake_get(objecttype, subtype, name):
    global FAKE_ETCD_STATE
    return FAKE_ETCD_STATE.get(
        '%s/%s/%s' % (objecttype, subtype, name),
        {'uuid': name})


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

        self.proctitle = mock.patch('setproctitle.setproctitle')
        self.mock_proctitle = self.proctitle.start()

    @mock.patch('shakenfist.db.see_this_node')
    @mock.patch('shakenfist.etcd.get', side_effect=fake_get)
    @mock.patch('shakenfist.etcd.put', side_effect=fake_put)
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    def test_update_power_states(self, mock_time, mock_exists, mock_put,
                                 mock_get, mock_see):
        m = cleaner.monitor()
        m._update_power_states()

        self.assertEqual(
            [
                mock.call('instance', None, 'running',
                          {
                              'uuid': 'running',
                              'power_state_previous': 'unknown',
                              'power_state': 'on',
                              'power_state_updated': 7
                          }),
                mock.call('instance', None, 'shutoff',
                          {
                              'uuid': 'shutoff',
                              'power_state_previous': 'unknown',
                              'power_state': 'off',
                              'power_state_updated': 7
                          }),
                mock.call('instance', None, 'crashed',
                          {
                              'uuid': 'crashed',
                              'power_state_previous': 'unknown',
                              'power_state': 'crashed',
                              'power_state_updated': 7,
                              'state': 'error',
                              'state_updated': 7
                          }),
                mock.call('instance', None, 'crashed',
                          {
                              'uuid': 'crashed',
                              'power_state_previous': 'unknown',
                              'power_state': 'crashed',
                              'power_state_updated': 7,
                              'state': 'error',
                              'state_updated': 7
                          }),
                mock.call('instance', None, 'paused',
                          {
                              'uuid': 'paused',
                              'power_state_previous': 'unknown',
                              'power_state': 'paused',
                              'power_state_updated': 7
                          }),
                mock.call('instance', None, 'suspended',
                          {
                              'uuid': 'suspended',
                              'power_state_previous': 'unknown',
                              'power_state': 'paused',
                              'power_state_updated': 7
                          }),
                mock.call('instance', None, 'foo',
                          {
                              'uuid': 'foo',
                              'power_state_previous': 'unknown',
                              'power_state': 'off',
                              'power_state_updated': 7
                          }),
                mock.call('instance', None, 'bar',
                          {
                              'uuid': 'bar',
                              'power_state_previous': 'unknown',
                              'power_state': 'off',
                              'power_state_updated': 7
                          }),
                mock.call('instance', None, 'nofiles',
                          {
                              'uuid': 'nofiles',
                              'state': 'error',
                              'state_updated': 7
                          })
            ],
            mock_put.mock_calls)
