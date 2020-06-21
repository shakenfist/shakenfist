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


class RequestMalformedException(APIException):
    pass


class ResourceCannotBeDeletedException(APIException):
    pass


class ResourceNotFoundException(APIException):
    pass


class ResourceInUseException(APIException):
    pass


class InsufficientResourcesException(APIException):
    pass


STATUS_CODES_TO_ERRORS = {
    400: RequestMalformedException,
    403: ResourceCannotBeDeletedException,
    404: ResourceNotFoundException,
    409: ResourceInUseException,
    507: InsufficientResourcesException,
}


class Client(object):
    def __init__(self, base_url='http://localhost:13000', verbose=False,
                 username=None, password=None):
        self.base_url = base_url
        self.username = username
        self.password = password

        self.cached_auth = None
        if verbose:
            LOG.setLevel(logging.DEBUG)

    def _request_url(self, method, url, data=None):
        if not self.cached_auth:
            r = requests.request('POST', self.base_url + '/auth',
                                 data=json.dumps(
                                     {'username': self.username,
                                      'password': self.password}),
                                 headers={'Content-Type': 'application/json'})
            self.cached_auth = 'Bearer %s' % r.json['access_token']

        h = {'Authorization': self.cached_auth}
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
            except Exception:
                LOG.debug('Text:\n    %s'
                          % ('\n    '.join(r.text.split('\n'))))
        LOG.debug('-------------------------------------------------------')

        if r.status_code in STATUS_CODES_TO_ERRORS:
            raise STATUS_CODES_TO_ERRORS[r.status_code](
                'API request failed', method, url, r.status_code, r.text)

        if r.status_code != 200:
            raise APIException(
                'API request failed', method, url, r.status_code, r.text)
        return r

    def get_instances(self, all=False):
        r = self._request_url('GET', self.base_url +
                              '/instances', data={'all': all})
        return r.json()

    def get_instance(self, instance_uuid):
        r = self._request_url('GET', self.base_url +
                              '/instances/' + instance_uuid)
        return r.json()

    def get_instance_interfaces(self, instance_uuid):
        r = self._request_url('GET', self.base_url + '/instances/' + instance_uuid +
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

        r = self._request_url('POST', self.base_url + '/instances',
                              data=body)
        return r.json()

    def snapshot_instance(self, instance_uuid, all=False):
        r = self._request_url('POST', self.base_url + '/instances/' + instance_uuid +
                              '/snapshot', data={'all': all})
        return r.json()

    def get_instance_snapshots(self, instance_uuid):
        r = self._request_url('GET', self.base_url + '/instances/' + instance_uuid +
                              '/snapshot')
        return r.json()

    def reboot_instance(self, instance_uuid, hard=False):
        style = 'soft'
        if hard:
            style = 'hard'
        r = self._request_url('POST', self.base_url + '/instances/' + instance_uuid +
                              '/reboot' + style)
        return r.json()

    def power_off_instance(self, instance_uuid):
        r = self._request_url('POST', self.base_url + '/instances/' + instance_uuid +
                              '/poweroff')
        return r.json()

    def power_on_instance(self, instance_uuid):
        r = self._request_url('POST', self.base_url + '/instances/' + instance_uuid +
                              '/poweron')
        return r.json()

    def pause_instance(self, instance_uuid):
        r = self._request_url('POST', self.base_url + '/instances/' + instance_uuid +
                              '/pause')
        return r.json()

    def unpause_instance(self, instance_uuid):
        r = self._request_url('POST', self.base_url + '/instances/' + instance_uuid +
                              '/unpause')
        return r.json()

    def delete_instance(self, instance_uuid):
        r = self._request_url('DELETE', self.base_url +
                              '/instances/' + instance_uuid)
        return r.json()

    def get_instance_events(self, instance_uuid):
        r = self._request_url('GET', self.base_url +
                              '/instances/' + instance_uuid + '/events')
        return r.json()

    def cache_image(self, image_url):
        r = self._request_url('POST', self.base_url + '/images',
                              data={'url': image_url})
        return r.json()

    def get_networks(self, all=False):
        r = self._request_url('GET', self.base_url +
                              '/networks', data={'all': all})
        return r.json()

    def get_network(self, network_uuid):
        r = self._request_url('GET', self.base_url +
                              '/networks/' + network_uuid)
        return r.json()

    def delete_network(self, network_uuid):
        r = self._request_url('DELETE', self.base_url +
                              '/networks/' + network_uuid)
        return r.json()

    def get_network_events(self, instance_uuid):
        r = self._request_url('GET', self.base_url +
                              '/networks/' + instance_uuid + '/events')
        return r.json()

    def allocate_network(self, netblock, provide_dhcp, provide_nat, name):
        r = self._request_url('POST', self.base_url + '/networks',
                              data={
                                  'netblock': netblock,
                                  'provide_dhcp': provide_dhcp,
                                  'provide_nat': provide_nat,
                                  'name': name
                              })
        return r.json()

    def get_nodes(self):
        r = self._request_url('GET', self.base_url + '/nodes')
        for n in r.json():
            n['lastseen'] = parsedate_to_datetime(n['lastseen'])
            yield n

    def float_interface(self, interface_uuid):
        r = self._request_url('POST', self.base_url + '/interfaces/' + interface_uuid +
                              '/float')
        return r.json()

    def defloat_interface(self, interface_uuid):
        r = self._request_url('POST', self.base_url + '/interfaces/' + interface_uuid +
                              '/defloat')
        return r.json()
