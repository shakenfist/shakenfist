
import os
import requests

from shakenfist.config import config
from shakenfist import exceptions
from shakenfist.image_resolver import util as resolver_util
from shakenfist import logutil
from shakenfist import util

LOG, _ = logutil.setup(__name__)


def resolve(name):
    # Name is assumed to be in the form debian or debian:10
    if name.startswith('debian:'):
        try:
            _, vernum = name.split(':')
        except Exception:
            raise exceptions.VersionSpecificationError(
                'Cannot parse version: %s' % name)

        url = config.DOWNLOAD_URL_DEBIAN % {'vernum': vernum}
        checksum_url = config.CHECKSUM_URL_DEBIAN % {'vernum': vernum}
        resp = requests.head(url, allow_redirects=True,
                             headers={'User-Agent': util.get_user_agent()})
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch %s, status code %d' % (url, resp.status_code))

    else:
        found_any = False
        for vernum in range(9, 20):
            url = config.DOWNLOAD_URL_DEBIAN % {'vernum': vernum}
            resp = requests.head(url, allow_redirects=True,
                                 headers={'User-Agent': util.get_user_agent()})
            if resp.status_code != 200:
                if found_any:
                    vernum -= 1
                    break
            else:
                found_any = True

        url = config.DOWNLOAD_URL_DEBIAN % {'vernum': vernum}
        checksum_url = config.CHECKSUM_URL_DEBIAN % {'vernum': vernum}

    checksums = resolver_util.fetch_remote_checksum(checksum_url)
    checksum = checksums.get(os.path.basename(url))
    LOG.with_fields({
        'name': name,
        'url': url,
        'checksum': checksum
    }).info('Image resolved')

    return (url, checksum if checksum else None)
