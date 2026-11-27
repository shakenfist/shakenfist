"""Human login, against a real identity provider and a real cluster.

``test_federation.py`` covers the workload exchange against a JWKS
server the test process runs itself, which is the right trade for
testing validation: the test controls the claims and the signature, so
it can assert what the cluster refuses. It is no use at all for testing
a flow. Discovery reads a document we did not write, and the login
exchange trades an identity token a provider minted for its own
reasons, with its own idea of how a group membership is spelled. So
this file covers what only a real provider can cover:

* a trusted issuer configured with the provider's real issuer URL,
  client identifier and scope list,
* the discovery request, against the document the provider publishes,
* a group bound mapping rule and a login through ``/auth/login``, with
  the identity token obtained from the provider's direct access grant,
* the metadata of the key that login mints, including its provenance,
* a refusal when the identity's group matches no rule,
* and which column a login's audience is verified against, which is
  the one misconfiguration an operator can make here and never
  diagnose from the refusal.

The provider is stood up by ``tools/ci-idp-keycloak.sh``, called from a
step in the ``shakenfist/actions`` workflow which deploys the cluster,
and it leaves a descriptor at ``~/.sf-ci-idp.json`` on the host the
suite runs on. ``requests`` is used directly for everything where the
exact status code is the assertion, because the client library maps
responses onto typed exceptions and flattens the distinctions this file
cares about; the administrative routes go through the client, as they
do in ``test_federation.py``.

**Which deployments expect a provider, and how that is decided.** A
test which skips when its fixture is missing, where the thing that
installs the fixture lives in a repository the test cannot see, cannot
be audited from either side: a reader here sees a skip and cannot tell
whether the fixture ever worked, and a reader there sees a step and
cannot tell whether anything consumed it. So these tests fail, rather
than skip, on the deployment that is supposed to have a provider.

That deployment is identified from the cluster rather than from an
environment variable or a job name, because a variable the workflow
sets is the same unauditable arrangement one step removed. The
discriminator is that the host running the suite is *also a
hypervisor*. The provider is only stood up on the one cluster topology
whose primary carries the hypervisor, network and database roles, and
which therefore has the memory to spare; the topology the other
cluster lanes deploy gives the primary none of those roles, so the
suite's host is not a hypervisor there and no provider is expected.
The single machine deployment -- one node holding every role, which the
cluster suite is run against deliberately -- is carved out first with
``sizing.is_single_machine()``, exactly as the structural minimum
assertion carves it out, since its one node is a hypervisor and no
provider is stood up on it.

``shakenfist/tests/test_ci_oidc_login_guard.py`` holds the other half
of that reasoning: it reads the workflows and fails if the topology
carrying the provider is ever moved into the merge queue, where its
primary would be the one without the memory, or if a topology appears
which this discriminator has nothing to say about.

Nothing here writes the person's password or a minted secret into a
test detail. The descriptor holds a credential, and a failure
attachment is read by whoever reads the job.
"""

import json
import os
import time

import jwt
import requests
from testtools import content

from shakenfist_ci import base
from shakenfist_ci import sizing
from shakenfist_client import apiclient


# What tools/ci-idp-keycloak.sh writes, and where. SF_CI_IDP_DESCRIPTOR
# is for a person reproducing a failure by hand against a provider they
# started somewhere else; CI never sets it.
DESCRIPTOR_PATH = os.environ.get(
    'SF_CI_IDP_DESCRIPTOR',
    os.path.join(os.path.expanduser('~'), '.sf-ci-idp.json'))

# Exactly what this file reads out of the descriptor, which is the whole
# interface between a shell script on the runner and this suite on a
# cluster node. shakenfist/tests/test_ci_idp_keycloak_seams.py holds the
# same set against what the script writes, and
# shakenfist/tests/test_ci_oidc_login_guard.py holds the two copies
# together, so a key added to one and not the other fails rather than
# producing a KeyError in a weekly job.
DESCRIPTOR_KEYS = {
    'issuer_url', 'client_id', 'oidc_scopes', 'username', 'password',
    'group_claim', 'group', 'image',
}

# Where tools/ci-jwks-ca.sh left the authority the provider's
# certificate is issued from. The descriptor deliberately does not
# carry a trust anchor: this is the same directory, and the same
# environment override, that test_federation.py already reads.
CI_CA_DIR = os.environ.get(
    'SF_CI_JWKS_CA_DIR',
    os.path.join(os.path.expanduser('~'), '.sf-ci-jwks-ca'))
CI_CA_CERT = os.path.join(CI_CA_DIR, 'ca-cert.pem')

# A capture of this provider version's discovery document, taken in the
# reproducible lab rather than in CI. Its values are the lab's and are
# not this cluster's, so it may be compared for shape and never for
# values: what is asserted below is that the live document lays its
# endpoints out at the same paths relative to its own issuer URL.
DISCOVERY_CAPTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
    'tests', 'files', 'idp', 'keycloak-discovery.json')

# The endpoints a login flow cannot start without, and which discovery
# is the only thing that fills in. Each is read from the document by
# name: this provider publishes device_authorization_endpoint twice, the
# second time inside mtls_endpoint_aliases, so anything which counts
# occurrences rather than indexing by name gets the wrong answer.
LOGIN_ENDPOINTS = ('authorization_endpoint', 'token_endpoint', 'jwks_uri',
                   'device_authorization_endpoint')

# The cluster permits one live trusted issuer per issuer URL, because
# two records claiming one iss value would make which provider's keys
# are trusted depend on listing order. There is one provider and so one
# issuer URL, so the tests in this file take turns: each creates its own
# issuer, deletes it in cleanup, and waits here for its predecessor to
# finish. Comfortably longer than the whole file takes to run against
# five workers, and far shorter than the job timeout, so a leaked issuer
# reports as a failure naming the wait rather than hanging the job.
ISSUER_CLAIM_WAIT = 300
ISSUER_CLAIM_POLL = 3

# What a mapping rule here grants. artifact.read is a scope with an
# endpoint a namespace can reach and an endpoint it cannot, which is
# what makes "scoped as the rule says" assertable in two requests.
RULE_SCOPES = ['artifact.read']
RULE_KEY_TTL = 3600
RULE_KEY_PREFIX = 'ci'


def _endpoint_suffixes(document, issuer_url):
    """Each login endpoint of a discovery document, relative to its issuer.

    The part of a document which can be compared between two
    deployments of one provider version. A value which is not a string,
    or which does not begin with the issuer URL the document belongs
    to, is reported as None rather than guessed at -- an endpoint
    somewhere else entirely is a difference worth failing on, not a
    suffix worth computing.
    """
    out = {}
    for field in LOGIN_ENDPOINTS:
        value = document.get(field)
        if not isinstance(value, str) or not value.startswith(issuer_url):
            out[field] = None
            continue
        out[field] = value[len(issuer_url):]
    return out


class ProviderFixture:
    """The provider, a trusted issuer for it, and a login, for a test class.

    Everything here is machinery rather than assertion: the guard which
    decides whether these tests apply to this deployment at all, the one
    trusted issuer the provider's URL permits, a mapping rule the
    provider's identity token can satisfy, and the two conversations --
    one with the provider, one with the cluster -- a login is made of.

    A mixin because more than one test class needs it and neither may
    inherit the other's test methods. Inherit it **before** the test
    base class, as ``SafeHeaderMixin`` says of itself and for the same
    reason: listed after it, the base class's ``setUp`` wins the method
    resolution order and none of this runs.

    It calls ``self.fail``, ``self.skipTest``, ``self.addCleanup`` and
    the assertions, so it is only usable mixed into a test case. The
    failures it raises are deliberate and are explained where they are
    raised; they are not an unhandled fixture error.
    """

    def setUp(self):
        super().setUp()

        self.base_url = self.system_client.base_url
        self.descriptor = self._require_a_provider()

        self.token = self._bearer(self.namespace, self.namespace_key)
        self.issuer_name = 'ci-idp-%s' % self._uniquifier()
        self.issuer = self._claim_the_issuer()

    # -- is a provider expected here? ----------------------------------

    def _read_descriptor(self):
        """The provider descriptor, or None if no provider was stood up.

        A descriptor which is there and unreadable, or there and not
        JSON, is not the same thing as no provider and is not treated as
        one: the script writes it as a complete document under a umask,
        so a half written one is a failure of something this suite
        cannot see and the test says so.
        """
        if not os.path.exists(DESCRIPTOR_PATH):
            return None

        with open(DESCRIPTOR_PATH) as f:
            body = f.read()
        try:
            descriptor = json.loads(body)
        except ValueError as e:
            self.fail(
                '%s exists but does not parse as JSON (%s). The provider '
                'bring-up writes it in one shot under a umask, so a '
                'malformed one means that step did not finish.'
                % (DESCRIPTOR_PATH, e))

        if not isinstance(descriptor, dict):
            self.fail('%s does not hold a JSON object' % DESCRIPTOR_PATH)
        return descriptor

    def _provider_lane(self):
        """Is this a deployment a provider was stood up on, and why?

        Returns a (expected, because) pair, where `because` is the
        reading this verdict was reached on and is quoted in both the
        failure and the skip, so a job's output says which branch was
        taken rather than only what happened.
        """
        nodes = self.system_client.get_nodes()

        if sizing.is_single_machine(nodes):
            return False, (
                'this is a single machine deployment -- one node holding '
                'every role -- which the cluster suite is run against '
                'deliberately and on which no provider is stood up')

        local = [n for n in nodes if self._node_is_local(n)]
        if not local:
            return False, (
                'the host running this suite is not one of the cluster\'s '
                '%d nodes, so it is not a deployment anything stood a '
                'provider up on' % len(nodes))

        node = local[0]
        if not node.get('is_hypervisor'):
            return False, (
                'the host running this suite is cluster node %s, which is '
                'not a hypervisor -- the topology which carries a provider '
                'gives its primary the hypervisor, network and database '
                'roles together, and this one has none of them'
                % node.get('name'))

        return True, (
            'the host running this suite is cluster node %s, which is also '
            'a hypervisor' % node.get('name'))

    def _require_a_provider(self):
        """The descriptor, or a failure where one was promised, or a skip.

        A present descriptor is believed wherever it is found: something
        started a provider, and these tests are the only thing that can
        say whether it works. An absent one is a failure on the
        deployment that was supposed to have one and a skip anywhere
        else.
        """
        descriptor = self._read_descriptor()
        expected, because = self._provider_lane()

        if descriptor is None:
            if expected:
                self.fail(
                    'There is no identity provider descriptor at %s, but '
                    '%s, which is the deployment a provider is stood up on. '
                    'The step named "Stand up an identity provider" in '
                    'shakenfist/actions\' smoke-cluster.yml runs '
                    'tools/ci-idp-keycloak.sh to create it, after the step '
                    'which trusts a throwaway JWKS certificate authority '
                    'and only for this topology. Either that step did not '
                    'run, or it ran and failed, or the component ref this '
                    'cluster was built from predates the script. This is a '
                    'failure rather than a skip on purpose: a skip here '
                    'cannot be told apart from a provider which has never '
                    'worked.' % (DESCRIPTOR_PATH, because))
            self.skipTest(
                'No identity provider was stood up for this deployment, and '
                'none is expected: %s. The human login path is covered '
                'against a real provider on the topology which carries one; '
                'see this module\'s docstring.' % because)

        missing = sorted(DESCRIPTOR_KEYS - set(descriptor))
        if missing:
            self.fail(
                '%s is missing %s. That file is the whole interface between '
                'the provider bring-up and this suite, so a key this file '
                'reads and that file does not carry is a change to one side '
                'of it only.' % (DESCRIPTOR_PATH, ', '.join(missing)))

        # Named, never valued: the descriptor holds the person's
        # password, and a detail is read by whoever reads the job. The
        # verdict goes in beside it so that a passing run says which
        # branch the guard took, and not only a failing one.
        self.addDetail('provider', content.text_content(
            'image %s, issuer %s, client_id %s, scopes %r, group %r in '
            'claim %r; a provider is %s here because %s'
            % (descriptor['image'], descriptor['issuer_url'],
               descriptor['client_id'], descriptor['oidc_scopes'],
               descriptor['group'], descriptor['group_claim'],
               'expected' if expected else 'not expected', because)))
        return descriptor

    # -- the issuer ----------------------------------------------------

    def _issuer_body(self, client_id=None, audience=None):
        """The configuration of a trusted issuer for the real provider.

        No jwks_uri, which is what makes the discovery fetch mandatory:
        an issuer created without one has nothing to verify a signature
        with unless the provider's published document supplied it, so
        the create fails rather than leaving a broken issuer behind.

        The audience defaults to this cluster's API URL, which is what
        an operator configuring a workload issuer is told to put there.
        Setting it is deliberate rather than incidental -- a login is
        not verified against it, and that is asserted below.
        """
        return {
            'name': self.issuer_name,
            'issuer_url': self.descriptor['issuer_url'],
            'audience': audience or self.base_url,
            'client_id': client_id or self.descriptor['client_id'],
            'oidc_scopes': self.descriptor['oidc_scopes'],
        }

    def _claim_the_issuer(self):
        """Create this test's trusted issuer, waiting for its turn.

        One live issuer may claim one issuer URL, and every test here
        points at the same provider, so a sibling worker holding the URL
        answers 409. Waited out rather than worked around: the
        alternative is a second provider in CI, or tests which share one
        issuer and so cannot each configure it -- and configuring it is
        what the audience assertion below is about.
        """
        deadline = time.time() + ISSUER_CLAIM_WAIT
        waited = False
        while True:
            try:
                issuer = self.system_client._request_url(
                    'POST', '/auth/issuers', data=self._issuer_body()).json()
                self.addCleanup(self._delete_issuer)
                return issuer

            except apiclient.ResourceStateConflictException as e:
                if time.time() > deadline:
                    self.fail(
                        'Another trusted issuer still claims %s after %d '
                        'seconds (%s). One live issuer may claim one issuer '
                        'URL and every test in this file points at the one '
                        'provider, so they take turns -- a wait this long '
                        'means a sibling test left its issuer behind.'
                        % (self.descriptor['issuer_url'], ISSUER_CLAIM_WAIT,
                           e.text))
                if not waited:
                    self.addDetail(
                        'issuer_url_contended', content.text_content(
                            'waiting for %s to be free: %s'
                            % (self.descriptor['issuer_url'], e.text)))
                    waited = True
                time.sleep(ISSUER_CLAIM_POLL)

    def _delete_issuer(self):
        """Release the issuer URL, and say so if it could not be released.

        Cleanup is best effort and must not mask the real assertion, but
        it is not silent either: this is the one failure here which
        cascades, because a leaked issuer keeps the URL claimed and
        every sibling test then waits its whole turn out and fails.
        """
        try:
            self.system_client._request_url(
                'DELETE', '/auth/issuers/' + self.issuer_name)
        except apiclient.ResourceNotFoundException:
            ...
        except Exception as e:
            self.addDetail('issuer_not_deleted', content.text_content(
                'issuer %s for %s could not be deleted (%s: %s), so it still '
                'claims that URL and every sibling test will wait for it'
                % (self.issuer_name, self.descriptor['issuer_url'],
                   type(e).__name__, e)))

    def _repoint_the_issuer(self, client_id=None, audience=None):
        """Rewrite this issuer's client registration, and return the result."""
        return self.system_client._request_url(
            'PUT', '/auth/issuers/' + self.issuer_name,
            data=self._issuer_body(
                client_id=client_id, audience=audience)).json()

    def _discover(self):
        return self.system_client._request_url(
            'POST', '/auth/issuers/%s/discover' % self.issuer_name).json()

    # -- rules ---------------------------------------------------------

    def _rules_url(self):
        return '%s/auth/namespaces/%s/rules' % (self.base_url, self.namespace)

    def _create_rule(self, group=None):
        """A rule this provider's identity token can satisfy.

        The claim's name and the value it carries both come from the
        descriptor, neither hardcoded. This provider ships no group
        claim at all, so the name is one the bring-up chose, and the
        value carries a leading slash because the mapper writes full
        group paths -- a rule has to bind the string the token actually
        carries rather than the group's name.
        """
        name = 'r-%s' % self._uniquifier()
        resp = requests.post(
            self._rules_url(),
            headers={'Authorization': 'Bearer %s' % self.token},
            json={'name': name, 'issuer': self.issuer_name,
                  'bound_claims': {
                      self.descriptor['group_claim']:
                          group or self.descriptor['group']},
                  'scopes': list(RULE_SCOPES), 'key_ttl': RULE_KEY_TTL,
                  'key_name_prefix': RULE_KEY_PREFIX},
            timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)
        return name

    # -- the provider --------------------------------------------------

    def _identity_token(self):
        """An identity token for the person, from the direct access grant.

        The grant is what makes a login testable without a browser or a
        person: the provider is asked for the same identity token a
        completed interactive flow would have produced, and the cluster
        cannot tell the difference because there is none to tell.

        The token endpoint is the one discovery found, read back off the
        issuer, rather than a path built here -- the cluster has already
        been told where this provider's endpoints are, and reading them
        back is one less thing this file has to know about the provider.
        The connection is verified against the authority the provider's
        certificate was issued from, which is the same authority the
        cluster verifies it against.
        """
        token_endpoint = self.issuer.get('token_endpoint')
        self.assertTrue(
            token_endpoint,
            'the issuer has no token_endpoint, so discovery did not fill '
            'one in and there is nowhere to ask for an identity token')
        self.assertTrue(
            os.path.exists(CI_CA_CERT),
            '%s does not exist, so the provider\'s certificate cannot be '
            'verified from here. tools/ci-jwks-ca.sh mints that authority '
            'and leaves it on this host, and the provider\'s certificate is '
            'issued from it.' % CI_CA_CERT)

        resp = requests.post(
            token_endpoint,
            data={'client_id': self.descriptor['client_id'],
                  'grant_type': 'password',
                  'scope': self.descriptor['oidc_scopes'],
                  'username': self.descriptor['username'],
                  'password': self.descriptor['password']},
            verify=CI_CA_CERT, timeout=60)
        self.assertEqual(
            200, resp.status_code,
            'the provider refused the direct access grant: %s'
            % resp.text[:500])

        body = resp.json()
        self.assertIn(
            'id_token', body,
            'the provider answered the grant without an id_token, carrying '
            'only %s. The identity token is the only token this cluster '
            'ever sees.' % sorted(body))
        return body['id_token']

    @staticmethod
    def _claims_of(token):
        """What a token says, without verifying it.

        Verifying it is the cluster's job and the subject of these
        tests. What this is for is asserting that the cluster recorded
        the identity which was actually presented, so it has to read the
        same token the cluster was given rather than a second one.
        """
        return jwt.decode(token, options={'verify_signature': False})

    # -- the cluster ---------------------------------------------------

    def _bearer(self, namespace, key):
        resp = requests.post(
            self.base_url + '/auth',
            json={'namespace': namespace, 'key': key}, timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)
        return resp.json()['access_token']

    def _login(self, token):
        return requests.post(
            self.base_url + '/auth/login', json={'token': token}, timeout=60)

    def _log_in_successfully(self, token=None):
        """Log in, and return the one credential it minted.

        The refusal this endpoint answers with is deliberately the same
        terse string whatever went wrong, because it is unauthenticated
        and anything more makes it an oracle. So a failure here says
        where to read instead rather than quoting a body which says
        nothing.
        """
        resp = self._login(token or self._identity_token())
        self.assertEqual(
            200, resp.status_code,
            'the login was answered %d. Every refusal from this endpoint '
            'carries the same string on purpose, so the reason is in '
            'sf-api\'s log, logged as "Login refused" with the issuer '
            'named.' % resp.status_code)

        minted = resp.json()
        self.assertEqual(
            1, len(minted),
            'one rule matched this identity, so the credential set should '
            'carry one key: %s' % [entry.get('key_name') for entry in minted])
        return minted[0]

    def _key_metadata(self, key_name):
        """One minted key's metadata, read as the system namespace.

        Read as system because provenance records who logged in, and a
        key's provenance is served to the system namespace alone -- a
        namespace's own key listing is read by everyone the namespace
        has trusted, and publishing who logged in and when to each of
        them is a disclosure that cannot be withdrawn.
        """
        return self.system_client._request_url(
            'GET', '/auth/namespaces/%s/keys/%s'
            % (self.namespace, key_name)).json()


class TestHumanLogin(ProviderFixture, base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'oidclogin'
        super().__init__(*args, **kwargs)

    # -- the issuer, and what discovery found --------------------------

    def test_an_issuer_for_the_real_provider_round_trips(self):
        issuers = self.system_client._request_url(
            'GET', '/auth/issuers').json()
        self.assertIn(self.issuer_name, [i['name'] for i in issuers])

        one = self.system_client._request_url(
            'GET', '/auth/issuers/' + self.issuer_name).json()
        self.addDetail('issuer', content.text_content(
            json.dumps(one, indent=4, sort_keys=True)))

        self.assertEqual(self.descriptor['issuer_url'], one['issuer_url'])
        self.assertEqual(self.descriptor['client_id'], one['client_id'])
        self.assertEqual(self.descriptor['oidc_scopes'], one['oidc_scopes'])
        self.assertEqual(self.base_url, one['audience'])

        # The create supplied no jwks_uri, so the one stored is the one
        # the provider publishes. An issuer with none could not verify a
        # signature at all, and the create would have been refused.
        self.assertTrue(
            one['jwks_uri'],
            'the issuer has no jwks_uri, so the create was answered without '
            'discovery having supplied one and nothing can verify a '
            'signature from this provider')
        self.assertTrue(one['jwks_uri'].startswith('https://'),
                        'jwks_uri is %r' % one['jwks_uri'])
        self.assertTrue(one['discovered_at'],
                        'the issuer records no discovery having run')

    def test_discovery_fills_every_login_endpoint_from_the_real_document(self):
        issuer = self._discover()
        self.addDetail('discovered', content.text_content(
            json.dumps(issuer, indent=4, sort_keys=True)))

        issuer_url = self.descriptor['issuer_url']
        for field in LOGIN_ENDPOINTS:
            self.assertTrue(
                issuer.get(field),
                'discovery left %s empty, so a client reading the login '
                'metadata would be told this provider cannot run the flow '
                'that endpoint belongs to' % field)
            self.assertTrue(
                issuer[field].startswith('https://'),
                '%s is %r, and the cluster refuses a plaintext endpoint '
                'because a document fetched over plaintext can be '
                'substituted by anyone on the path' % (field, issuer[field]))

        # Shape, never values. The capture was taken against the lab's
        # own address and this provider is at the cluster's, so what can
        # be compared is where each endpoint sits relative to the issuer
        # URL of the document it came from.
        self.assertTrue(
            os.path.exists(DISCOVERY_CAPTURE),
            '%s is missing, so the live document cannot be compared against '
            'a capture of this provider version. The cluster suite runs out '
            'of a checkout of this repository, which is where that capture '
            'lives.' % DISCOVERY_CAPTURE)
        with open(DISCOVERY_CAPTURE) as f:
            captured = json.load(f)

        self.assertEqual(
            _endpoint_suffixes(captured, captured['issuer']),
            _endpoint_suffixes(issuer, issuer_url),
            'the live provider does not publish its login endpoints at the '
            'paths a capture of this provider version does')

    def test_the_login_metadata_offers_this_issuer(self):
        # Unauthenticated on purpose: a person about to log in holds no
        # credential for this cluster, and this is the only place a
        # client can learn which provider to start a flow at.
        resp = requests.get(self.base_url + '/auth/login', timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)

        offered = [e for e in resp.json() if e['name'] == self.issuer_name]
        self.addDetail('offered', content.text_content(
            json.dumps(offered, indent=4, sort_keys=True)))
        self.assertEqual(
            1, len(offered),
            'the issuer configured for the real provider is not offered for '
            'human login, so no client could start a flow at it')

        entry = offered[0]
        self.assertEqual(self.descriptor['issuer_url'], entry['issuer_url'])
        self.assertEqual(self.descriptor['client_id'], entry['client_id'])
        self.assertEqual(self.descriptor['oidc_scopes'], entry['oidc_scopes'])
        for field in ('authorization_endpoint', 'token_endpoint',
                      'device_authorization_endpoint'):
            self.assertTrue(entry.get(field), '%s is not offered' % field)

        # Withheld deliberately, and this route is unauthenticated: the
        # key set is ours to verify signatures with and no client fetches
        # it, and the audience is how this cluster names itself.
        for field in ('jwks_uri', 'audience', 'uuid'):
            self.assertNotIn(field, entry)

    # -- the login, end to end -----------------------------------------

    def test_a_group_bound_rule_mints_a_working_scoped_key(self):
        self._create_rule()
        minted = self._log_in_successfully()
        self.addDetail('minted_key_name',
                       content.text_content(minted['key_name']))

        self.assertEqual(self.namespace, minted['namespace'])
        self.assertTrue(minted['key_name'].startswith(RULE_KEY_PREFIX + '-'))
        self.assertTrue(minted['key'].startswith('sfk_'),
                        'a cluster minted secret must carry the prefix')
        self.assertEqual(RULE_SCOPES, minted['scopes'])

        # The credential works, which is the whole point of a login.
        token = self._bearer(self.namespace, minted['key'])
        resp = requests.get(
            self.base_url + '/artifacts',
            headers={'Authorization': 'Bearer %s' % token}, timeout=30)
        self.assertEqual(200, resp.status_code, resp.text)

        # And carries only what the rule granted, rather than whatever
        # the person's own namespace key would have reached.
        resp = requests.get(
            self.base_url + '/instances',
            headers={'Authorization': 'Bearer %s' % token}, timeout=30)
        self.assertEqual(
            403, resp.status_code,
            'a key scoped to %s reached the instance list' % RULE_SCOPES)

    def test_the_minted_keys_metadata_reports_its_grant(self):
        rule = self._create_rule()
        identity = self._identity_token()
        claims = self._claims_of(identity)
        before = time.time()
        minted = self._log_in_successfully(identity)

        metadata = self._key_metadata(minted['key_name'])
        self.addDetail('key_metadata', content.text_content(
            json.dumps(metadata, indent=4, sort_keys=True)))

        self.assertEqual(self.namespace, metadata['namespace'])
        self.assertEqual(minted['key_name'], metadata['key_name'])
        self.assertEqual(RULE_SCOPES, metadata['scopes'])

        # The lifetime the rule grants, counted from the mint. Bounded
        # on both sides: a key with no expiry is a permanent grant, and
        # one expiring far later than the rule says is a rule which is
        # not in force.
        self.assertIsNotNone(
            metadata['expiry'],
            'a key minted by a login must expire; the rule granted a '
            'lifetime of %d seconds' % RULE_KEY_TTL)
        self.assertGreaterEqual(metadata['expiry'], before + RULE_KEY_TTL)
        self.assertLessEqual(metadata['expiry'],
                             time.time() + RULE_KEY_TTL)

        # The two answers describe the same moment. To the second rather
        # than exactly: the response carries the value the mint computed
        # and the metadata carries it back out of the database, and what
        # is worth asserting is that a client which cached the one is not
        # being told something different by the other -- not how a float
        # survives a column.
        self.assertAlmostEqual(
            minted['expiry'], metadata['expiry'], delta=1,
            msg='the login and the key metadata disagree on when this '
                'credential stops working')

        # Provenance names the person who logged in, the rule which
        # entitled them and the claims which actually satisfied it --
        # the grant as it was made, rather than as the rule reads now.
        provenance = metadata['provenance']
        self.assertEqual('login', provenance['source'])
        self.assertEqual(self.issuer_name, provenance['issuer'])
        self.assertEqual(rule, provenance['rule_name'])
        self.assertEqual(claims['sub'], provenance['sub'],
                         'the provenance records a different subject from '
                         'the identity token which was presented')
        self.assertEqual(claims['jti'], provenance['jti'])
        self.assertEqual(
            {self.descriptor['group_claim']: [self.descriptor['group']]},
            provenance['claims'],
            'an array claim records only the elements which matched, so a '
            'group bound rule should record the one group it was satisfied '
            'by and not the person\'s whole membership')

    def test_an_identity_whose_group_matches_no_rule_is_refused(self):
        # A rule bound to a group this person is not in, rather than no
        # rule at all: both are refused, and only this one proves the
        # claim comparison is what refused it.
        other = self.descriptor['group'] + '-nobody'
        self._create_rule(group=other)

        resp = self._login(self._identity_token())
        self.assertEqual(
            401, resp.status_code,
            'an identity whose %s claim carries %r was logged in through a '
            'rule bound to %r'
            % (self.descriptor['group_claim'], self.descriptor['group'],
               other))

        # And nothing was minted on the way to the refusal.
        keys = self.system_client._request_url(
            'GET', '/auth/namespaces/%s/keys' % self.namespace).json()
        self.assertEqual(
            ['test'], sorted(keys),
            'the refused login left a key behind in %s' % self.namespace)

    def test_a_login_is_verified_against_the_client_id_not_the_api_url(self):
        """Which column a human token's audience is checked against.

        An operator configuring an issuer is told to put their cluster's
        API URL in `audience`, because that is the audience a workload
        asks its provider to stamp. A person's identity token cannot
        carry it: OpenID Connect requires an identity token's audience
        to be the client identifier of the relying party it was issued
        for, and that is what this provider stamps. So the two are
        different fields stamped by different parties, and a login is
        verified against `client_id`.

        That is not visible from either end. The issuer created here
        already has the API URL in `audience` and logs in fine, which is
        the half an operator would never think to check; and an issuer
        whose *client registration* is the API URL -- the mistake
        available to anyone who conflates the two columns -- refuses
        every login with a deliberately terse refusal that gives them
        nothing to work from. Both halves are asserted here, on one
        issuer and one identity token, so that neither can be true
        without the other.
        """
        self._create_rule()

        # The conflation: the API URL in the client registration as well
        # as in the audience.
        repointed = self._repoint_the_issuer(client_id=self.base_url)
        self.assertEqual(self.base_url, repointed['client_id'])
        self.assertEqual(self.base_url, repointed['audience'])

        identity = self._identity_token()
        self.assertEqual(
            self.descriptor['client_id'], self._claims_of(identity)['aud'],
            'the provider does not stamp its client identifier as the '
            'identity token\'s audience, so there is no trap to assert')

        resp = self._login(identity)
        self.assertEqual(
            401, resp.status_code,
            'an issuer whose client registration is this cluster\'s API URL '
            'accepted an identity token stamped with the provider\'s client '
            'identifier, so a login is not verified against client_id after '
            'all')

        # The same issuer, with the client registration the provider
        # actually knows, accepts the same token. The same one on
        # purpose: a refused login is refused before the replay claim,
        # which is the last gate before minting, so the token is still
        # unspent. If this answers 401 as a replay, that order has
        # changed and a refusal now spends the identity it refused.
        restored = self._repoint_the_issuer()
        self.assertEqual(self.descriptor['client_id'], restored['client_id'])
        self.assertEqual(self.base_url, restored['audience'])

        minted = self._log_in_successfully(identity)
        self.assertEqual(self.namespace, minted['namespace'])
        self.assertEqual(RULE_SCOPES, minted['scopes'])
