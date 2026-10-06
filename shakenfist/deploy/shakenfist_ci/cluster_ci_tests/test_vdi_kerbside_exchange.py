# Copyright 2026 Michael Still and contributors
import json
import re
import shlex
import socket
import ssl
import time
import urllib.parse

import requests
from testtools import content

from shakenfist_ci import base
from shakenfist_ci import process
from shakenfist_ci import retries
from shakenfist_ci import spice_subject
from shakenfist_ci import vdi


# Kerbside scrapes Shaken Fist's consoles every 60 seconds, and a token for a
# console it has not scraped yet is refused with a 404 which leaves the token
# unspent. Two and a half scrapes is long enough for the first scrape after
# the instance reached created to have landed, whenever in the cycle it did.
SCRAPE_WAIT = 150
SCRAPE_POLL_INTERVAL = 10

# Kerbside's body for that 404 (SfToken.get() in kerbside/api.py). It is the
# only refusal worth waiting out: every other non-200 is a verdict.
CONSOLE_NOT_FOUND = 'console not found'

# Kerbside's body for a token whose jti it has already consumed.
TOKEN_ALREADY_USED = 'token already used'

# Each GET of the exchange URL is one plain HTTP round trip on the mesh.
EXCHANGE_REQUEST_TIMEOUT = 30

# A TLS handshake with the proxy, on the mesh, should take milliseconds.
TLS_TIMEOUT = 10

# The cluster_config row holding the console token signing key's private PEM
# (shakenfist/util/vdi_tokens.py), and the PEM marker which finds the key
# exported under any other name. Neither may appear in a daemon's environment.
SIGNING_KEY_PATTERNS = ('KERBSIDE_JWT_SIGNING_KEY', 'PRIVATE KEY')

DAEMON_UNIT_GLOB = 'sf-*.service'

# Daemons #4097 names. Each must be among the units checked somewhere in the
# cluster, so that a unit glob or a listing which matches nothing cannot pass.
REQUIRED_DAEMON_UNITS = ('sf-api.service', 'sf-queues.service')


def redact_vv(vv_text):
    """A .vv file with its password removed, for the test log.

    Kerbside's password is a live console credential for a minute.
    """
    return re.sub(r'(?m)^password=.*$', 'password=<redacted>', vv_text)


def daemon_environ_script(patterns, unit_glob=DAEMON_UNIT_GLOB,
                          systemctl='systemctl'):
    """A shell script counting pattern matches in daemon environments.

    For each running unit matching unit_glob it prints one line: the unit,
    its main PID, the size in bytes of /proc/<pid>/environ, then the number
    of environment variables containing each pattern, in order. Only counts
    leave the node, never the environment itself. A unit with no main PID
    (0) is skipped. The size is there so that an environment which could not
    be read -- which would otherwise count zero matches -- is visible as an
    empty one, and fails rather than passes.

    systemctl is a seam: 'systemctl --user' runs the same logic against a
    user's own units, whose environments that user can read without root.
    """
    counts = ''.join(
        ' "$(tr \'\\0\' \'\\n\' < /proc/$pid/environ | grep -a -c -F -e %s)"'
        % shlex.quote(pattern) for pattern in patterns)
    return (
        '%(systemctl)s list-units --type=service --state=running --no-legend '
        '--plain %(glob)s | while read -r unit _; do '
        'pid=$(%(systemctl)s show -p MainPID --value "$unit"); '
        'if [ -z "$pid" ] || [ "$pid" = 0 ]; then continue; fi; '
        'size=$(wc -c < /proc/$pid/environ); '
        'echo "$unit" "$pid" "${size:-0}"%(counts)s; '
        'done'
        % {'systemctl': systemctl, 'glob': shlex.quote(unit_glob),
           'counts': counts})


def parse_daemon_environ_counts(output, patterns):
    """Parse daemon_environ_script()'s output into one dict per unit."""
    rows = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 3 + len(patterns):
            raise ValueError('Unexpected daemon environment line: %r' % line)
        rows.append({
            'unit': fields[0],
            'pid': int(fields[1]),
            'environ_bytes': int(fields[2]),
            'matches': dict(zip(patterns, (int(f) for f in fields[3:]))),
        })
    return rows


class TestVDIKerbsideExchange(vdi.BaseVDIConsoleTestCase):
    """Exchange a console token at Kerbside, and check what comes back.

    test_vdi_tokens.py checks the token Shaken Fist mints; this follows it to
    Kerbside. A token's exchange URL is fetched with a plain GET -- the token
    is the credential -- and the .vv that comes back must name the proxy
    rather than the hypervisor, carry a CA and a host-subject which the
    proxy's TLS endpoint actually satisfies with host name checking on, and
    be issued once only. Alongside, the console token signing key must not
    be in any Shaken Fist daemon's environment (#4097).

    Like the mint test, every test here skips on a cluster which does not
    advertise the vdi-console-proxy capability, and fails instead when the
    deploy profile says Kerbside was expected
    (SF_CI_EXPECT_VDI_CONSOLE_PROXY=1, see base.kerbside_expected()).

    docs/plans/PLAN-kerbside-deployer-phase-04-ci.md (D9, D10) is the
    design.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'vdiexchange'
        super().__init__(*args, **kwargs)

    def _exchange(self, instance_uuid):
        """Exchange a fresh token for instance_uuid at Kerbside.

        Returns the exchange URL and Kerbside's 200 response. A console
        Kerbside has not scraped yet is retried, each time with a newly
        minted token, for up to SCRAPE_WAIT seconds; any other refusal fails
        the test at once with its status and body.
        """
        attempts = []

        def attempt():
            url = self._mint_vdi_console_proxy(instance_uuid)['url']
            r = requests.get(url, timeout=EXCHANGE_REQUEST_TIMEOUT)
            attempts.append(r.status_code)
            status = r.status_code
            if status == 404 and CONSOLE_NOT_FOUND in r.text:
                # Classified apart from any other 404, such as the route
                # itself missing, which waiting will not fix.
                status = CONSOLE_NOT_FOUND
            return status, (url, r)

        status, (url, r) = retries.retry_while_transient(
            attempt, {CONSOLE_NOT_FOUND}, time.time() + SCRAPE_WAIT,
            interval=SCRAPE_POLL_INTERVAL)
        self.addDetail(
            'exchange_attempts',
            content.text_content(json.dumps(attempts)))

        if status == CONSOLE_NOT_FOUND:
            self.fail(
                'Kerbside still had not scraped instance %s after %d seconds '
                '(%d attempts): %s'
                % (instance_uuid, SCRAPE_WAIT, len(attempts), r.text))
        if status != 200:
            self.fail(
                'Kerbside refused the console token exchange for instance %s '
                'with status %d: %s' % (instance_uuid, r.status_code, r.text))
        return url, r

    def test_exchange_returns_a_proxy_console_file(self):
        self._require_vdi_console_proxy()
        instance_uuid = self._create_spice_instance(
            self.test_client, self.namespace)

        url, r = self._exchange(instance_uuid)
        self.addDetail('kerbside_vv_file', content.text_content(redact_vv(r.text)))
        proxy_vv = self._parse_vv(r.text)
        direct_vv = self._fetch_direct_vv(
            instance_uuid, detail_name='direct_vv_file')

        for key in ('host', 'port', 'tls-port', 'password', 'ca',
                    'host-subject'):
            self.assertTrue(
                proxy_vv.get(key),
                'Kerbside\'s .vv has no %s value' % key)
        self.assertEqual('spice', proxy_vv['type'])

        # The .vv must send the viewer to the proxy, not to the hypervisor
        # the direct file names.
        self.assertNotEqual(
            (direct_vv.get('host'), direct_vv.get('tls-port')),
            (proxy_vv['host'], proxy_vv['tls-port']),
            'Kerbside\'s .vv names the hypervisor\'s own console endpoint')
        for key in ('port', 'tls-port'):
            port = int(proxy_vv[key])
            self.assertFalse(
                vdi.CONSOLE_PORT_MIN <= port <= vdi.CONSOLE_PORT_MAX,
                'Kerbside\'s .vv %s %d is in Shaken Fist\'s console port '
                'range %d-%d, so it is a hypervisor\'s port'
                % (key, port, vdi.CONSOLE_PORT_MIN, vdi.CONSOLE_PORT_MAX))
        self.assertEqual(
            urllib.parse.urlsplit(url).hostname, proxy_vv['host'],
            'Kerbside\'s .vv host is not the host of the exchange URL')

        # The proxy's TLS endpoint must satisfy what the .vv tells a viewer
        # to check: its certificate chains to the .vv's CA alone, it is
        # valid for the host the .vv names (an IP SAN on a cluster which
        # addresses Kerbside by IP), and its subject is the host-subject.
        # The handshake completes before any SPICE bytes are exchanged, so
        # nothing past it is needed.
        host = proxy_vv['host']
        tls_port = int(proxy_vv['tls-port'])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(
            cadata=proxy_vv['ca'].replace('\\n', '\n'))
        context.check_hostname = True
        try:
            with socket.create_connection(
                    (host, tls_port), timeout=TLS_TIMEOUT) as sock:
                with context.wrap_socket(
                        sock, server_hostname=host) as tls:
                    der = tls.getpeercert(binary_form=True)
        except (OSError, ssl.SSLError) as e:
            self.fail(
                'TLS handshake with Kerbside\'s proxy at %s:%d, trusting only '
                'the .vv\'s CA and checking the host name, failed: %s'
                % (host, tls_port, e))

        subject = spice_subject.host_subject(der)
        self.addDetail(
            'proxy_certificate_subject', content.text_content(str(subject)))
        self.assertIsNotNone(
            subject,
            'the proxy certificate\'s subject cannot be rendered as a SPICE '
            'host-subject')
        self.assertEqual(
            proxy_vv['host-subject'], subject,
            'Kerbside\'s .vv host-subject does not match the subject of the '
            'certificate its proxy presents')

    def test_exchange_refuses_a_replayed_token(self):
        self._require_vdi_console_proxy()
        instance_uuid = self._create_spice_instance(
            self.test_client, self.namespace)

        url, _ = self._exchange(instance_uuid)
        r = requests.get(url, timeout=EXCHANGE_REQUEST_TIMEOUT)
        self.assertEqual(
            401, r.status_code,
            'a second exchange of the same token was not refused: %s'
            % redact_vv(r.text))
        self.assertIn(TOKEN_ALREADY_USED, r.text)

    def test_signing_key_absent_from_daemon_environments(self):
        """The console token signing key is in no sf-* daemon's environment.

        The key's private PEM lives in the cluster_config row
        KERBSIDE_JWT_SIGNING_KEY and is read from the database where it is
        needed (#4099); only mocked fixtures checked that it stays out of
        daemon environments (#4097). This reads each running sf-* unit's
        main PID's /proc/<pid>/environ, as root, on every node, and counts
        variables containing the row name or a PEM private key marker.

        /proc/<pid>/environ is the environment the process was started
        with, which is what systemd, an EnvironmentFile or a deploy would
        have put there. A variable a daemon sets on itself later is not
        visible to this, and nothing in Shaken Fist does that.
        """
        self._require_vdi_console_proxy()

        # A cluster with no signing key has nothing to leak, and would pass
        # vacuously.
        material = self.test_client.get_vdi_token_public_keys()
        self.assertTrue(
            material.get('keys'),
            'the cluster publishes no console token signing key, so there is '
            'no key whose absence to check')

        nodes = [n for n in self._get_cluster_nodes()
                 if n.get('state') == 'created']
        self.assertNotEqual([], nodes, 'the cluster reports no created nodes')

        script = daemon_environ_script(SIGNING_KEY_PATTERNS)
        checked = []
        for node in nodes:
            try:
                self._node_exec(node, ['true'])
            except process.ProcessExecutionError as e:
                message = (
                    'Cannot exec on node %s (%s) over the mesh: %s'
                    % (node.get('name'), node.get('ip'), e))
                if base.kerbside_expected():
                    self.fail(message)
                self.skipTest(message)

            out, _ = self._node_exec(node, ['sh', '-c', script], sudo=True)
            rows = parse_daemon_environ_counts(out, SIGNING_KEY_PATTERNS)
            self.assertNotEqual(
                [], rows,
                'no running %s unit with a main PID on node %s'
                % (DAEMON_UNIT_GLOB, node.get('name')))
            for row in rows:
                row['node'] = node.get('name')
                checked.append(row)

        self.addDetail(
            'daemon_environment_counts',
            content.text_content(json.dumps(checked, indent=4)))

        units = {row['unit'] for row in checked}
        for unit in REQUIRED_DAEMON_UNITS:
            self.assertIn(
                unit, units, '%s was not checked on any node' % unit)

        unreadable = [
            '%s on %s (pid %d)' % (row['unit'], row['node'], row['pid'])
            for row in checked if row['environ_bytes'] <= 0]
        self.assertEqual(
            [], unreadable,
            'these daemons\' environments read as empty, so their counts '
            'mean nothing')

        leaks = [
            '%s on %s (pid %d): %s'
            % (row['unit'], row['node'], row['pid'],
               ', '.join('%r x%d' % (pattern, count)
                         for pattern, count in row['matches'].items()
                         if count))
            for row in checked if any(row['matches'].values())]
        self.assertEqual(
            [], leaks,
            'the console token signing key, or a private key, is in these '
            'daemons\' environments')
