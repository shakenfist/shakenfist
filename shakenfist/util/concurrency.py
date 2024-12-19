import os
import threading
import time

import psutil
from oslo_concurrency import processutils
from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist.util import callstack as util_callstack
# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


LOG, _ = logs.setup(__name__)


class Job:
    def __init__(self):
        self.exit = threading.Event()

    def run(self):
        LOG.debug('Starting job execution')
        self.execute()
        LOG.debug('Finished job execution')


class LockRefresherJob(Job):
    def __init__(self, locks):
        super().__init__()
        self.locks = locks

    def execute(self):
        etcd.reset_client()
        last_refresh = 0
        while not self.exit.is_set():
            if time.time() - last_refresh > 9:
                etcd.refresh_locks(self.locks)
                last_refresh = time.time()
            time.sleep(0.2)


# Mid-range best effort, equivalent to not specifying a value
PRIORITY_NORMAL = (2, 4)
PRIORITY_LOW = (2, 7)
PRIORITY_HIGH = (2, 0)


def _log_results(stdout, stderr, execution_time):
    fields = {
        'stdout': stdout,
        'stderr': stderr,
        'execution_time': f'{execution_time:.2f}'
    }

    try:
        LOG.with_fields(fields).debug('Command output')
    except OSError:
        # This happens when the log message is too long...
        if len(stdout) > 512:
            fields['stdout'] = stdout[:512] + '...'
        if len(stderr) > 512:
            fields['stderr'] = stderr[:512] + '...'
        LOG.with_fields(fields).debug('Command output (truncated)')


def _is_gunicorn():
    return 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '')


def execute(locks, command, check_exit_code=[0], env_variables=None,
            namespace=None, iopriority=None, cwd=None,
            suppress_command_logging=False):
    if namespace:
        command = f'ip netns exec {namespace} {command}'

    if iopriority:
        current_iopriority = psutil.Process().ionice()
        if current_iopriority != iopriority:
            command = 'ionice -c %d -n %d %s' % (iopriority[0], iopriority[1],
                                                 command)

    if not suppress_command_logging:
        LOG.info('Executing %s with locks %s', command, locks)

    if not locks:
        start_time = time.time()
        stdout, stderr = processutils.execute(
            command, check_exit_code=check_exit_code,
            env_variables=env_variables, shell=True, cwd=cwd)
        if not suppress_command_logging:
            _log_results(stdout, stderr, time.time() - start_time)
        return stdout, stderr

    else:
        if _is_gunicorn():
            caller = util_callstack.generate_traceback()
            LOG.warning(
                f'Lock refreshers should not be used under gunicorn: {caller}')

        refresher = LockRefresherJob(locks)
        refresher_thread = threading.Thread(
            target=refresher.run, daemon=True, name='lock-refresher')
        refresher_thread.start()

        try:
            start_time = time.time()
            stdout, stderr = processutils.execute(
                command, check_exit_code=check_exit_code,
                env_variables=env_variables, shell=True)
            if not suppress_command_logging:
                _log_results(stdout, stderr, time.time() - start_time)
            return stdout, stderr
        finally:
            refresher.exit.set()
            refresher_thread.join(1.0)
            if refresher_thread.is_alive():
                LOG.error('Failed to terminate lock refresher thread with '
                          f'ident {refresher_thread.ident}')
