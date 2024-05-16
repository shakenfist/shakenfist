import cpuinfo
import distro
import os
import pathlib
from pbr.version import VersionInfo
from shakenfist_utilities import logs
import stat
import sys
import time
import traceback
import uuid


# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist import eventlog


LOG, _ = logs.setup(__name__)


class RecordedOperation():
    def __init__(self, operation, relatedobject, threshold=0):
        self.operation = operation
        self.object = relatedobject
        self.threshold = threshold

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, *args):
        duration = time.time() - self.start_time

        if duration < self.threshold:
            return

        object_type, object_uuid = self.unique_label()
        if object_uuid:
            if object_type:
                eventlog.add_event(EVENT_TYPE_STATUS, object_type, object_uuid,
                                   '%s complete' % self.operation, duration)
            else:
                LOG.with_fields({
                    'label': self.object,
                    'duration': duration}).info('Finish %s', self.operation)

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


CACHED_VERSION = None


def get_version():
    global CACHED_VERSION

    if not CACHED_VERSION:
        CACHED_VERSION = VersionInfo('shakenfist').version_string()
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
    msg = '[Exception] Ignored error in {}: {}'.format(processname, e)
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
        filename = '{}.{}'.format(basename, extn)
        if os.path.exists(filename):
            return filename
    return None


def link(source, destination):
    """ Hardlink a file, unless we have to symlink. """
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


def valid_uuid4(uuid_string):
    try:
        uuid.UUID(uuid_string, version=4)
    except ValueError:
        return False
    return True
