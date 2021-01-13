import mock

from shakenfist.baseobject import State
from shakenfist.config import SFConfigBase
from shakenfist.daemons import cleaner
from shakenfist.tests import test_shakenfist


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


class FakeConfig(SFConfigBase):
    NODE_NAME: str = 'abigcomputer'
    STORAGE_PATH: str = '/srv/shakenfist'
    LOGLEVEL_CLEANER: str = 'debug'
    LOG_METHOD_TRACE: int = 1


fake_config = FakeConfig()


FAKE_ETCD_STATE = {}


def fake_instance_get(uuid):
    global FAKE_ETCD_STATE
    return FAKE_ETCD_STATE.get(
        '%s/%s/%s' % ('instance', None, uuid),
        {
            'uuid': uuid,
            'node': 'abigcomputer',
            'name': 'bob',
            'cpus': 1,
            'memory': 1024,
            'namespace': 'space',
            'disk_spec': [{'base': 'cirros', 'size': 8}],
            'version': 2
        })


def fake_put(objecttype, subtype, name, v):
    global FAKE_ETCD_STATE
    FAKE_ETCD_STATE['%s/%s/%s' % (objecttype, subtype, name)] = v


def fake_get(objecttype, subtype, name):
    global FAKE_ETCD_STATE
    val = FAKE_ETCD_STATE.get('%s/%s/%s' % (objecttype, subtype, name))
    return val


class CleanerTestCase(test_shakenfist.ShakenFistTestCase):
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
        self.config = mock.patch('shakenfist.daemons.cleaner.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.virt.Instance.error',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.etcd.get', side_effect=fake_get)
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.see_this_node')
    @mock.patch('shakenfist.db.add_event')
    @mock.patch('shakenfist.virt.Instance._db_get', side_effect=fake_instance_get)
    @mock.patch('shakenfist.etcd.put', side_effect=fake_put)
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    def test_update_power_states(self, mock_time, mock_exists, mock_put,
                                 mock_get_instance, mock_event, mock_see,
                                 mock_lock, mock_etcd_get, mock_error):

        m = cleaner.Monitor('cleaner')
        m._update_power_states()

        result = []
        for c in mock_put.mock_calls:
            if type(c[1][3]) is dict and 'placement_attempts' in c[1][3]:
                continue
            if type(c[1][3]) is State:
                val = c[1][3]
            else:
                val = {'power_state': c[1][3]['power_state']}
            result.append((c[1][0], c[1][1], c[1][2], val))

        self.assertEqual(
            [
                ('attribute/instance', 'running',
                 'power_state', {'power_state': 'on'}),
                ('attribute/instance', 'shutoff',
                 'power_state', {'power_state': 'off'}),
                ('attribute/instance', 'crashed',
                 'power_state', {'power_state': 'crashed'}),
                ('attribute/instance', 'crashed',
                 'state', State('error', 7)),
                ('attribute/instance', 'paused',
                 'power_state', {'power_state': 'paused'}),
                ('attribute/instance', 'suspended',
                 'power_state', {'power_state': 'paused'}),
                ('attribute/instance', 'foo',
                 'power_state', {'power_state': 'off'}),
                ('attribute/instance', 'bar',
                 'power_state', {'power_state': 'off'}),
                ('attribute/instance', 'nofiles',
                 'state', State('error', 7))
            ], result)
