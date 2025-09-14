import os
import pathlib
import stat
import sys
import time
import traceback
import uuid

import cpuinfo
import distro
import flask
from shakenfist_utilities import logs  # noreorder

from shakenfist import eventlog
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.util import concurrency as util_concurrency
# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


LOG, _ = logs.setup(__name__)


class RecordedOperation():
    def __init__(self, operation, relatedobject, threshold=0):
        self.operation = operation
        self.object = relatedobject
        self.threshold = threshold

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, traceback):
        duration = round(time.time() - self.start_time, 2)

        if duration < self.threshold:
            return

        message = f'{self.operation} finished'
        if exc_val:
            message += f' ({str(exc_type)} exception raised)'

        if self.object:
            eventlog.add_event_multi(
                EVENT_TYPE_STATUS, [self.object], message, duration)
        else:
            LOG.with_fields({
                'operation': self.operation,
                'duration': duration
            }).info(message)


def recorded_method(func):
    def wrapper(*args, **kwargs):
        with RecordedOperation(f'{func} execution', None):
            return func(*args, **kwargs)
    return wrapper


CACHED_VERSION = None


def get_version():
    global CACHED_VERSION

    if not CACHED_VERSION:
        CACHED_VERSION = get_version('shakenfist')
    return CACHED_VERSION


def get_user_agent():
    architecture = cpuinfo.get_cpu_info()
    return ('Mozilla/5.0 (%(distribution)s; %(vendor)s %(architecture)s) '
            'Shaken Fist/%(version)s'
            % {
                'distribution': distro.name(pretty=True),
                'architecture': architecture['arch_string_raw'],
                'vendor': architecture['vendor_id_raw'],
                'version': get_version()
            })


def ignore_exception(processname, e):
    msg = f'[Exception] Ignored error in {processname}: {e}'
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
    """Find if any of the possible extensions exists. """
    for extn in extensions:
        filename = f'{basename}.{extn}'
        if os.path.exists(filename):
            return filename
    return None


def link(source, destination):
    """Hard link a file, unless we have to symlink. """
    try:
        os.link(source, destination)
    except OSError:
        try:
            os.symlink(source, destination)
        except FileExistsError as e:
            # We should have checked if the destination existed before we were
            # called, so this implies we raced through just this method. Make
            # sure the destination points to the right place and if it does
            # just shrug and keep going.
            if os.path.realpath(destination) != source:
                raise e

    pathlib.Path(destination).touch(exist_ok=True)


def link_or_copy(source, destination):
    """Hard link a file, unless we have to copy it. """
    try:
        os.link(source, destination)
    except OSError:
        util_concurrency.execute(f'cp {source} {destination}')

    pathlib.Path(destination).touch(exist_ok=True)


def valid_uuid4(uuid_string):
    try:
        uuid.UUID(uuid_string, version=4)
    except ValueError:
        return False
    return True


def get_request_id():
    try:
        return flask.request.environ.get('FLASK_REQUEST_ID')
    except RuntimeError:
        return None
