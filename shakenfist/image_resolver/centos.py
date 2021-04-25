import os
import re
import requests

from shakenfist.config import config
from shakenfist import exceptions
from shakenfist.image_resolver import util as resolver_util
from shakenfist import logutil
from shakenfist import util

URLS = {
    'centos:6': config.get('LISTING_URL_CENTOS6'),
    'centos:7': config.get('LISTING_URL_CENTOS7'),
    'centos:8': config.get('LISTING_URL_CENTOS8')
}


LOG, _ = logutil.setup(__name__)


def resolve(name):
    # Name is assumed to be in the form centos or centos:[678]
    if name == 'centos':
        name = 'centos:8'

    resp = requests.get(URLS[name],
                        headers={'User-Agent': util.get_user_agent()})
    if resp.status_code != 200:
        raise exceptions.HTTPError(
            'Failed to fetch %s, status code %d'
            % (URLS[name], resp.status_code))

    images = {}
    index = resp.text
    LINE_RE = re.compile(r'.*a href="([^"]*GenericCloud-[^"]*\.qcow2)".*')
    for line in index.split('\n'):
        m = LINE_RE.match(line)
        if m:
            image = m.group(1)
            version = image.split('-')[-1].split('.')[0].split('_')[0]
            if len(version) < 4:
                continue
            if len(version) == 4:
                version = '20%s01' % version
            images[version] = image

    newest = sorted(images)[-1]
    url = '%s/%s' % (URLS[name], images[newest])
    checksum_url = URLS[name] + '/sha256sum.txt'

    checksums = resolver_util.fetch_remote_checksum(checksum_url)
    checksum = checksums.get(os.path.basename(url))
    LOG.with_fields({
        'name': name,
        'url': url,
        'checksum': checksum
    }).info('Image resolved')

    if checksum:
        return (url, 'sha256', checksum)
    else:
        return (url, None, None)
