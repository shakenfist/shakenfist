import os
import re
import requests

from shakenfist.config import config
from shakenfist import exceptions
from shakenfist.image_resolver import util as resolver_util
from shakenfist import logutil
from shakenfist.util import general as util_general

LOG, _ = logutil.setup(__name__)


def resolve(name):
    # Name is assumed to be in the form ubuntu, ubuntu:18.04, or ubuntu:bionic
    resp = requests.get(config.LISTING_URL_UBUNTU, allow_redirects=True,
                        headers={'User-Agent': util_general.get_user_agent()})
    if resp.status_code != 200:
        raise exceptions.HTTPError('Failed to fetch %s, status code %d'
                                   % (config.LISTING_URL_UBUNTU, resp.status_code))

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

    url = (config.DOWNLOAD_URL_UBUNTU % {'vernum': vernum,
                                         'vername': vername})

    checksum_url = config.CHECKSUM_URL_UBUNTU % {'vername': vername}
    checksums = resolver_util.fetch_remote_checksum(checksum_url)
    checksum = checksums.get('*' + os.path.basename(url))
    LOG.with_fields({
        'name': name,
        'url': url,
        'checksum': checksum
    }).info('Image resolved')

    if checksum:
        return url, checksum, 'md5'
    else:
        return url, None, None
