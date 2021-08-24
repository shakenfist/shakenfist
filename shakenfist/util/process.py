import multiprocessing
from oslo_concurrency import processutils
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


def execute(locks, command, check_exit_code=[0], env_variables=None):
    LOG.info('Executing %s with locks %s', command, locks)

    if not locks:
        return processutils.execute(
            command, check_exit_code=check_exit_code,
            env_variables=env_variables, shell=True)

    else:
        p = multiprocessing.Process(
            target=_lock_refresher, args=(locks,))
        p.start()

        try:
            return processutils.execute(
                command, check_exit_code=check_exit_code,
                env_variables=env_variables, shell=True)
        finally:
            p.terminate()
            p.join()
