
import os
import requests

from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import util

DEBIAN_URL = ('https://cloud.debian.org/images/cloud/OpenStack/current-%(version)s/'
              'debian-%(version)s-openstack-amd64.qcow2')
CHECKSUM_URL = 'https://cloud.debian.org/images/cloud/OpenStack/current-%(version)s/MD5SUMS'


LOG, _ = logutil.setup(__name__)


def resolve(name):
    # Name is assumed to be in the form debian or debian:10
    if name.startswith('debian:'):
        try:
            _, vernum = name.split(':')
        except Exception:
            raise exceptions.VersionSpecificationError(
                'Cannot parse version: %s' % name)

        url = DEBIAN_URL % {'version': vernum}
        checksum_url = CHECKSUM_URL % {'version': vernum}
        resp = requests.head(url,
                             headers={'User-Agent': util.get_user_agent()})
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch %s, status code %d' % (url, resp.status_code))

    else:
        found_any = False
        for vernum in range(9, 20):
            url = DEBIAN_URL % {'version': vernum}
            resp = requests.head(url,
                                 headers={'User-Agent': util.get_user_agent()})
            if resp.status_code != 200:
                if found_any:
                    vernum -= 1
                    break
            else:
                found_any = True

        url = DEBIAN_URL % {'version': vernum}
        checksum_url = CHECKSUM_URL % {'version': vernum}

    checksums = util.fetch_remote_checksum(checksum_url)
    checksum = checksums.get(os.path.basename(url))
    LOG.with_fields({
        'name': name,
        'url': url,
        'checksum': checksum
    }).info('Image resolved')
    return (url, checksum)
