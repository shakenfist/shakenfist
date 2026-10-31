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
"""

import os

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
