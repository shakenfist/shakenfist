import os
import re
import requests

from shakenfist.config import config
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import util

CIRROS_URL = 'http://download.cirros-cloud.net/'


LOG, _ = logutil.setup(__name__)


def resolve(name):
    # Name is assumed to be in the form cirros or cirros:0.4.0
    resp = requests.get(CIRROS_URL,
                        headers={'User-Agent': util.get_user_agent()})
    if resp.status_code != 200:
        raise exceptions.HTTPError(
            'Failed to fetch http://download.cirros-cloud.net/, '
            'status code %d' % resp.status_code)

    if name == 'cirros':
        versions = []
        dir_re = re.compile(r'.*<a href="([0-9]+\.[0-9]+\.[0-9]+)/">.*/</a>.*')
        for line in resp.text.split('\n'):
            m = dir_re.match(line)
            if m:
                versions.append(m.group(1))
        LOG.with_field('versions', versions).info('Found cirros versions')
        vernum = versions[-1]
    else:
        try:
            _, vernum = name.split(':')
        except Exception:
            raise exceptions.VersionSpecificationError(
                'Cannot parse version: %s' % name)

    url = config.get('DOWNLOAD_URL_CIRROS') % {'vernum': vernum}

    checksum_url = CIRROS_URL + '/' + vernum + '/MD5SUMS'
    checksums = util.fetch_remote_checksum(checksum_url)
    checksum = checksums.get(os.path.basename(url))
    LOG.with_fields({
        'name': name,
        'url': url,
        'checksum': checksum
    }).info('Image resolved')
    return (url, checksum)
