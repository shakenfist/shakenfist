import re
import requests

from shakenfist import config
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
        LOG.withField('versions', versions).info('Found cirros versions')
        vernum = versions[-1]
    else:
        try:
            # Name is assumed to be in the form cirros:0.4.0
            _, vernum = name.split(':')
        except Exception:
            raise exceptions.VersionSpecificationError(
                'Cannot parse version: %s' % name)

    return (config.parsed.get('DOWNLOAD_URL_CIRROS')
            % {'vernum': vernum})
