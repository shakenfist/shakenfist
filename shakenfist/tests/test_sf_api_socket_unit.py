# Copyright 2026 Michael Still and contributors

"""Tests for the node role's systemd-held sf-api listening socket.

systemd holds sf-api's listening socket (``sf-api.socket``) so that a restart
of sf-api does not close port 13000 and reset the connections queued on it,
which a load balancer surfaced as 502s (#3664). That only works while several
files agree: the socket unit, the service unit, config.yml which installs the
socket and records whether it changed, and register.yml which hands the port
from a running sf-api to systemd. Nothing exercises those files short of a
real deploy, so the properties each depends on are pinned here.
"""

import os
import re
import shlex

import yaml

from shakenfist.tests import base


ROLE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'roles', 'node'))
SOCKET_PATH = os.path.join(ROLE_PATH, 'templates', 'sf-api.socket')
SERVICE_PATH = os.path.join(ROLE_PATH, 'templates', 'sf-api.service')
CONFIG_TASKS_PATH = os.path.join(ROLE_PATH, 'tasks', 'config.yml')
REGISTER_TASKS_PATH = os.path.join(ROLE_PATH, 'tasks', 'register.yml')


def parse_unit(path):
    """Parse a systemd unit into {section: {key: [values]}}.

    configparser cannot be used: systemd units repeat keys (After=, for one),
    which configparser rejects.
    """
    sections = {}
    current = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('[') and line.endswith(']'):
                current = sections.setdefault(line[1:-1], {})
                continue
            key, value = line.split('=', 1)
            current.setdefault(key, []).append(value)
    return sections


def gunicorn_argv():
    """The gunicorn command line from sf-api.service's ExecStart."""
    exec_start = parse_unit(SERVICE_PATH)['Service']['ExecStart'][0]
    argv = shlex.split(exec_start)
    if argv[:2] != ['/bin/sh', '-c']:
        raise AssertionError(f'ExecStart is not a /bin/sh -c command: {exec_start}')
    return shlex.split(argv[2])


def load_tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def flatten(tasks):
    """Tasks in execution order, with blocks expanded in place."""
    for task in tasks:
        yield task
        yield from flatten(task.get('block', []))


def service_task(tasks, name, state):
    for index, task in enumerate(tasks):
        service = task.get('ansible.builtin.service')
        if service and service.get('name') == name and service.get('state') == state:
            return index, task
    return None, None


class SfApiSocketUnitTestCase(base.ShakenFistTestCase):
    def test_socket_listens_where_gunicorn_would_bind(self):
        # gunicorn ignores --bind when it is passed a socket, but --bind is the
        # fallback if it is not, and an operator reads the port from it.
        socket = parse_unit(SOCKET_PATH)['Socket']
        argv = gunicorn_argv()
        bind = argv[argv.index('--bind') + 1]
        self.assertEqual([bind], socket['ListenStream'])

    def test_gunicorn_keeps_the_pid_systemd_started(self):
        # gunicorn uses the passed socket only when LISTEN_PID is its own pid,
        # so the shell must exec it rather than run it as a child.
        argv = gunicorn_argv()
        self.assertEqual('exec', argv[0])
        self.assertTrue(argv[1].endswith('/gunicorn'), argv)

    def test_service_requires_and_follows_its_socket(self):
        unit = parse_unit(SERVICE_PATH)['Unit']
        self.assertIn('sf-api.socket', unit.get('Requires', []))
        self.assertIn('sf-api.socket', unit.get('After', []))

    def test_socket_starts_and_stops_with_sf_target(self):
        unit = parse_unit(SOCKET_PATH)
        self.assertEqual(['sf.target'], unit['Unit'].get('PartOf'))
        self.assertEqual(['sf.target'], unit['Install'].get('WantedBy'))

    def test_config_installs_the_socket_and_records_its_change(self):
        tasks = load_tasks(CONFIG_TASKS_PATH)
        installs = [
            t for t in tasks
            if t.get('ansible.builtin.template', {}).get('src') == 'sf-api.socket']
        self.assertEqual(1, len(installs))
        self.assertEqual('/etc/systemd/system/sf-api.socket',
                         installs[0]['ansible.builtin.template']['dest'])
        self.assertEqual('Reload systemd', installs[0].get('notify'))
        registered = installs[0]['register']

        facts = [t['ansible.builtin.set_fact'] for t in tasks if 'ansible.builtin.set_fact' in t]
        recorded = [f for f in facts if 'sf_units_changed' in f]
        self.assertEqual(1, len(recorded))
        self.assertIn(f'({registered} is changed)', recorded[0]['sf_units_changed'])
        self.assertEqual(f'{{{{ {registered} is changed }}}}', recorded[0]['sf_api_socket_changed'])

    def test_register_frees_the_port_before_starting_the_socket(self):
        tasks = load_tasks(REGISTER_TASKS_PATH)
        handover = [t for t in tasks if 'block' in t and any(
            b.get('ansible.builtin.service', {}).get('name') == 'sf-api.socket' for b in t['block'])]
        self.assertEqual(1, len(handover))

        # Within the handover sf-api stops, then the socket is (re)started.
        block = handover[0]['block']
        stop, _ = service_task(block, 'sf-api', 'stopped')
        restart, _ = service_task(block, 'sf-api.socket', 'restarted')
        self.assertIsNotNone(stop)
        self.assertIsNotNone(restart)
        self.assertLess(stop, restart)

        # It runs when the socket is not active or its unit changed. The
        # condition is checked by name; a when which drops either half leaves
        # one of the two transitions running into a refused socket start.
        when = handover[0]['when']
        check = [t for t in tasks if t.get('ansible.builtin.command') == 'systemctl is-active sf-api.socket']
        self.assertEqual(1, len(check))
        self.assertIn(f'{check[0]["register"]}.rc != 0', when)
        self.assertRegex(when, r'\bor\b')
        self.assertIn('sf_api_socket_changed', when)

    def test_register_hands_over_before_starting_sf_api(self):
        tasks = list(flatten(load_tasks(REGISTER_TASKS_PATH)))
        handover = next(i for i, t in enumerate(tasks)
                        if t.get('ansible.builtin.service', {}).get('name') == 'sf-api.socket')
        starts_api = next(
            i for i, t in enumerate(tasks)
            if 'api' in t.get('with_items', [])
            and re.fullmatch(r'sf-\{\{ item \}\}', t.get('ansible.builtin.service', {}).get('name', '')))
        self.assertLess(handover, starts_api)
