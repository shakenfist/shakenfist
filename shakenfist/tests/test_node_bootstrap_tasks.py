# Copyright 2026 Michael Still and contributors

"""Tests for the node role's bootstrap task list.

``pip_extra`` is how an operator passes index or proxy flags such as
``--index-url`` to the package installs the ``bootstrap`` entry point runs.
On a node which can reach only a private index, an install which does not
read it is the first thing to fail -- which is exactly what happened when
the pip/uv upgrade ran before ``pip_extra`` was ever used (issue 4614, and
the same shape in kerbside#580). The tests below hold the class closed:
every pip-style install in the task list must interpolate ``pip_extra``,
and because the flags may be empty or several words, each must be a
``shell`` task so the word-splitting actually happens.

The second group runs the role's own restart-on-change command against a
synthetic site-packages tree. The command decides whether every ``sf-*``
daemon on every node restarts, and the thing it must not react to is a new
version string on unchanged source: setuptools_scm writes ``_version.py``
inside the hashed package directories, so without an exclusion a docs-only
commit -- or a dirty tree's date suffix rolling over at midnight UTC --
bounces the whole cluster (issue 4483).
"""

import os
import shutil
import subprocess
import tempfile

import yaml

from shakenfist.tests import base


TASKS_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'roles', 'node',
    'tasks', 'bootstrap.yml'))


def pip_install_tasks():
    with open(TASKS_PATH) as f:
        tasks = yaml.safe_load(f)

    found = []
    for task in tasks:
        for module in ('ansible.builtin.command', 'ansible.builtin.shell'):
            action = task.get(module)
            if isinstance(action, dict):
                action = action.get('cmd', '')
            if action and 'pip install' in action:
                found.append((task.get('name', '(unnamed)'), module, action))
    return found


class NodeBootstrapTasksTestCase(base.ShakenFistTestCase):
    def test_every_pip_install_reads_pip_extra(self):
        installs = pip_install_tasks()

        # Vacancy guard: the virtualenv bootstrap and the package install
        # both live here today. If this stops matching them, the assertions
        # below are checking nothing.
        self.assertGreaterEqual(
            len(installs), 2,
            'expected at least the pip/uv upgrade and the package install '
            'in %s, found %s' % (TASKS_PATH, installs))

        for name, module, action in installs:
            self.assertIn(
                '{{ pip_extra }}', action,
                'task %r does not interpolate pip_extra, so index/proxy '
                'flags are lost for that install (issue 4614)' % name)
            self.assertEqual(
                'ansible.builtin.shell', module,
                'task %r interpolates pip_extra via %s, but pip_extra may '
                'be empty or several words, which only shell word-splitting '
                'handles' % (name, module))


# The name of the task which computes the hash register.yml gates daemon
# restarts on. Read off the role rather than restated here, so the command
# under test is the command that deploys.
SIGNATURE_TASK_NAME = 'Compute the installed Shaken Fist code signature'

# The venv prefix the role hard-codes. The test substitutes it for a
# temporary tree; the rest of the command, including the python3* glob and
# every -not predicate, runs exactly as written.
VENV_PREFIX = '/srv/shakenfist/venv'


def signature_command():
    with open(TASKS_PATH) as f:
        tasks = yaml.safe_load(f)

    for task in tasks:
        if task.get('name') == SIGNATURE_TASK_NAME:
            return task['ansible.builtin.shell']['cmd']

    raise AssertionError(
        'no task named %r in %s; the restart-on-change tests below are '
        'checking nothing' % (SIGNATURE_TASK_NAME, TASKS_PATH))


class NodeCodeSignatureTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.packages = os.path.join(
            self.root, 'lib', 'python3.11', 'site-packages')
        for package in ('shakenfist', 'shakenfist_client'):
            os.makedirs(os.path.join(self.packages, package))
            self.write(package, '__init__.py', '')
            self.write(package, '_version.py',
                       "version = '0.8.dev1+g0000000'\n")
        self.write('shakenfist', 'virt.py', 'def start():\n    pass\n')

    def write(self, package, name, content):
        with open(os.path.join(self.packages, package, name), 'w') as f:
            f.write(content)

    def signature(self):
        command = signature_command()
        self.assertIn(
            VENV_PREFIX, command,
            'the signature command no longer mentions %s, so the '
            'substitution below is pointing the hash at nothing'
            % VENV_PREFIX)
        command = command.replace(VENV_PREFIX, self.root)

        result = subprocess.run(
            ['/bin/bash', '-c', command], capture_output=True, text=True)
        self.assertEqual(
            0, result.returncode,
            'the signature command failed: %s' % result.stderr)
        return result.stdout.strip()

    def test_a_version_only_bump_does_not_change_the_signature(self):
        before = self.signature()

        # What a develop commit which touched no Python source does: both
        # packages are reinstalled with a new version string and nothing
        # else changes.
        for package in ('shakenfist', 'shakenfist_client'):
            self.write(package, '_version.py',
                       "version = '0.8.dev2+gdeadbee'\n")

        self.assertEqual(
            before, self.signature(),
            'a version-only bump changed the code signature, so every sf-* '
            'unit on every node restarts on a docs- or CI-only commit '
            '(issue 4483)')

    def test_a_source_change_does_change_the_signature(self):
        # The other half: an exclusion which swallowed real changes would
        # satisfy the test above while never restarting anything.
        before = self.signature()
        self.write('shakenfist', 'virt.py',
                   'def start():\n    return 1\n')

        self.assertNotEqual(
            before, self.signature(),
            'a change to installed Python source left the code signature '
            'untouched, so no daemon would be restarted for it')

    def test_pycache_is_not_hashed(self):
        # .pyc files carry embedded timestamps, so hashing them would make
        # the signature change on its own.
        before = self.signature()
        os.makedirs(os.path.join(self.packages, 'shakenfist', '__pycache__'))
        self.write(os.path.join('shakenfist', '__pycache__'),
                   'virt.cpython-311.pyc', 'not really bytecode')

        self.assertEqual(
            before, self.signature(),
            'a __pycache__ entry changed the code signature')
