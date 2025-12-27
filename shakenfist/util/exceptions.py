
import fcntl
import hashlib
import json
import os
import sys
import threading
import time
import traceback

from shakenfist_utilities import logs  # noreorder


LOG, _ = logs.setup(__name__)


def ignore_exception(processname, e):
    msg = f'[Exception] Ignored error in {processname}: {e}'
    exc_type, exc_value, exc_tb = sys.exc_info()
    if exc_tb:
        msg += '\n%s' % traceback.format_exc(exc_type, exc_value, exc_tb)
        record_exception(exc_type, exc_value, exc_tb)
    LOG.error(msg)


def record_exception(exc_type, exc_value, exc_tb):
    traceback_str = '\n%s' % traceback.format_exc(exc_type, exc_value, exc_tb)

    h = hashlib.sha256(traceback_str).hexdigest()[-8:]
    os.makedirs(os.path.join('/srv/shakenfist/exceptions'), exist_ok=True)
    
    flags = os.O_RDWR | os.O_CREAT
    fd = os.open(f'/srv/shakenfist/exceptions/{h}.json', flags, 0o644)
    
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = {}

        size = os.fstat(fd).st_size
        if size > 0:
            d = os.read(fd, size)
            if d:
                data = json.loads(d)
            os.lseek(fd, 0, os.SEEK_SET)

        data['traceback'] = traceback_str
        data['count'] = data.get('count', 0) + 1

        if 'events' not in data:
            data['events'] = []
        data['events'].append(time.time())

        os.write(fd, json.dumps(data, indent=4, sort_keys=True))

    except:
        # Ignore the exception here because we're already on the error path
        os.close(fd)


_original_excepthook = sys.excepthook


def _tracking_excepthook(exc_type, exc_value, exc_tb):
    record_exception(exc_type, exc_value, exc_tb)
    _original_excepthook(exc_type, exc_value, exc_tb)


def _thread_excepthook(args):
    record_exception(args.exc_type, args.exc_value, args.exc_traceback)


def install_exception_tracking():
    sys.excepthook = _tracking_excepthook
    threading.excepthook = _thread_excepthook