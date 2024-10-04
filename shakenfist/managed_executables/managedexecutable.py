import os
import shutil
import signal

import jinja2
import psutil
from shakenfist_utilities import logs

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config


LOG, _ = logs.setup(__name__)


class ManagedExecutable(dbo):
    object_type = 'unknown_managed_executable'

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None
    }

    def __init__(self, static_values):
        super().__init__(static_values['uuid'], static_values.get('version'))

        self.__namespace = static_values['namespace']
        self.__owner_type = static_values['owner_type']
        self.__owner_uuid = static_values['owner_uuid']
        self.__config_templates = {}
        self.__config_dir = os.path.join(config.STORAGE_PATH, self.object_type,
                                         self.uuid)

    # Static values
    @property
    def namespace(self):
        return self.__namespace

    @property
    def owner_type(self):
        return self.__owner_type

    @property
    def owner_uuid(self):
        return self.__owner_uuid

    @property
    def config_directory(self):
        return self.__config_dir

    @config_directory.setter
    def config_directory(self, value):
        self.__config_dir = value

    def __str__(self):
        return (f'{self.object_type}({self.uuid}, as owned by '
                f'{self.owner_type}({self.owner_uuid}))')

    # Helpers
    def _read_template(self, config_path, template):
        with open(os.path.join(config.STORAGE_PATH, template)) as f:
            self.__config_templates[config_path] = jinja2.Template(f.read())

    def _make_config(self, just_this_path=None):
        config_dir = self.config_directory
        os.makedirs(config_dir, exist_ok=True)
        subst = self.subst_dict()

        for outpath in self.__config_templates:
            if just_this_path and outpath != just_this_path:
                continue

            config_path = os.path.join(config_dir, outpath)
            regenerated = self.__config_templates[outpath].render(subst)

            with open(config_path, 'w') as f:
                f.write(regenerated)

    def _remove_config(self):
        path = self.config_directory
        if os.path.exists(path):
            shutil.rmtree(path)

    def _send_signal(self, sig):
        pid = self.get_pid()
        if pid:
            if not psutil.pid_exists(pid):
                return False
            os.kill(pid, sig)
            if sig == signal.SIGKILL:
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
            return True
        return False

    def subst_dict(self):
        return {
            'config_dir': self.config_directory,
            'namespace': self.namespace
        }

    def get_pid(self):
        path = self.config_directory
        pid_file = os.path.join(path, 'pid')
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = int(f.read())
                return pid
        return None

    def is_running(self):
        pid = self.get_pid()
        if pid and psutil.pid_exists(pid):
            return True
        return False

    def terminate(self):
        self._send_signal(signal.SIGKILL)
        self._remove_config()
