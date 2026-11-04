# The ports kerbside_source.yml reserves on a co-located Kerbside host are the
# ports Shaken Fist's daemons actually listen on.
#
# kerbside_source.yml hard-codes the daemon ports (guarded only by a comment),
# and so does tools/ci-test-kerbside-wiring.sh. A change to sf-api.socket or to
# one of SFConfig's *_PORT defaults would leave those copies stale, and a
# co-located Kerbside listener could then bind a port a daemon needs. This test
# reads the ports from their sources of truth and pins the copy (#4707).

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


def _sf_api_socket_port():
    """The port sf-api.socket binds, which systemd hands sf-api."""
    with open(SF_API_SOCKET) as f:
        match = re.search(r'^ListenStream=.*?:(\d+)\s*$', f.read(), re.MULTILINE)
    assert match is not None, 'sf-api.socket has no ListenStream <addr>:<port>'
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
