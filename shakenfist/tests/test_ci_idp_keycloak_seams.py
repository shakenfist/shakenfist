# Copyright 2026 Michael Still and contributors

"""The CI step which stands a real identity provider up for the cluster suite.

``tools/ci-idp-keycloak.sh`` runs on the GitHub runner, starts a provider on the
test cluster's primary and leaves a descriptor there which the cluster CI tests
read. Its caller is in ``shakenfist/actions``, which this repository cannot see,
and the tests which consume it run only against a deployed cluster -- so without
these tests the script has no coverage from either side. That combination has
already cost us once: ``tools/ci-jwks-ca.sh``'s five dependent tests skip when it
has not run, its only caller is in that other repository, and a ``grep`` here for
the script's name finds nothing, which makes "did this fixture ever work" a
question that needs a clone of another repository to answer.

So the script is run here, against a stub ``ssh``, rather than read. Only the
transport and the provider are faked -- the stub records every command, answers
the two questions the script asks for data, and plays the provider for the
requests the script makes on the primary -- while the script's own text, its
guards, the documents it builds and the commands it sends are the real ones.
What that pins:

* it refuses, naming the variable, when ``ci-environment.sh`` was not sourced,
  and it refuses before it has built anything;
* it refuses, naming ``tools/ci-jwks-ca.sh``, when that script's certificate
  authority is not on the primary -- the provider's certificate is issued from
  it, and a certificate from anywhere else is one the cluster refuses;
* the descriptor it writes parses as JSON, carries exactly the keys the cluster
  tests read, and is created under a umask and narrowed to ``u=r,go=`` rather
  than copied and then chmodded, because it holds a password;
* the claim the group arrives in is named in the descriptor as well as valued,
  and both agree with the identity token a real provider of this configuration
  minted, so a test cannot bind a rule to a claim this provider does not emit;
* the certificate request covers the address the cluster is given with a
  ``subjectAltName``, which is the one certificate detail no trust store setting
  can repair;
* every request to the provider is made on the primary and verified against the
  authority there, rather than from the runner, which reaches cluster nodes over
  ssh and nothing else;
* Docker is configured before it is installed, because installing it starts it
  and a daemon that has started has already inserted its rules;
* the provider image is the pinned tag ``tools/idp-lab/docker-compose.yml``
  serves the committed provider captures from, so the fixtures, the operator
  guide's walkthrough and CI all describe one provider version.
"""

import json
import os
import shutil
import subprocess
import tempfile

from shakenfist.tests import base


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCRIPT = 'tools/ci-idp-keycloak.sh'
COMPOSE = 'tools/idp-lab/docker-compose.yml'
FEDERATION_TEST = 'shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_federation.py'
CAPTURED_CLAIMS = 'shakenfist/tests/files/idp/keycloak-id-token-claims.json'

# What the script calls the descriptor on the primary, and what the cluster CI
# tests open there.
DESCRIPTOR_NAME = '.sf-ci-idp.json'

# Exactly what the cluster CI tests read out of the descriptor. A key added to
# the script and not here, or here and not there, fails: the descriptor is the
# whole interface between a shell script on a runner and a test suite on a
# cluster node, and neither can see the other.
DESCRIPTOR_KEYS = {
    'issuer_url', 'client_id', 'oidc_scopes', 'username', 'password',
    'group_claim', 'group', 'image',
}

# What ci-environment.sh exports for a cluster, shaped as shakenfist/actions'
# ci-include-common-localhost.yml writes it: a "nodes" list and each node's
# address. The uuids that file also exports are not needed here.
CLUSTER = {
    'nodes': 'primary sf1 sf2',
    'primary': '10.0.0.10',
    'sf1': '10.0.0.20',
    'sf2': '10.0.0.21',
}


def _read(relative):
    with open(os.path.join(ROOT, relative)) as f:
        return f.read()


# ssh, as far as the script can tell. It records every command as a JSON line,
# captures what the script writes to a file on the primary, answers the two
# questions the script asks for data rather than for an exit status, and -- since
# every request to the provider is made on the primary now -- answers the
# provider's half of Keycloak's administrative API too.
#
# SF_SEAM_CA is how a test says the certificate authority is not there.
SSH_STUB = '''#!/usr/bin/env python3
import base64
import json
import os
import re
import sys

ISSUER = 'https://10.0.0.10:8443/realms/shakenfist'

# ssh is always called as "ssh [-n] <options> user@host <command>", so the
# command is the last argument and the target the one before it. A script which
# called it some other way should fail here rather than be guessed at.
argv = sys.argv[1:]
target, command = argv[-2], argv[-1]
with open(os.path.join(os.environ['SF_SEAM_DIR'], 'calls.jsonl'), 'a') as f:
    f.write(json.dumps({'kind': 'ssh', 'target': target,
                        'command': command}) + chr(10))

# What the script wrote to the primary, kept under the name it wrote it to so a
# test can ask for one file rather than guess which document is which.
written = re.search(r"cat > '([^']+)'", command)
if written:
    name = os.path.basename(written.group(1))
    with open(os.path.join(os.environ['SF_SEAM_DIR'],
                           'wrote-%s' % name), 'w') as f:
        f.write(sys.stdin.read())

if 'home=' in command:
    print('home=/home/debian')
    print('ca=%s' % os.environ.get('SF_SEAM_CA', 'yes'))
    sys.exit(0)

found = re.search(r"https://[^'\\" ]+", command)
if not found:
    # Everything that is not a request to the provider: the certificate, the
    # container, the files, the evidence.
    sys.exit(0)
url = found.group(0)

# A status code is what the script asked for, so the body is irrelevant.
if '--write-out' in command:
    print(201)
    sys.exit(0)


def token(claims):
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()).decode().rstrip('=')
    return 'stub-header.%s.stub-signature' % payload


if '/realms/master/protocol/openid-connect/token' in url:
    body = {'access_token': 'stub-administrative-token'}
elif '/realms/master/.well-known/openid-configuration' in url:
    body = {}
elif 'protocol-mappers/models' in url:
    body = []
elif 'clients?clientId=' in url:
    body = [{'id': 'stub-client-uuid'}]
elif 'users?username=' in url:
    body = [{'id': 'stub-user-uuid'}]
elif 'groups?search=' in url:
    body = [{'id': 'stub-group-uuid'}]
elif 'openid-connect/auth/device' in url:
    body = {'user_code': 'STUB-CODE', 'device_code': 'stub',
            'verification_uri': '%s/device' % ISSUER}
elif 'openid-connect/token' in url:
    body = {'id_token': token({'groups': ['/sf-admins'],
                               'preferred_username': 'person'})}
elif '.well-known/openid-configuration' in url:
    body = {'issuer': ISSUER,
            'authorization_endpoint': '%s/auth' % ISSUER,
            'token_endpoint': '%s/token' % ISSUER,
            'jwks_uri': '%s/certs' % ISSUER,
            'device_authorization_endpoint': '%s/auth/device' % ISSUER}
else:
    sys.exit('the stub provider was asked for %s, which it does not serve' % url)

json.dump(body, sys.stdout)
'''

# scp is not called at all any more, and this is here so that it cannot start
# being called quietly: anything it is handed is recorded and nothing is copied.
SCP_STUB = '''#!/usr/bin/env python3
import json
import os
import sys

with open(os.path.join(os.environ['SF_SEAM_DIR'], 'calls.jsonl'), 'a') as f:
    f.write(json.dumps({'kind': 'scp',
                        'command': ' '.join(sys.argv[1:])}) + chr(10))
'''


class CiIdpKeycloakScriptTestCase(base.ShakenFistTestCase):
    """The script's behaviour, run rather than read."""

    def setUp(self):
        super().setUp()
        self.stubs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.stubs, ignore_errors=True)
        self.log = os.path.join(self.stubs, 'calls.jsonl')
        for command, stub in (('ssh', SSH_STUB), ('scp', SCP_STUB)):
            path = os.path.join(self.stubs, command)
            with open(path, 'w') as f:
                f.write(stub)
            os.chmod(path, 0o755)

    def _run(self, environment=None, ca='yes'):
        """Run the script with only ``environment``, PATH and the stubs set.

        Nothing from the developer's or the runner's own environment leaks in, so
        an ambient ``nodes`` or an exported password cannot make a case pass.
        """
        env = dict(CLUSTER if environment is None else environment)
        env['PATH'] = '%s:%s' % (self.stubs, os.environ['PATH'])
        env['SF_SEAM_DIR'] = self.stubs
        env['SF_SEAM_CA'] = ca
        return subprocess.run(
            ['bash', os.path.join(ROOT, SCRIPT)], env=env, capture_output=True,
            text=True, timeout=120)

    def _calls(self):
        """Every command the script sent, in order, as records."""
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f if line.strip()]

    def _sent(self):
        """The same, as one blob, for the assertions that are substring ones."""
        return '\n'.join(
            '%s %s %s' % (call['kind'], call.get('target', ''),
                          call.get('command', ''))
            for call in self._calls())

    def _commands_containing(self, text):
        return [call['command'] for call in self._calls()
                if text in call.get('command', '')]

    def _descriptor(self):
        """The descriptor the script wrote to the primary, parsed.

        Asked for by the name the script wrote it under, because several of the
        documents it sends are JSON and only this one is the interface.
        """
        path = os.path.join(self.stubs, 'wrote-%s' % DESCRIPTOR_NAME)
        if not os.path.exists(path):
            self.fail('the script wrote no %s to the primary. It sent %s'
                      % (DESCRIPTOR_NAME,
                         sorted(name for name in os.listdir(self.stubs)
                                if name.startswith('wrote-'))))
        with open(path) as f:
            return json.load(f)

    # -- the guards ----------------------------------------------------

    def _assert_refuses(self, environment, reason, ca='yes'):
        result = self._run(environment, ca=ca)
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertIn(reason, result.stderr)

    def test_refuses_without_a_nodes_list(self):
        env = dict(CLUSTER)
        del env['nodes']
        self._assert_refuses(env, 'ci-environment.sh must be sourced')
        # And before it has built anything. A script which starts a provider and
        # then discovers it cannot finish has wasted the run and left a node
        # configured.
        self.assertEqual([], self._calls())

    def test_refuses_without_a_primary(self):
        env = dict(CLUSTER)
        del env['primary']
        self._assert_refuses(env, 'primary: ci-environment.sh must be sourced')
        self.assertEqual([], self._calls())

    def test_refuses_a_node_name_which_is_not_a_variable_name(self):
        env = dict(CLUSTER)
        env['nodes'] = 'primary sf-1'
        self._assert_refuses(env, "names 'sf-1'")
        self.assertEqual([], self._calls())

    def test_refuses_mid_list_when_an_address_is_missing(self):
        env = dict(CLUSTER)
        env['sf2'] = ''
        self._assert_refuses(env, '${sf2} (its address)')
        self.assertEqual([], self._calls())

    def test_refuses_when_the_jwks_certificate_authority_is_absent(self):
        # The failure this names is the one a reader of the lane's log has to be
        # able to act on: the step which mints that authority lives in another
        # repository and may simply not have run.
        self._assert_refuses(CLUSTER, 'tools/ci-jwks-ca.sh', ca='no')
        self.assertEqual([], self._commands_containing('docker run'))

    # -- a complete run ------------------------------------------------

    def test_a_complete_run_succeeds_against_a_stub_provider(self):
        result = self._run()
        self.assertEqual(0, result.returncode,
                         '%s\n%s' % (result.stdout, result.stderr))

    def test_the_descriptor_carries_exactly_the_keys_the_tests_read(self):
        self.assertEqual(0, self._run().returncode)
        descriptor = self._descriptor()
        self.assertEqual(DESCRIPTOR_KEYS, set(descriptor))

    def test_the_descriptor_describes_the_provider_it_started(self):
        self.assertEqual(0, self._run().returncode)
        descriptor = self._descriptor()

        # https, and the primary's own address: the cluster refuses a plaintext
        # issuer, and the address is what the certificate is issued for.
        self.assertTrue(descriptor['issuer_url'].startswith('https://'),
                        descriptor['issuer_url'])
        self.assertIn(CLUSTER['primary'], descriptor['issuer_url'])

        # The group as the identity token carries it, not as the provider names
        # it. The mapper writes full group paths, so a rule which bound
        # "sf-admins" would match nothing and the login would be refused for a
        # reason nothing in the refusal mentions.
        self.assertTrue(descriptor['group'].startswith('/'),
                        descriptor['group'])

        self.assertIn('openid', descriptor['oidc_scopes'])
        self.assertTrue(descriptor['password'])
        self.assertIn('keycloak', descriptor['image'])

    def test_the_group_claim_is_named_as_well_as_valued(self):
        self.assertEqual(0, self._run().returncode)
        descriptor = self._descriptor()
        captured = json.loads(_read(CAPTURED_CLAIMS))

        # The committed capture is an identity token a real provider of this
        # same configuration minted, so it settles which of the two values is
        # the claim's name and which is the string inside it. Swapped, both of
        # these fail -- and a rule bound the wrong way round matches nothing,
        # which is a refused login whose refusal says nothing about why.
        self.assertIn(
            descriptor['group_claim'], captured,
            'an identity token from this configuration carries %s, so a rule '
            'bound to a claim called %r would match nothing'
            % (sorted(captured), descriptor['group_claim']))
        self.assertIn(descriptor['group'], captured[descriptor['group_claim']])
        self.assertFalse(descriptor['group_claim'].startswith('/'),
                         descriptor['group_claim'])

    def test_the_descriptor_is_written_without_a_world_readable_window(self):
        self.assertEqual(0, self._run().returncode)
        self.assertIn('umask 077', self._sent())
        self.assertIn("chmod 0400 '/home/debian/.sf-ci-idp.json'",
                      self._sent())
        # And not copied with scp at all, which would leave a file holding a
        # password world readable with scp's default mode for as long as the
        # chmod took to arrive.
        for call in self._calls():
            if call['kind'] == 'scp':
                self.assertNotIn('.sf-ci-idp.json', call['command'])

    def test_no_credential_is_passed_on_a_command_line(self):
        self.assertEqual(0, self._run().returncode)
        descriptor = self._descriptor()

        # The password reaches the provider in a file curl was pointed at, so
        # it is in nothing's argv on either host -- and the file is removed
        # once the requests that read it are done, because that was its whole
        # purpose.
        for call in self._calls():
            self.assertNotIn(descriptor['password'], call.get('command', ''))
        self.assertIn("--data-urlencode 'password@", self._sent())
        self.assertIn('rm -f', self._commands_containing('person-grant')[-1])

    def test_the_certificate_covers_the_address_the_cluster_is_given(self):
        self.assertEqual(0, self._run().returncode)
        sent = self._sent()
        # A certificate with no subjectAltName is refused by hostname even when
        # the authority which signed it is the only trust anchor, and no bundle
        # setting repairs that.
        self.assertIn('subjectAltName=IP:%s' % CLUSTER['primary'], sent)
        # Signed by the authority the cluster already trusts, rather than by
        # itself, and signed on the primary, where that authority already is.
        self.assertIn('-CA ', sent)
        self.assertIn('.sf-ci-jwks-ca/ca-key.pem', sent)
        issuance = self._commands_containing('openssl req')
        self.assertEqual(1, len(issuance), issuance)
        self.assertEqual(
            ['debian@%s' % CLUSTER['primary']],
            [call['target'] for call in self._calls()
             if 'openssl req' in call.get('command', '')])

    def test_every_request_to_the_provider_is_made_on_the_primary(self):
        self.assertEqual(0, self._run().returncode)
        # Every https request, which is every request to the provider. The one
        # plaintext request this script makes is to sf-api's own port on the
        # node it is already logged in to, which is a different question.
        self.assertTrue(self._commands_containing('https://'))
        for call in self._calls():
            if 'https://' not in call.get('command', ''):
                continue
            # Over ssh, which is the only way anything else in this workflow
            # reaches a cluster node, and never from the runner.
            self.assertEqual('ssh', call['kind'], call)
            # --insecure here would mean the script's own checks said nothing
            # about the certificate the cluster has to accept.
            self.assertNotIn('--insecure', call['command'])
            self.assertNotIn(' -k ', call['command'])
            self.assertIn('--cacert', call['command'])

        # Named one by one, because the loop above can only inspect the
        # requests it was shown: a request moved back onto the runner would
        # simply not be here, and this is what notices that.
        asked = ' '.join(self._commands_containing('https://'))
        for endpoint in ('/realms/master/protocol/openid-connect/token',
                         '/protocol/openid-connect/auth/device',
                         '/protocol/openid-connect/token',
                         '/.well-known/openid-configuration'):
            self.assertIn(endpoint, asked,
                          'nothing asked the primary for %s, so either the '
                          'script stopped asking or it asked from the runner'
                          % endpoint)

    def test_the_container_is_pinned_bounded_and_on_the_host_network(self):
        self.assertEqual(0, self._run().returncode)
        sent = self._sent()
        tag = _image_tag(_read(SCRIPT))
        self.assertIn("docker pull '%s'" % tag, sent)
        self.assertIn(tag, sent.split('docker run')[1])
        # A ceiling, because this shares the primary with sf-api, the suite's
        # virtualenv and five stestr workers.
        self.assertIn('--memory', sent)
        # The host's network namespace, so Docker needs to add no address
        # translation on a node which is also the cluster's network node.
        self.assertIn('--network host', sent)

    def test_docker_is_configured_before_it_is_installed(self):
        self.assertEqual(0, self._run().returncode)
        # Installing the package starts the daemon, and a daemon which has
        # started has already inserted its rules into the forwarding path of a
        # node that forwards instance traffic. Writing the configuration
        # afterwards stops it managing rules from its next restart and undoes
        # none of them, so the order here is the whole mitigation.
        installs = self._commands_containing('apt-get install')
        self.assertEqual(1, len(installs), installs)
        script = installs[0]
        self.assertIn('/etc/docker/daemon.json', script)
        self.assertLess(script.index('/etc/docker/daemon.json'),
                        script.index('apt-get install'),
                        'the daemon.json is written after the install, so the '
                        'daemon has already started with its own rules')

    def test_every_node_is_asked_to_fetch_the_document_sf_api_fetches(self):
        self.assertEqual(0, self._run().returncode)
        asked = {call['target'] for call in self._calls()
                 if "--cacert '/etc/sf/jwks-ca.pem'" in call.get('command', '')}
        # With the installed bundle as the only trust anchor, which is the
        # fetch sf-api makes, and from every node, because sf-api runs on all
        # of them.
        self.assertEqual(
            {'debian@%s' % CLUSTER['primary'], 'debian@%s' % CLUSTER['sf1'],
             'debian@%s' % CLUSTER['sf2']}, asked)


def _image_tag(text):
    """The provider image a script or compose file pins, as one string."""
    for line in text.splitlines():
        stripped = line.strip()
        for prefix in ("IMAGE='", 'image: '):
            if stripped.startswith(prefix):
                return stripped[len(prefix):].strip().strip("'")
    return None


class CiIdpKeycloakPinningTestCase(base.ShakenFistTestCase):
    """What the script shares with the files beside it."""

    def test_the_script_exists_and_is_executable(self):
        path = os.path.join(ROOT, SCRIPT)
        self.assertTrue(os.path.exists(path), '%s is missing' % SCRIPT)
        self.assertTrue(
            os.access(path, os.X_OK),
            '%s is not executable, and its caller runs it with bash, so this is '
            'a warning rather than a break -- but keep it +x' % SCRIPT)

    def test_the_provider_version_matches_the_lab_and_the_captures(self):
        script_tag = _image_tag(_read(SCRIPT))
        compose_tag = _image_tag(_read(COMPOSE))
        self.assertIsNotNone(script_tag, '%s pins no image' % SCRIPT)
        self.assertEqual(
            compose_tag, script_tag,
            '%s serves %s and %s serves %s. The committed provider documents '
            'under shakenfist/tests/files/idp/, the operator guide walkthrough '
            'taken against them and the functional tests which drive the '
            "provider's login page should all be describing one version, so "
            'move both tags in one commit.'
            % (COMPOSE, compose_tag, SCRIPT, script_tag))

    def test_the_certificate_authority_is_the_one_the_tests_already_read(self):
        # The script issues the provider's certificate from the authority
        # tools/ci-jwks-ca.sh left on the primary, and the federated exchange
        # tests issue themselves a leaf from the same one. Two readers of one
        # directory, named in two files, so hold them together.
        self.assertIn('.sf-ci-jwks-ca', _read(SCRIPT))
        self.assertIn('.sf-ci-jwks-ca', _read(FEDERATION_TEST))
