import re
import requests

from shakenfist import config
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
        raise exceptions.HTTPError(
            'Failed to fetch %s, status code %d' % (UBUNTU_URL, resp.status_code))

    num_to_name = {}
    name_to_num = {}
    dir_re = re.compile(
        r'.*<a href="(.*)/">.*Ubuntu Server ([0-9]+\.[0-9]+).*')
    for line in resp.text.split('\n'):
        m = dir_re.match(line)
        if m:
            num_to_name[m.group(2)] = m.group(1)
            name_to_num[m.group(1)] = m.group(2)
    LOG.withField('versions', num_to_name).info('Found ubuntu versions')

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

    return (config.parsed.get('DOWNLOAD_URL_UBUNTU')
            % {'vernum': vernum,
               'vername': vername})
