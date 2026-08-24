#!/usr/bin/python3
# Copyright 2019 Michael Still and contributors

"""Exercise the namespace capacity claim pathways against a live cluster.

This is the phase 4 operator soak of PLAN-scheduler-reservations, run as
a script rather than by hand because the interesting half of the
behaviour is on a timer: a claim's coverage_state only flips to
``expired`` when the capacity reconciler next sweeps, so the timeout
paths cannot be checked by a person watching a terminal without a lot of
waiting and arithmetic.

The claim REST API is admin-only and shakenfist_client has no bindings
for it, so everything here is raw HTTP.

DESTRUCTIVE, but only to things it created. It makes its own namespace
and deletes it on the way out, including after a failure or a Ctrl-C.
It never touches a namespace, claim or instance it did not create. It
does consume real cluster capacity while it runs, so do not point it at
a cluster that is already full.

Usage:
    tools/exercise-namespace-claims.py                 # full run
    tools/exercise-namespace-claims.py --no-instances  # API paths only
    tools/exercise-namespace-claims.py --no-expiry     # skip the ~7 min wait
    tools/exercise-namespace-claims.py --keep          # leave state behind

Credentials come from $SHAKENFIST_API_URL / $SHAKENFIST_NAMESPACE /
$SHAKENFIST_KEY, falling back to ~/.shakenfist. The authenticated
namespace must be a cluster administrator.
"""

import argparse
import json
import os
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.request


# How long to wait for the reconciler to sweep an expired claim. The
# sweep runs inside reconcile_scheduler_capacity (mariadb.py, the
# UPDATE ... WHERE state = 'active' AND expires_at < NOW()), which the
# cluster maintainer runs every five minutes, so a claim can sit expired
# but unswept for most of that. Allow a pass to be missed entirely.
RECONCILE_SWEEP_TIMEOUT = 12 * 60
RECONCILE_POLL_INTERVAL = 10

# How long cleanup waits for an asynchronously deleted object to actually
# disappear before giving up and telling the operator to finish by hand.
CLEANUP_TIMEOUT = 180
CLEANUP_POLL_INTERVAL = 5

# Network creation is asynchronous too: an instance cannot attach to a
# network until the network daemon has moved it out of 'initial'.
NETWORK_READY_TIMEOUT = 180
NETWORK_POLL_INTERVAL = 5

# The TTL given to the claim used for the expiry test. Short enough that
# the wait is dominated by the sweep interval rather than by the TTL.
EXPIRY_CLAIM_TTL = 30

# A claim big enough that no real cluster can promise it, used to
# provoke the 507 capacity refusal.
IMPOSSIBLE_CPUS = 1000000


class Failure(Exception):
    """A check failed. Distinct from a transport or setup error."""


class Client:
    def __init__(self, base_url, namespace, key, insecure=False):
        self.base_url = base_url.rstrip('/')
        self.namespace = namespace
        self.key = key
        self.token = None
        self.ctx = None
        if insecure:
            self.ctx = ssl.create_default_context()
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def request(self, method, path, body=None, authenticate=True):
        """Return (status, decoded body). Never raises on an HTTP status."""
        url = self.base_url + path
        data = None
        headers = {'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if authenticate:
            headers['Authorization'] = 'Bearer %s' % self.token

        req = urllib.request.Request(
            url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60,
                                        context=self.ctx) as resp:
                raw = resp.read().decode('utf-8')
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read().decode('utf-8')
            try:
                return e.code, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return e.code, raw

    def authenticate(self):
        status, body = self.request(
            'POST', '/auth',
            {'namespace': self.namespace, 'key': self.key},
            authenticate=False)
        if status != 200:
            raise Failure('authentication failed: HTTP %s %s' % (status, body))
        self.token = body['access_token']


class Runner:
    """Runs checks, remembers what failed, and prints as it goes."""

    def __init__(self):
        self.passed = 0
        self.failed = []
        self.skipped = []
        self.section = None

    def begin(self, title):
        self.section = title
        print('\n=== %s ===' % title)

    def check(self, description, fn):
        try:
            detail = fn()
        except Failure as e:
            self.failed.append((self.section, description, str(e)))
            print('  FAIL  %s\n          %s' % (description, e))
            return None
        except Exception:
            trace = traceback.format_exc().strip().splitlines()[-1]
            self.failed.append((self.section, description, trace))
            print('  ERROR %s\n          %s' % (description, trace))
            return None
        self.passed += 1
        print('  ok    %s%s' % (
            description, '  [%s]' % detail if detail else ''))
        # Callers use the return value as a "did this pass?" guard for
        # dependent checks, so a passing check must never return a falsy
        # value. Checks that have no detail to report return None, and
        # returning that here silently skipped their dependents rather
        # than running them.
        return detail if detail else True

    def skip(self, description, why):
        self.skipped.append((self.section, description, why))
        print('  skip  %s  (%s)' % (description, why))

    def report(self):
        print('\n' + '=' * 62)
        print('passed %d, failed %d, skipped %d'
              % (self.passed, len(self.failed), len(self.skipped)))
        if self.failed:
            print('\nFailures:')
            for section, description, detail in self.failed:
                print('  [%s] %s\n      %s' % (section, description, detail))
        return 1 if self.failed else 0


def expect(status, body, wanted, what):
    if status != wanted:
        raise Failure('%s: expected HTTP %s, got %s -- %s'
                      % (what, wanted, status, body))
    return body


def load_credentials(args):
    url = os.environ.get('SHAKENFIST_API_URL')
    namespace = os.environ.get('SHAKENFIST_NAMESPACE')
    key = os.environ.get('SHAKENFIST_KEY')

    if not (url and namespace and key):
        path = os.path.expanduser(args.config)
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    conf = json.load(f)
            except (OSError, ValueError) as e:
                print('Could not read %s: %s' % (path, e), file=sys.stderr)
                sys.exit(2)
            if not isinstance(conf, dict):
                print('%s does not contain a JSON object' % path,
                      file=sys.stderr)
                sys.exit(2)
            url = url or conf.get('apiurl')
            namespace = namespace or conf.get('namespace')
            key = key or conf.get('key')

    missing = [n for n, v in [('api url', url), ('namespace', namespace),
                              ('key', key)] if not v]
    if missing:
        print('No credentials for: %s. Set SHAKENFIST_API_URL, '
              'SHAKENFIST_NAMESPACE and SHAKENFIST_KEY, or populate %s.'
              % (', '.join(missing), args.config), file=sys.stderr)
        sys.exit(2)
    return url, namespace, key


def wait_until_gone(client, path):
    """Poll an object until it 404s or reaches the deleted state."""
    deadline = time.time() + CLEANUP_TIMEOUT
    while time.time() < deadline:
        status, body = client.request('GET', path)
        if status == 404:
            return True
        if isinstance(body, dict) and body.get('state') == 'deleted':
            return True
        time.sleep(CLEANUP_POLL_INTERVAL)
    return False


def delete_namespace(client, namespace):
    """Delete a namespace, retrying while it still owns resources.

    A namespace delete is refused with a 400 while anything in it is
    still being torn down. The objects we made are already waited for,
    but their teardown can leave briefly-lingering dependents, so retry
    rather than declaring the cleanup failed on the first refusal.
    """
    deadline = time.time() + CLEANUP_TIMEOUT
    while True:
        status, _ = client.request('DELETE', '/auth/namespaces/%s' % namespace)
        if status == 200 or time.time() >= deadline:
            return status
        time.sleep(CLEANUP_POLL_INTERVAL)


def cluster_capacity(client):
    """Total cluster capacity per dimension, or None if unavailable."""
    status, body = client.request('GET', '/admin/resources')
    if status != 200:
        return None
    return body


def main():
    parser = argparse.ArgumentParser(
        description='Exercise namespace capacity claims against a cluster.')
    parser.add_argument('--config', default='~/.shakenfist',
                        help='client config to fall back to (default '
                             '~/.shakenfist)')
    parser.add_argument('--namespace-prefix', default='claimsoak',
                        help='prefix for the throwaway namespace')
    parser.add_argument('--no-instances', action='store_true',
                        help='skip the drawdown phase, which creates real '
                             'instances')
    parser.add_argument('--no-expiry', action='store_true',
                        help='skip the expiry phase and its multi-minute wait')
    parser.add_argument('--keep', action='store_true',
                        help='do not clean up, for post-mortem inspection')
    parser.add_argument('--insecure', action='store_true',
                        help='do not verify TLS certificates')
    parser.add_argument('--image', default='cirros',
                        help='disk base for the drawdown instances. The '
                             'default is resolved by the cirros image '
                             'resolver, so it needs no pre-existing '
                             'artifact. A sf://label/... reference works '
                             'too, but only if the label is in this '
                             'namespace or is shared')
    parser.add_argument('--instance-cpus', type=int, default=1)
    parser.add_argument('--instance-memory', type=int, default=1024)
    parser.add_argument('--instance-disk', type=int, default=8)
    args = parser.parse_args()

    url, namespace, key = load_credentials(args)
    client = Client(url, namespace, key, insecure=args.insecure)

    # A namespace name that is unique per run, so a previous crashed run
    # cannot collide with this one and so cleanup can never match
    # somebody else's namespace.
    soak_ns = '%s-%d' % (args.namespace_prefix, int(time.time()))

    print('cluster    : %s' % url)
    print('admin as   : %s' % namespace)
    print('namespace  : %s  (created and deleted by this script)' % soak_ns)
    print('instances  : %s' % ('skipped' if args.no_instances else
                               'yes, from %s' % args.image))
    print('expiry     : %s' % ('skipped' if args.no_expiry else
                               'yes, up to %d min of waiting'
                               % (RECONCILE_SWEEP_TIMEOUT // 60)))

    r = Runner()
    created_ns = False
    instances = []

    try:
        r.begin('Preflight')

        def _auth():
            try:
                client.authenticate()
            except urllib.error.URLError as e:
                raise Failure('cannot reach %s: %s' % (url, e.reason))
            return 'as %s' % namespace
        if not r.check('authenticate as an administrator', _auth):
            print('\nCannot continue without authentication.')
            return r.report()

        def _capacity():
            body = cluster_capacity(client)
            if body is None:
                raise Failure('/admin/resources did not return 200; the '
                              'reconciler may not have built the cluster '
                              'capacity singleton yet')
            return None
        r.check('read /admin/resources', _capacity)

        def _mkns():
            status, body = client.request(
                'POST', '/auth/namespaces', {'namespace': soak_ns})
            expect(status, body, 200, 'create namespace')
            return soak_ns
        if r.check('create the throwaway namespace', _mkns):
            created_ns = True
        else:
            print('\nCannot continue without a namespace.')
            return r.report()

        claims_url = '/auth/namespaces/%s/claims' % soak_ns

        # ------------------------------------------------------------------
        r.begin('Request validation (these must not reach the database)')

        def bad_create(body, what):
            def _fn():
                status, resp = client.request('POST', claims_url, body)
                expect(status, resp, 400, what)
                return None
            return _fn

        base = {'limit_cpus': 4, 'limit_memory_mb': 4096, 'limit_disk_gb': 40,
                'expires_in_seconds': 3600}

        for description, body in [
                ('missing expires_in_seconds',
                 {k: v for k, v in base.items() if k != 'expires_in_seconds'}),
                ('zero expires_in_seconds', dict(base, expires_in_seconds=0)),
                ('negative expires_in_seconds',
                 dict(base, expires_in_seconds=-1)),
                ('boolean expires_in_seconds',
                 dict(base, expires_in_seconds=True)),
                ('string expires_in_seconds',
                 dict(base, expires_in_seconds='3600')),
                ('negative limit_cpus', dict(base, limit_cpus=-1)),
                ('boolean limit_cpus', dict(base, limit_cpus=True)),
                ('string limit_memory_mb', dict(base, limit_memory_mb='4096'))]:
            r.check('400 on %s' % description,
                    bad_create(body, description))

        # ------------------------------------------------------------------
        r.begin('Create')

        state = {}

        def _create():
            status, body = client.request('POST', claims_url, base)
            expect(status, body, 200, 'create claim')
            for field, wanted in [('namespace', soak_ns),
                                  ('coverage_state', 'active'),
                                  ('limit_cpus', 4),
                                  ('limit_memory_mb', 4096),
                                  ('limit_disk_gb', 40)]:
                if body.get(field) != wanted:
                    raise Failure('claim.%s is %r, expected %r'
                                  % (field, body.get(field), wanted))
            for field in ('used_cpus', 'used_memory_mb', 'used_disk_gb'):
                if body.get(field) != 0:
                    raise Failure('a new claim has %s = %r, expected 0'
                                  % (field, body.get(field)))
            if not body.get('expires_at'):
                raise Failure('claim has no expires_at')
            state['uuid'] = body['uuid']
            return body['uuid']

        if not r.check('create a claim', _create):
            print('\nCannot continue without a claim.')
            return r.report()

        claim_url = '%s/%s' % (claims_url, state['uuid'])

        def _duplicate():
            status, body = client.request('POST', claims_url, base)
            expect(status, body, 409, 'duplicate claim')
            return 'exists'
        r.check('409 on a second claim for the same namespace', _duplicate)

        def _impossible():
            status, body = client.request(
                'POST', '/auth/namespaces/%s/claims' % soak_ns,
                dict(base, limit_cpus=IMPOSSIBLE_CPUS))
            # The namespace already holds a claim, so 'exists' is
            # checked before capacity. Assert we get one of the two
            # refusals rather than a success or a 500.
            if status not in (409, 507):
                raise Failure('expected 409 or 507, got %s -- %s'
                              % (status, body))
            return 'HTTP %d' % status
        r.check('an impossible claim is refused, not granted', _impossible)

        # ------------------------------------------------------------------
        r.begin('Read')

        def _list():
            status, body = client.request('GET', claims_url)
            expect(status, body, 200, 'list claims')
            if not isinstance(body, list) or len(body) != 1:
                raise Failure('expected exactly one claim, got %r' % (body,))
            if body[0]['uuid'] != state['uuid']:
                raise Failure('listed the wrong claim')
            return None
        r.check('the claim appears in the namespace listing', _list)

        def _get():
            status, body = client.request('GET', claim_url)
            expect(status, body, 200, 'get claim')
            return body['coverage_state']
        r.check('the claim can be read by uuid', _get)

        def _wrong_ns():
            status, body = client.request(
                'GET', '/auth/namespaces/system/claims/%s' % state['uuid'])
            expect(status, body, 404, 'cross-namespace read')
            return 'not disclosed'
        r.check('404 reading the claim through another namespace', _wrong_ns)

        # ------------------------------------------------------------------
        r.begin('Update')

        def _grow():
            status, body = client.request(
                'PUT', claim_url, {'limit_cpus': 8})
            expect(status, body, 200, 'grow claim')
            if body['limit_cpus'] != 8:
                raise Failure('limit_cpus is %r after growing to 8'
                              % body['limit_cpus'])
            # The field mask must have left the other dimensions alone.
            if body['limit_memory_mb'] != 4096:
                raise Failure('growing cpus changed limit_memory_mb to %r; '
                              'the field mask is not being honoured'
                              % body['limit_memory_mb'])
            if body['limit_disk_gb'] != 40:
                raise Failure('growing cpus changed limit_disk_gb to %r; '
                              'the field mask is not being honoured'
                              % body['limit_disk_gb'])
            return '4 -> 8 cpus, other dimensions untouched'
        r.check('grow one dimension without disturbing the others', _grow)

        def _empty_put():
            status, body = client.request('PUT', claim_url, {})
            expect(status, body, 400, 'empty update')
            return None
        r.check('400 on an update naming no fields', _empty_put)

        def _redate():
            status, body = client.request(
                'PUT', claim_url, {'expires_in_seconds': 7200})
            expect(status, body, 200, 're-date claim')
            return None
        r.check('a claim can be re-dated', _redate)

        def _shrink_to_zero():
            status, body = client.request('PUT', claim_url, {'limit_cpus': 0})
            expect(status, body, 200, 'shrink to zero')
            status, body = client.request('PUT', claim_url, {'limit_cpus': 8})
            expect(status, body, 200, 'restore')
            return None
        r.check('an unused claim can be shrunk to zero and back',
                _shrink_to_zero)

        # ------------------------------------------------------------------
        r.begin('Drawdown against real instances')

        if args.no_instances:
            r.skip('placement draws the claim down', '--no-instances')
            r.skip('a claim cannot shrink below its usage', '--no-instances')
        else:
            def _network():
                status, body = client.request(
                    'POST', '/networks',
                    {'netblock': '10.200.0.0/24', 'name': 'claimsoak',
                     'namespace': soak_ns, 'provide_dhcp': True,
                     'provide_nat': False})
                expect(status, body, 200, 'create network')
                state['network'] = body['uuid']
                return body['uuid']
            have_network = r.check('create a network for the instances',
                                   _network)

            if have_network:
                def _network_ready():
                    # Network creation is asynchronous: the API returns a
                    # network in the 'initial' state and the network
                    # daemon brings it up. Attaching an instance before
                    # then is refused with a 406, so wait for 'created'.
                    net_url = '/networks/%s' % state['network']
                    deadline = time.time() + NETWORK_READY_TIMEOUT
                    seen = None
                    while time.time() < deadline:
                        status, body = client.request('GET', net_url)
                        if status == 200:
                            seen = body.get('state')
                            if seen == 'created':
                                return 'ready'
                            if seen in ('error', 'deleted'):
                                raise Failure(
                                    'network reached %s instead of created'
                                    % seen)
                        time.sleep(NETWORK_POLL_INTERVAL)
                    raise Failure(
                        'network was still %s after %ds'
                        % (seen, NETWORK_READY_TIMEOUT))
                have_network = r.check('the network becomes ready',
                                       _network_ready)

            def _instance():
                status, body = client.request(
                    'POST', '/instances',
                    {'name': 'claimsoak-1',
                     'cpus': args.instance_cpus,
                     'memory': args.instance_memory,
                     'namespace': soak_ns,
                     'disk': [{'size': args.instance_disk,
                               'base': args.image,
                               'type': 'disk'}],
                     'network': ([{'network_uuid': state['network']}]
                                 if have_network else [])})
                expect(status, body, 200, 'create instance')
                instances.append(body['uuid'])
                return body['uuid']
            made = r.check('create an instance in the claimed namespace',
                           _instance)

            if made:
                def _drawdown():
                    # The claim's used_* counters move in the same
                    # transaction as the placement, so they should be
                    # correct as soon as the create returns. Poll
                    # briefly anyway: the create returns once queued.
                    deadline = time.time() + 120
                    last = None
                    while time.time() < deadline:
                        status, body = client.request('GET', claim_url)
                        if status == 200:
                            last = body
                            if body['used_cpus'] >= args.instance_cpus:
                                return ('used_cpus=%s used_memory_mb=%s '
                                        'used_disk_gb=%s'
                                        % (body['used_cpus'],
                                           body['used_memory_mb'],
                                           body['used_disk_gb']))
                        time.sleep(5)
                    raise Failure(
                        'claim usage never reflected the instance within '
                        '120s; last read %r' % (last,))
                drawn = r.check('placement draws the claim down', _drawdown)

                if drawn:
                    def _below_usage():
                        status, body = client.request(
                            'PUT', claim_url, {'limit_cpus': 0})
                        expect(status, body, 409, 'shrink below usage')
                        return 'below_usage'
                    r.check('409 shrinking a claim below its usage',
                            _below_usage)

        # ------------------------------------------------------------------
        r.begin('Expiry (the timeout pathway)')

        if args.no_expiry:
            r.skip('a claim expires and stops covering placements',
                   '--no-expiry')
            r.skip('409 updating an expired claim', '--no-expiry')
        else:
            def _short():
                status, body = client.request(
                    'PUT', claim_url,
                    {'expires_in_seconds': EXPIRY_CLAIM_TTL})
                expect(status, body, 200, 'shorten claim')
                return 'expires in %ds' % EXPIRY_CLAIM_TTL
            shortened = r.check('re-date the claim to expire shortly', _short)

            if shortened:
                def _expire():
                    # coverage_state is a stored column, flipped by the
                    # reconciler's sweep rather than computed on read,
                    # so this waits for a reconcile pass and not merely
                    # for the wall clock to pass expires_at.
                    deadline = time.time() + RECONCILE_SWEEP_TIMEOUT
                    started = time.time()
                    while time.time() < deadline:
                        status, body = client.request('GET', claim_url)
                        if status != 200:
                            raise Failure('claim vanished while waiting for '
                                          'expiry: HTTP %s' % status)
                        if body['coverage_state'] == 'expired':
                            return ('swept after %ds'
                                    % int(time.time() - started))
                        if body['state'] != 'created':
                            raise Failure(
                                'claim object state became %r; coverage_state '
                                'and state are separate facts and the object '
                                'should still be created'
                                % body['state'])
                        time.sleep(RECONCILE_POLL_INTERVAL)
                    raise Failure(
                        'claim was still active %d minutes after expiry; the '
                        'reconciler sweep may not be running'
                        % (RECONCILE_SWEEP_TIMEOUT // 60))
                expired = r.check(
                    'the reconciler sweeps the claim to expired', _expire)

                if expired:
                    def _not_active():
                        status, body = client.request(
                            'PUT', claim_url, {'limit_cpus': 16})
                        expect(status, body, 409, 'update expired claim')
                        return 'not_active'
                    r.check('409 growing an expired claim', _not_active)

                    def _still_listed():
                        status, body = client.request('GET', claims_url)
                        expect(status, body, 200, 'list after expiry')
                        if len(body) != 1:
                            raise Failure(
                                'an expired claim should still be listed so '
                                'it can be deleted; listing has %d entries'
                                % len(body))
                        return None
                    r.check('an expired claim is still visible and deletable',
                            _still_listed)

        # ------------------------------------------------------------------
        r.begin('Delete')

        def _delete():
            status, body = client.request('DELETE', claim_url)
            expect(status, body, 200, 'delete claim')
            if body.get('uuid') != state['uuid']:
                raise Failure('delete did not return the claim as it was')
            return None
        deleted = r.check('delete returns the claim as it was', _delete)

        if deleted:
            def _gone():
                status, body = client.request('GET', claim_url)
                expect(status, body, 404, 'read deleted claim')
                return None
            r.check('404 reading the deleted claim', _gone)

            def _recreate():
                status, body = client.request('POST', claims_url, base)
                expect(status, body, 200, 'recreate after delete')
                state['uuid'] = body['uuid']
                return 'capacity was returned'
            r.check('the namespace can claim again after deleting',
                    _recreate)

    except KeyboardInterrupt:
        print('\ninterrupted, cleaning up')
    finally:
        if args.keep:
            print('\n--keep: leaving namespace %s in place' % soak_ns)
        elif created_ns:
            print('\n=== Cleanup ===')
            for uuid in instances:
                status, _ = client.request('DELETE', '/instances/%s' % uuid)
                print('  instance %s: HTTP %s' % (uuid, status))
            for uuid in instances:
                if wait_until_gone(client, '/instances/%s' % uuid):
                    print('  instance %s: gone' % uuid)
                else:
                    print('  instance %s: still present after %ds'
                          % (uuid, CLEANUP_TIMEOUT))
            if state.get('network'):
                net = state['network']
                status, _ = client.request('DELETE', '/networks/%s' % net)
                print('  network %s: HTTP %s' % (net, status))
                # Network deletion is asynchronous (HTTP 202): the API
                # returns before the network object reaches the deleted
                # state. Deleting the namespace while it still owns a
                # network is refused with a 400, so wait for the object
                # to actually go away rather than sleeping and hoping.
                if wait_until_gone(client, '/networks/%s' % net):
                    print('  network %s: gone' % net)
                else:
                    print('  network %s: still present after %ds'
                          % (net, CLEANUP_TIMEOUT))
            status = delete_namespace(client, soak_ns)
            print('  namespace %s: HTTP %s' % (soak_ns, status))
            if status != 200:
                print('  NOTE: the namespace was not removed. Inspect and '
                      'clean up by hand:')
                print('    sf-client --namespace %s namespace delete %s'
                      % (soak_ns, soak_ns))

    return r.report()


if __name__ == '__main__':
    sys.exit(main())
