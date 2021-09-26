import json
import os
from pbr.version import VersionInfo
import requests
import secrets
import stat
import string
import sys
import time
import traceback

# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, logutils, and specific
# other util modules.
from shakenfist import db
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


class RecordedOperation():
    def __init__(self, operation, relatedobject):
        self.operation = operation
        self.object = relatedobject

    def __enter__(self):
        self.start_time = time.time()
        object_type, object_uuid = self.unique_label()
        if object_type and object_uuid:
            db.add_event(object_type, object_uuid,
                         self.operation, 'start', None, None)
        return self

    def __exit__(self, *args):
        duration = time.time() - self.start_time
        log = LOG
        object_type, object_uuid = self.unique_label()
        if object_uuid:
            if object_type:
                db.add_event(object_type, object_uuid,
                             self.operation, 'finish', duration, None)
                log = LOG.with_object(self.object)
            else:
                log = LOG.with_field({'label', self.object})
        log.with_field('duration', duration).info('Finish %s', self.operation)

    def unique_label(self):
        if self.object:
            if isinstance(self.object, str):
                object_type = None
                object_uuid = self.object
            else:
                object_type, object_uuid = self.object.unique_label()
        else:
            object_type = None
            object_uuid = None

        return object_type, object_uuid


def get_api_token(base_url, namespace='system'):
    with db.get_lock('namespace', None, namespace, op='Get API token'):
        auth_url = base_url + '/auth'
        LOG.info('Fetching %s auth token from %s', namespace, auth_url)
        ns = db.get_namespace(namespace)
        if 'service_key' in ns:
            key = ns['service_key']
        else:
            key = ''.join(secrets.choice(string.ascii_lowercase)
                          for i in range(50))
            ns['service_key'] = key
            db.persist_namespace(namespace, ns)

    r = requests.request('POST', auth_url,
                         data=json.dumps({
                             'namespace': namespace,
                             'key': key
                         }),
                         headers={'Content-Type': 'application/json',
                                  'User-Agent': get_user_agent()})
    if r.status_code != 200:
        raise Exception('Unauthorized')
    return 'Bearer %s' % r.json()['access_token']


CACHED_VERSION = None


def get_version():
    global CACHED_VERSION

    if not CACHED_VERSION:
        CACHED_VERSION = VersionInfo('shakenfist').version_string()
    return CACHED_VERSION


def get_user_agent():
    return 'Mozilla/5.0 (Ubuntu; Linux x86_64) Shaken Fist/%s' % get_version()


def ignore_exception(processname, e):
    msg = '[Exception] Ignored error in %s: %s' % (processname, e)
    _, _, tb = sys.exc_info()
    if tb:
        msg += '\n%s' % traceback.format_exc()

    LOG.error(msg)


def noneish(value):
    if not value:
        return True
    if value.lower() == 'none':
        return True
    return False


def stat_log_fields(path):
    st = os.stat(path)
    return {
        'size': st.st_size,
        'mode': stat.filemode(st.st_mode),
        'owner': st.st_uid,
        'group': st.st_gid,
    }


def file_permutation_exists(basename, extensions):
    """ Find if any of the possible extensions exists. """
    for extn in extensions:
        filename = '%s.%s' % (basename, extn)
        if os.path.exists(filename):
            return filename
    return None


def link(source, destination):
    """ Hardlink a file, unless we have to symlink. """
    try:
        os.link(source, destination)
    except OSError:
        os.symlink(source, destination)
