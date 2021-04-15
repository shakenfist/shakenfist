import os
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
    # Name is assumed to be in the form ubuntu, ubuntu:18.04, or ubuntu:bionic
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

    checksum_url = UBUNTU_URL + '/' + vername + '/current/MD5SUMS'
    checksums = util.fetch_remote_checksum(checksum_url)
    checksum = checksums.get('*' + os.path.basename(url))
    LOG.with_fields({
        'name': name,
        'url': url,
        'checksum': checksum
    }).info('Image resolved')
    return (url, checksum)
