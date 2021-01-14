import re
import requests

from shakenfist.config import config
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import util

CIRROS_URL = 'http://download.cirros-cloud.net/'


LOG, _ = logutil.setup(__name__)


def resolve(name):
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
            # Name is assumed to be in the form cirros:0.4.0
            _, vernum = name.split(':')
        except Exception:
            raise exceptions.VersionSpecificationError(
                'Cannot parse version: %s' % name)

    url = config.get('DOWNLOAD_URL_CIRROS') % {'vernum': vernum}
    log = LOG.with_field('url', url)

    # Retrieve check sum file
    checksum_url = CIRROS_URL + '/' + vernum + '/MD5SUMS'
    resp = requests.get(checksum_url,
                        headers={'User-Agent': util.get_user_agent()})
    log.with_field('checksum_url', checksum_url
                   ).with_field('resp', resp).debug("Checksum request response")
    if resp.status_code != 200:
        # Cirros does not always have a checksum file available
        log.info('Unable to retrieve MD5SUMS for cirros image')
        return url, None

    sum_re = re.compile(r'^([0-9a-f]+) .*'+'cirros-'+vernum+'-x86_64-disk.img')
    checksum = None
    for line in resp.text.split('\n'):
        m = sum_re.match(line)
        if m:
            checksum = m.group(1)
            break
    if not checksum_url:
        log.warning('Did not find checksum')

    log.with_field('checksum', checksum).info('Checksum retrieval')

    return (url, checksum)
