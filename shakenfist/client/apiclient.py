import base64
from email.utils import parsedate_to_datetime
import json
import logging
import requests


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)


class APIException(Exception):
    def __init__(self, message, method, url, status_code, text):
        self.message = message
        self.method = method
        self.url = url
        self.status_code = status_code
        self.text = text


def _request_url(method, url, data=None):
    h = {}
    if data:
        h['Content-Type'] = 'application/json'
    r = requests.request(method, url, data=json.dumps(data), headers=h)

    LOG.debug('-------------------------------------------------------')
    LOG.debug('API client requested: %s %s' % (method, url))
    if data:
        LOG.debug('Data:\n    %s'
                  % ('\n    '.join(json.dumps(data,
                                              indent=4,
                                              sort_keys=True).split('\n'))))
    LOG.debug('API client response: code = %s' % r.status_code)
    if r.text:
        try:
            LOG.debug('Data:\n    %s'
                      % ('\n    '.join(json.dumps(json.loads(r.text),
                                                  indent=4,
                                                  sort_keys=True).split('\n'))))
        except:
            LOG.debug('Text:\n    %s'
                      % ('\n    '.join(r.text.split('\n'))))
    LOG.debug('-------------------------------------------------------')

    if r.status_code != 200:
        raise APIException(
            'API request failed', method, url, r.status_code, r.text)
    return r


class Client(object):
    def __init__(self, base_url='http://localhost:13000', verbose=False):
        self.base_url = base_url
        if verbose:
            LOG.setLevel(logging.DEBUG)

    def get_instances(self):
        r = _request_url('GET', self.base_url + '/instances')
        return r.json()

    def get_instance(self, instance_uuid):
        r = _request_url('GET', self.base_url + '/instances/' + instance_uuid)
        return r.json()

    def get_instance_interfaces(self, instance_uuid):
        r = _request_url('GET', self.base_url + '/instances/' + instance_uuid +
                         '/interfaces')
        return r.json()

    def create_instance(self, name, cpus, memory, network, disk, sshkey, userdata,
                        force_placement=None):
        body = {
            'name': name,
            'cpus': cpus,
            'memory': memory,
            'network': network,
            'disk': disk,
            'ssh_key': sshkey,
            'user_data': userdata
        }

        if force_placement:
            body['placed_on'] = force_placement

        r = _request_url('POST', self.base_url + '/instances',
                         data=body)
        return r.json()

    def snapshot_instance(self, instance_uuid, all=False):
        r = _request_url('POST', self.base_url + '/instances/' + instance_uuid +
                         '/snapshot', data={'all': all})
        return r.json()

    def get_instance_snapshots(self, instance_uuid):
        r = _request_url('GET', self.base_url + '/instances/' + instance_uuid +
                         '/snapshot')
        return r.json()

    def reboot_instance(self, instance_uuid, hard=False):
        style = 'soft'
        if hard:
            style = 'hard'
        r = _request_url('POST', self.base_url + '/instances/' + instance_uuid +
                         '/reboot' + style)
        return r.json()

    def power_off_instance(self, instance_uuid):
        r = _request_url('POST', self.base_url + '/instances/' + instance_uuid +
                         '/poweroff')
        return r.json()

    def power_on_instance(self, instance_uuid):
        r = _request_url('POST', self.base_url + '/instances/' + instance_uuid +
                         '/poweron')
        return r.json()

    def pause_instance(self, instance_uuid):
        r = _request_url('POST', self.base_url + '/instances/' + instance_uuid +
                         '/pause')
        return r.json()

    def unpause_instance(self, instance_uuid):
        r = _request_url('POST', self.base_url + '/instances/' + instance_uuid +
                         '/unpause')
        return r.json()

    def delete_instance(self, instance_uuid):
        r = _request_url('DELETE', self.base_url +
                         '/instances/' + instance_uuid)
        return r.json()

    def get_instance_events(self, instance_uuid):
        r = _request_url('GET', self.base_url +
                         '/instances/' + instance_uuid + '/events')
        return r.json()

    def cache_image(self, image_url):
        r = _request_url('POST', self.base_url + '/images',
                         data={'url': image_url})
        return r.json()

    def get_networks(self):
        r = _request_url('GET', self.base_url + '/networks')
        return r.json()

    def get_network(self, network_uuid):
        r = _request_url('GET', self.base_url + '/networks/' + network_uuid)
        return r.json()

    def delete_network(self, network_uuid):
        r = _request_url('DELETE', self.base_url + '/networks/' + network_uuid)
        return r.json()

    def get_network_events(self, instance_uuid):
        r = _request_url('GET', self.base_url +
                         '/networks/' + instance_uuid + '/events')
        return r.json()

    def allocate_network(self, netblock, provide_dhcp, provide_nat, name):
        r = _request_url('POST', self.base_url + '/networks',
                         data={
                             'netblock': netblock,
                             'provide_dhcp': provide_dhcp,
                             'provide_nat': provide_nat,
                             'name': name
                         })
        return r.json()

    def get_nodes(self):
        r = _request_url('GET', self.base_url + '/nodes')
        for n in r.json():
            n['lastseen'] = parsedate_to_datetime(n['lastseen'])
            yield n

    def float_interface(self, interface_uuid):
        r = _request_url('POST', self.base_url + '/interfaces/' + interface_uuid +
                         '/float')
        return r.json()

    def defloat_interface(self, interface_uuid):
        r = _request_url('POST', self.base_url + '/interfaces/' + interface_uuid +
                         '/defloat')
        return r.json()
