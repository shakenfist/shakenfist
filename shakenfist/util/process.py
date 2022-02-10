import multiprocessing
from oslo_concurrency import processutils
import psutil
import time

# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, logutils, and specific
# other util modules.
from shakenfist import db
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


def _lock_refresher(locks):
    while True:
        db.refresh_locks(locks)
        time.sleep(10)


# Mid-range best effort, equivalent to not specifying a value
PRIORITY_NORMAL = (2, 4)
PRIORITY_LOW = (2, 7)
PRIORITY_HIGH = (2, 0)


def execute(locks, command, check_exit_code=[0], env_variables=None,
            namespace=None, iopriority=None, cwd=None,
            suppress_command_logging=False):
    if namespace:
        command = 'ip netns exec %s %s' % (namespace, command)

    if iopriority:
        current_iopriority = psutil.Process().ionice()
        if current_iopriority != iopriority:
            command = 'ionice -c %d -n %d %s' % (iopriority[0], iopriority[1],
                                                 command)

    if not suppress_command_logging:
        LOG.info('Executing %s with locks %s', command, locks)

    if not locks:
        return processutils.execute(
            command, check_exit_code=check_exit_code,
            env_variables=env_variables, shell=True, cwd=cwd)

    else:
        p = multiprocessing.Process(
            target=_lock_refresher, args=(locks,))
        p.start()

        try:
            return processutils.execute(
                command, check_exit_code=check_exit_code,
                env_variables=env_variables, shell=True)
        finally:
            p.kill()
            p.join()
