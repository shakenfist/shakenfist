import re
import requests

from shakenfist.config import config
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import util

# The official Ubuntu download URL 'https://cloud-images.ubuntu.com' is unreliable.
# We try it first, but then try an alternative location on failure.
UBUNTU_URL = 'https://cloud-images.ubuntu.com'


LOG, _ = logutil.setup(__name__)


def resolve(name):
    resp = requests.get(UBUNTU_URL,
                        headers={'User-Agent': util.get_user_agent()})
    if resp.status_code != 200:
        raise exceptions.HTTPError('Failed to fetch %s, status code %d'
                                   % (UBUNTU_URL, resp.status_code))

    num_to_name = {}
    name_to_num = {}
    dir_re = re.compile(
        r'.*<a href="(.*)/">.*Ubuntu Server ([0-9]+\.[0-9]+).*')
    for line in resp.text.split('\n'):
        m = dir_re.match(line)
        if m:
            num_to_name[m.group(2)] = m.group(1)
            name_to_num[m.group(1)] = m.group(2)
    LOG.with_field('versions', num_to_name).info('Found ubuntu versions')

    vernum = None
    vername = None

    if name == 'ubuntu':
        vernum = sorted(num_to_name.keys())[-1]
        vername = num_to_name[vernum]
    else:
        try:
            # Name is assumed to be in the form ubuntu:18.04 or ubuntu:bionic
            _, version = name.split(':')
            if version in num_to_name:
                vernum = version
                vername = num_to_name[version]
            else:
                vername = version
                vernum = name_to_num[version]
        except Exception:
            raise exceptions.VersionSpecificationError(
                'Cannot parse version: %s' % name)

    url = (config.get('DOWNLOAD_URL_UBUNTU') % {'vernum': vernum,
                                                'vername': vername})
    log = LOG.with_field('url', url)

    # Retrieve check sum file
    checksum_url = UBUNTU_URL + '/' + vername + '/current/MD5SUMS'
    resp = requests.get(checksum_url,
                        headers={'User-Agent': util.get_user_agent()})
    if resp.status_code != 200:
        raise exceptions.HTTPError('Failed to fetch %s, status code %d' % (
                                   checksum_url, resp.status_code))

    sum_re = re.compile(r'^([0-9a-f]+) .*'+vername+'-server-cloudimg-amd64.img')
    checksum = None
    for line in resp.text.split('\n'):
        m = sum_re.match(line)
        if m:
            checksum = m.group(1)
            break
    if not checksum_url:
        log.warning('Did not find checksum')
    checksum = checksum.strip()

    log.with_field('checksum', checksum).info('Checksum check')
    return (url, checksum)
