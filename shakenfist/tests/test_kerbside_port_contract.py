# Copyright 2026 Michael Still and contributors

# The ports kerbside_source.yml reserves on a co-located Kerbside host are the
# ports Shaken Fist's daemons actually listen on.
#
# kerbside_source.yml hard-codes the daemon ports (guarded only by a comment),
# and so does tools/ci-test-kerbside-wiring.sh. A change to sf-api.socket or to
# one of SFConfig's *_PORT defaults would leave those copies stale, and a
# co-located Kerbside listener could then bind a port a daemon needs. This test
# reads the ports from their sources of truth and pins both copies (#4707) --
# both, because a failure message which names a second file the test does not
# read sends the reader to fix a copy that then fails somewhere else.

import os
import re

import yaml

from shakenfist.config import SFConfig
from shakenfist.tests import base


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


NODE_TASKS = os.path.join(
    _repo_root(), 'shakenfist', 'deploy', 'collection', 'roles', 'node', 'tasks',
    'kerbside_source.yml')
SF_API_SOCKET = os.path.join(
    _repo_root(), 'shakenfist', 'deploy', 'collection', 'roles', 'node', 'templates',
    'sf-api.socket')
WIRING_SCRIPT = os.path.join(_repo_root(), 'tools', 'ci-test-kerbside-wiring.sh')


def _reserved_listener_ports():
    """The port keys of the kerbside_sf_listener_ports map kerbside_source.yml
    sets, which it reserves on a co-located host."""
    with open(NODE_TASKS) as f:
        tasks = yaml.safe_load(f)
    for task in tasks:
        listener = (task.get('vars') or {}).get('kerbside_sf_listener_ports')
        if listener is not None:
            return set(listener)
    raise AssertionError(
        'kerbside_source.yml no longer sets kerbside_sf_listener_ports; this '
        'test can no longer pin the port contract.')


def _wiring_script_ports():
    """The co-located port list ci-test-kerbside-wiring.sh expects."""
    with open(WIRING_SCRIPT) as f:
        match = re.search(r'^want_ports = \(\[([0-9,\s]*)\]', f.read(), re.MULTILINE)
    if match is None:
        raise AssertionError(
            'ci-test-kerbside-wiring.sh no longer has a want_ports list of the '
            'co-located ports; this test can no longer pin that copy.')
    return {int(port) for port in match.group(1).split(',') if port.strip()}


def _sf_api_socket_port():
    """The port sf-api.socket binds, which systemd hands sf-api."""
    with open(SF_API_SOCKET) as f:
        match = re.search(r'^ListenStream=.*?:(\d+)\s*$', f.read(), re.MULTILINE)
    if match is None:
        raise AssertionError('sf-api.socket has no ListenStream <addr>:<port>')
    return int(match.group(1))


def _default_port(name):
    return SFConfig.model_fields[name].default


class KerbsidePortContractTestCase(base.ShakenFistTestCase):
    def test_reserved_ports_match_their_sources(self):
        want = {
            _sf_api_socket_port(),
            _default_port('RESOURCES_METRICS_PORT'),
            _default_port('MARIADB_GATEWAY_PORT'),
            _default_port('MARIADB_GATEWAY_METRICS_PORT'),
            _default_port('CLUSTER_METRICS_PORT'),
        }
        self.assertEqual(
            want, _reserved_listener_ports(),
            'The ports kerbside_source.yml reserves on a co-located host no '
            'longer match sf-api.socket and SFConfig\'s *_PORT defaults. Update '
            'kerbside_sf_listener_ports (and the copy in '
            'tools/ci-test-kerbside-wiring.sh) to match.')
        self.assertEqual(
            want, _wiring_script_ports(),
            "The want_ports list in tools/ci-test-kerbside-wiring.sh no longer "
            "matches sf-api.socket and SFConfig's *_PORT defaults. Update it to "
            'match, along with kerbside_sf_listener_ports in '
            'kerbside_source.yml.')
