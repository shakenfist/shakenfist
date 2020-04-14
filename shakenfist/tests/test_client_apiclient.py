import datetime
import json
import mock
import testtools


from shakenfist.client import apiclient


class ApiClientTestCase(testtools.TestCase):
    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_get_instances(self, mock_request):
        client = apiclient.Client()
        list(client.get_instances())

        mock_request.assert_called_with(
            'GET', 'http://localhost:13000/instances')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_get_instance(self, mock_request):
        client = apiclient.Client()
        client.get_instance('notreallyauuid')

        mock_request.assert_called_with(
            'GET', 'http://localhost:13000/instances/notreallyauuid')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_get_instance_interfaces(self, mock_request):
        client = apiclient.Client()
        client.get_instance_interfaces('notreallyauuid')

        mock_request.assert_called_with(
            'GET', 'http://localhost:13000/instances/notreallyauuid/interfaces')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_create_instance(self, mock_request):
        client = apiclient.Client()
        client.create_instance('foo', 1, 2, ['netuuid1'], ['8@cirros'],
                               'sshkey', None)

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances',
            data={
                'name': 'foo',
                'cpus': 1,
                'memory': 2,
                'network': ['netuuid1'],
                'disk': ['8@cirros'],
                'ssh_key': 'sshkey',
                'user_data': None
            })

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_create_instance_user_data(self, mock_request):
        client = apiclient.Client()
        client.create_instance('foo', 1, 2, ['netuuid1'], ['8@cirros'],
                               'sshkey', 'userdatabeforebase64')

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances',
            data={
                'name': 'foo',
                'cpus': 1,
                'memory': 2,
                'network': ['netuuid1'],
                'disk': ['8@cirros'],
                'ssh_key': 'sshkey',
                'user_data': "dXNlcmRhdGFiZWZvcmViYXNlNjQ="
            })

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_snapshot_instance(self, mock_request):
        client = apiclient.Client()
        client.snapshot_instance('notreallyauuid', all=True)

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances/notreallyauuid/snapshot',
            data={'all': True})

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_soft_reboot_instance(self, mock_request):
        client = apiclient.Client()
        client.reboot_instance('notreallyauuid')

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances/notreallyauuid/rebootsoft')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_hard_reboot_instance(self, mock_request):
        client = apiclient.Client()
        client.reboot_instance('notreallyauuid', hard=True)

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances/notreallyauuid/reboothard')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_power_off_instance(self, mock_request):
        client = apiclient.Client()
        client.power_off_instance('notreallyauuid')

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances/notreallyauuid/poweroff')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_power_on_instance(self, mock_request):
        client = apiclient.Client()
        client.power_on_instance('notreallyauuid')

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances/notreallyauuid/poweron')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_pause_instance(self, mock_request):
        client = apiclient.Client()
        client.pause_instance('notreallyauuid')

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances/notreallyauuid/pause')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_unpause_instance(self, mock_request):
        client = apiclient.Client()
        client.unpause_instance('notreallyauuid')

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/instances/notreallyauuid/unpause')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_delete_instance(self, mock_request):
        client = apiclient.Client()
        client.delete_instance('notreallyauuid')

        mock_request.assert_called_with(
            'DELETE', 'http://localhost:13000/instances/notreallyauuid')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_cache_image(self, mock_request):
        client = apiclient.Client()
        client.cache_image('imageurl')

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/images',
            data={'url': 'imageurl'})

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_get_networks(self, mock_request):
        client = apiclient.Client()
        client.get_networks()

        mock_request.assert_called_with(
            'GET', 'http://localhost:13000/networks')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_get_network(self, mock_request):
        client = apiclient.Client()
        client.get_network('notreallyauuid')

        mock_request.assert_called_with(
            'GET', 'http://localhost:13000/networks/notreallyauuid')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_delete_network(self, mock_request):
        client = apiclient.Client()
        client.delete_network('notreallyauuid')

        mock_request.assert_called_with(
            'DELETE', 'http://localhost:13000/networks/notreallyauuid')

    @mock.patch('shakenfist.client.apiclient._request_url')
    def test_allocate_network(self, mock_request):
        client = apiclient.Client()
        client.allocate_network('192.168.1.0/24', True, True)

        mock_request.assert_called_with(
            'POST', 'http://localhost:13000/networks',
            data={
                'netblock': '192.168.1.0/24',
                'provide_dhcp': True,
                'provide_nat': True
            })


class GetNodesMock():
    def json(self):
        return json.loads("""[
{
    "name": "sf-1.c.mikal-269605.internal",
    "ip": "10.128.15.213",
    "lastseen": "Mon, 13 Apr 2020 03:00:22 -0000"
},
{
    "name": "sf-2.c.mikal-269605.internal",
    "ip": "10.128.15.210",
    "lastseen": "Mon, 13 Apr 2020 03:04:17 -0000"
}
]
""")


class ApiClientGetNodesTestCase(testtools.TestCase):
    @mock.patch('shakenfist.client.apiclient._request_url',
                return_value=GetNodesMock())
    def test_get_nodes(self, mock_request):
        client = apiclient.Client()
        out = list(client.get_nodes())

        mock_request.assert_called_with(
            'GET', 'http://localhost:13000/nodes')
        assert(type(out[0]['lastseen']) == datetime.datetime)
