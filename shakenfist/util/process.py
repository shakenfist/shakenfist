import multiprocessing
import time

import psutil
from oslo_concurrency import processutils
from shakenfist_utilities import logs

from shakenfist import etcd
# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


LOG, _ = logs.setup(__name__)


def _lock_refresher(locks):
    while True:
        etcd.refresh_locks(locks)
        time.sleep(10)


# Mid-range best effort, equivalent to not specifying a value
PRIORITY_NORMAL = (2, 4)
PRIORITY_LOW = (2, 7)
PRIORITY_HIGH = (2, 0)


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
        return processutils.execute(
            command, check_exit_code=check_exit_code,
            env_variables=env_variables, shell=True, cwd=cwd)

    else:
        p = fork(_lock_refresher, [locks], 'lock-refresher')

        try:
            return processutils.execute(
                command, check_exit_code=check_exit_code,
                env_variables=env_variables, shell=True)
        finally:
            p.kill()
            p.join()


def _process_start_shim(*args):
    etcd.reset_client()
    args[0](*args[1:])


def fork(process_callback, args, process_name):
    # We need to reset the etcd thread local cache before we start running a
    # subprocess.

    shim_args = [process_callback]
    shim_args.extend(args)

    p = multiprocessing.Process(
        target=_process_start_shim, args=shim_args, name=process_name)
    p.start()
    return p
