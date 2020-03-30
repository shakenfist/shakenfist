from email.utils import parsedate_to_datetime
import requests


BASE_URL = 'http://localhost:13000'


class APIException(Exception):
    pass


def _request_url(method, url, data=None):
    r = requests.request(method, url, data=data)
    if r.status_code != 200:
        raise APIException('Failed to get %s (status %s): %s'
                           % (url, r.status_code, r.text))
    return r


def get_instances():
    r = _request_url('GET', BASE_URL + '/instances')
    for n in r.json():
        yield n


def get_instance(uuid):
    r = _request_url('GET', BASE_URL + '/instances/' + uuid)
    return r.json()


def get_instance_interfaces(uuid):
    r = _request_url('GET', BASE_URL + '/instances/' + uuid +
                     '/interfaces')
    return r.json()


def create_instance(network, name, cpus, memory, disk, ssh_key):
    r = _request_url('POST', BASE_URL + '/instances',
                     data={
                         'network': network,
                         'name': name,
                         'cpus': cpus,
                         'memory': memory,
                         'disk': ' '.join(disk),
                         'ssh_key': ssh_key
                     })
    return r.json()


def snapshot_instance(uuid, all=False):
    r = _request_url('POST', BASE_URL + '/instances/' + uuid +
                     '/snapshot', data={'all': all})
    return r.json()


def delete_instance(uuid):
    r = _request_url('DELETE', BASE_URL + '/instances/' + uuid)
    return r.json()


def cache_image(image_url):
    r = _request_url('POST', BASE_URL + '/images',
                     data={'url': image_url})
    return r.json()


def get_networks():
    r = _request_url('GET', BASE_URL + '/networks')
    for n in r.json():
        yield n


def get_network(uuid):
    r = _request_url('GET', BASE_URL + '/networks/' + uuid)
    return r.json()


def delete_network(uuid):
    r = _request_url('DELETE', BASE_URL + '/networks/' + uuid)
    return r.json()


def allocate_network(netblock, provide_dhcp, provide_nat):
    r = _request_url('POST', BASE_URL + '/networks',
                     data={
                         'netblock': netblock,
                         'provide_dhcp': provide_dhcp,
                         'provide_nat': provide_nat
                     })
    return r.json()


def get_nodes():
    r = _request_url('GET', BASE_URL + '/nodes')
    for n in r.json():
        n['lastseen'] = parsedate_to_datetime(n['lastseen'])
        yield n
