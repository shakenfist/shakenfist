import os
import shutil
import signal
import time
from typing import Any
from typing import Optional
from typing import Union
from uuid import UUID

import jinja2
import psutil
from pydantic import BaseModel
from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.schema.object_types import ObjectType


LOG, _ = logs.setup(__name__)


# How long _send_signal() waits for a process it has sent SIGKILL to. The
# managed executables are started through sf-privexec and daemonise, so they
# are never children of the process signalling them, and waitpid() on one
# returns at once. dnsmasq's hard restart then started the replacement while
# the old process still held its sockets, and the new one failed to bind its
# DHCP port 13ms after the kill (#4472).
KILL_WAIT_TIMEOUT = 5

# How often _wait_for_exit()'s fallback rechecks a pid when psutil's own wait
# could not be used.
KILL_POLL_INTERVAL = 0.1


class ManagedExecutable(dbo):
    object_type = ObjectType.UNKNOWN_MANAGED_EXECUTABLE

    state_targets: dict[str | None, tuple[str, ...]] = {  # type: ignore[assignment]
        None: (dbo.STATE_CREATED,),
        dbo.STATE_CREATED: (dbo.STATE_DELETED,),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, data: Union[BaseModel, dict[str, Any]]) -> None:
        """Initialize a ManagedExecutable.

        Args:
            data: Either a Pydantic model (for MariaDB storage) or a dict
                  (for backward compatibility with etcd storage).
        """
        # Support both Pydantic models and dicts
        if isinstance(data, BaseModel):
            # Pydantic model - extract fields
            super().__init__(data.uuid, data.version)  # type: ignore[attr-defined]
            self.__namespace: str = data.namespace  # type: ignore[attr-defined]
            self.__owner_type: ObjectType = data.owner_type  # type: ignore[attr-defined]
            self.__owner_uuid: UUID = data.owner_uuid  # type: ignore[attr-defined]
        else:
            # Dict (legacy etcd path)
            super().__init__(data['uuid'], data.get('version'))
            self.__namespace = data['namespace']
            self.__owner_type = data['owner_type']
            self.__owner_uuid = data['owner_uuid']

        self.__config_templates: dict[str, jinja2.Template] = {}
        self.__config_dir = os.path.join(config.STORAGE_PATH, self.object_type,
                                         str(self.uuid))

    # Static values
    @property
    def namespace(self) -> str:
        return self.__namespace

    @property
    def owner_type(self) -> ObjectType:
        return self.__owner_type

    @property
    def owner_uuid(self) -> UUID:
        return self.__owner_uuid

    @property
    def config_directory(self) -> str:
        return self.__config_dir

    @config_directory.setter
    def config_directory(self, value: str) -> None:
        self.__config_dir = value

    def __str__(self) -> str:
        return (f'{self.object_type}({self.uuid}, as owned by '
                f'{self.owner_type}({self.owner_uuid}))')

    # Helpers
    def _read_template(self, config_path: str, template: str) -> None:
        with open(os.path.join(config.STORAGE_PATH, template)) as f:
            self.__config_templates[config_path] = jinja2.Template(f.read())

    def _make_config(self, just_this_path: Optional[str] = None) -> None:
        config_dir = self.config_directory
        os.makedirs(config_dir, exist_ok=True)
        subst = self.subst_dict()

        for outpath in self.__config_templates:
            if just_this_path and outpath != just_this_path:
                continue

            config_path = os.path.join(config_dir, outpath)
            original = ''
            if os.path.exists(config_path):
                with open(config_path) as f:
                    original = f.read()

            regenerated = self.__config_templates[outpath].render(subst)
            with open(config_path, 'w') as f:
                f.write(regenerated)

            if original == regenerated:
                self.add_event(EVENT_TYPE_AUDIT, 'generated unchanged configuration',
                               extra={'path': outpath})

            else:
                self.add_event(EVENT_TYPE_AUDIT, 'generated modified configuration',
                               extra={
                                   'path': outpath,
                                   'original': original,
                                   'regenerated': regenerated
                               })

    def _remove_config(self) -> None:
        path = self.config_directory
        if os.path.exists(path):
            self.add_event(EVENT_TYPE_AUDIT, 'removed configuration',
                           extra={'path': path})
            shutil.rmtree(path)

    def _send_signal(self, sig: signal.Signals) -> bool:
        pid = self.get_pid()
        if pid:
            if not psutil.pid_exists(pid):
                return False
            os.kill(pid, sig)
            self.add_event(EVENT_TYPE_AUDIT, 'sent signal',
                           extra={
                               'pid': pid,
                               'signal': sig
                           })
            if sig == signal.SIGKILL:
                self._wait_for_exit(pid)
            return True
        return False

    def _wait_for_exit(self, pid: int) -> None:
        """Wait for a process we have killed, child or not, to be gone.

        This never raises. The kill is part way through a teardown -- terminate()
        still has a configuration directory to remove, and its caller the rest of
        a network delete to perform -- so a wait which cannot observe the exit
        must say so and return rather than abort all of that (#4753).
        """
        log = LOG.with_fields({
            'pid': pid,
            'object_type': self.object_type,
            'object_uuid': self.uuid
        })
        still_running = (f'Process still running {KILL_WAIT_TIMEOUT} seconds '
                         'after SIGKILL')

        try:
            psutil.Process(pid).wait(timeout=KILL_WAIT_TIMEOUT)
            return
        except psutil.NoSuchProcess:
            return
        except psutil.TimeoutExpired:
            log.warning(still_running)
            return
        except OSError as e:
            # psutil waits with pidfd_open() where it can, and falls back to its
            # own waitpid() loop for only four errnos. A process which has been
            # SIGKILLed but not yet reaped can still be found by pid while no
            # longer having a thread group leader, and pidfd_open() answers
            # EINVAL in that window rather than ESRCH, which psutil re-raises
            # (#4753). Any other OSError from here is also a failure to watch
            # the pid rather than evidence about the process, so poll instead of
            # giving up on the wait that #4472 added.
            log.info(f'Waiting for exit failed ({e}), polling the pid instead')

        # These errors are all raised before any waiting happened, so the poll
        # gets the whole timeout rather than what might be left of it.
        deadline = time.time() + KILL_WAIT_TIMEOUT
        while time.time() < deadline:
            if not psutil.pid_exists(pid):
                return
            time.sleep(KILL_POLL_INTERVAL)

        log.warning(still_running)

    def subst_dict(self) -> dict[str, Any]:
        return {
            'config_dir': self.config_directory,
            'namespace': self.namespace
        }

    def get_pid(self) -> Optional[int]:
        path = self.config_directory
        pid_file = os.path.join(path, 'pid')
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = int(f.read())
                return pid
        return None

    def is_running(self) -> bool:
        pid = self.get_pid()
        if pid and psutil.pid_exists(pid):
            return True
        return False

    def terminate(self) -> None:
        self._send_signal(signal.SIGKILL)
        self._remove_config()
        self.add_event(EVENT_TYPE_AUDIT, 'terminated')
