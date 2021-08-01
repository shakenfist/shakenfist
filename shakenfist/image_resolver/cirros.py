import os
import re
import requests

from shakenfist.config import config
from shakenfist import exceptions
from shakenfist.image_resolver import util as resolver_util
from shakenfist import logutil
from shakenfist import util


LOG, _ = logutil.setup(__name__)


def resolve(name):
    # Name is assumed to be in the form cirros or cirros:0.4.0
    if name == 'cirros':
        resp = requests.get(config.LISTING_URL_CIRROS,
                            allow_redirects=True,
                            headers={'User-Agent': util.get_user_agent()})
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch %s, status code %d'
                % (config.LISTING_URL_CIRROS, resp.status_code))

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

    url = config.DOWNLOAD_URL_CIRROS % {'vernum': vernum}

    checksum_url = config.CHECKSUM_URL_CIRROS % {'vernum': vernum}
    checksums = resolver_util.fetch_remote_checksum(checksum_url)
    checksum = checksums.get(os.path.basename(url))
    LOG.with_fields({
        'name': name,
        'url': url,
        'checksum': checksum
    }).info('Image resolved')

    return (url, checksum if checksum else None)
