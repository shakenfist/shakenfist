# Copyright 2019 Michael Still and contributors
"""Namespace capacity claims, against a real cluster.

A claim is a namespace's promise of aggregate capacity: creating one is
itself a guarded admission decision against the cluster, an instance
create in a claimed namespace draws the claim down instead of the
cluster's unclaimed sums, and -- for this release -- exceeding a claim
is *recorded* rather than refused (D16's advisory period). None of that
is exercised by a test which mocks the database away, so it is exercised
here.

What this file defends
----------------------

The client verbs, which are what an operator actually uses. Every
request here goes through ``shakenfist_client.apiclient``'s claim
methods, via the ``_claim_api()`` adapter below -- which exists because
the verbs raise typed exceptions and a refusal's *status code* is
frequently the assertion, so it hands each request back as a
``(status, body)`` pair.

This file used to drive the endpoints through
``apiclient.Client._request_url()`` instead, on the reasoning that the
collection installs ``shakenfist-client`` from PyPI, so a test written
against new ``apiclient`` methods could not pass in CI until a client
release existed. That reasoning (phase 4's decision D7) was wrong, and
had been wrong since 2026-06-24: cluster CI does not install the
released client. It builds a wheel from a ``client-python`` checkout at
``develop`` -- see "Where the functional jobs get their code" in
``docs/developer_guide/ci.md`` -- so a verb is available here as soon
as it merges, with no release involved. Phase 4b
(``docs/plans/PLAN-scheduler-reservations-phase-04b-client.md``) added
the verbs and moved this file onto them.

Why success assertions retry 507
--------------------------------

This file runs concurrently with the rest of the cluster CI suite, on
one cluster, across four stestr workers. Creating or growing a claim is
a guarded admission against the ``cluster_capacity`` singleton, so it
needs free cluster capacity at that instant -- capacity nothing here
ever reserved, and which the sibling tests are free to be holding. When
they were, this file failed asserting 200 against a 507 the server was
right to send (issue 3907, three occurrences in one day, one of them
6.6 seconds after the preceding test on the same worker released two
instances whose deletion was still in flight).

So every claim request whose *success* is asserted goes through
``_claim_api_awaiting_headroom()``, which treats a full cluster as the
transient condition it is on a busy CI cluster. Requests whose
*refusal* is asserted must not: they use
``_claim_api_awaiting_accounting()``, which retries only 503, so a 507
the test wants to see is returned immediately rather than retried for
the whole wait.

What this file deliberately does not assert
-------------------------------------------

* **That the cluster's unclaimed usage did not move.** No REST endpoint
  publishes ``cluster_capacity``: ``/admin/resources`` reports the
  scheduler's per-node view, not the claim ledger. The claim branch and
  the unclaimed branch of ``_direct_admit_instance_placement()`` are
  mutually exclusive by construction (an ``elif``), so observing the
  claim charge is observing that the unclaimed sums were not charged --
  but that is an argument from reading the code, not an assertion, and
  it is worth knowing which of the two you have. The unit tests in
  ``shakenfist/tests/`` assert the counters directly.
* **The ``Namespace.hard_delete()`` cascade to claims.** ``DELETE
  /auth/namespaces/<ns>`` is a soft delete; ``hard_delete()`` (and so
  the cascade which returns a claim's capacity) runs only when the
  cluster daemon collects the namespace ``CLEANER_DELAY`` later, an hour
  by default. That is longer than any functional test may wait, so the
  cascade is covered by unit tests and the claim's *own* delete -- which
  is synchronous, and returns the same capacity through the same
  transaction -- is what is covered here.
* **That the reconciler agrees with these counters.** It recomputes
  every counter from ground truth every five minutes; asserting the
  agreement means waiting out a period, which belongs in a soak rather
  than in the suite.
* **Anything about the command line.** ``sf-client namespace claim``
  exists as of phase 4b, but its argument parsing and output formatting
  are covered by unit tests in ``client-python``. What this file defends
  is the API surface those commands sit on.
"""

import functools
import json
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_ci import retries
from shakenfist_client import apiclient


# The advisory audit event Instance._event_claim_over_limit() writes when
# a placement pushes a namespace past what it claimed. Matched exactly:
# it is deliberately distinct from the P5 event ("placement recorded
# despite exceeding capacity guard"), which is a ground-truth write
# exceeding a *node* guard, and a test which matched loosely would not
# be able to tell the two apart.
CLAIM_OVER_LIMIT_MESSAGE = 'placement admitted over namespace capacity claim'

# Long enough that no test here races its own claim expiring, short
# enough that a claim leaked by a hard test failure stops holding
# cluster capacity within the hour.
CLAIM_EXPIRY_SECONDS = 3600

# The shape of every instance created here, and therefore the unit the
# claims are denominated in. Kept to the smallest instance the suite
# uses elsewhere: these tests are about accounting, not about capacity,
# and must not depend on how big the CI cluster is.
INSTANCE_CPUS = 1
INSTANCE_MEMORY_MB = 1024
INSTANCE_DISK_GB = 8

# A claim no cluster can promise, for the capacity refusal. Absurd by
# design rather than derived from the cluster's real totals, which are
# not published anywhere this test can read them.
IMPOSSIBLE_CPUS = 1000000000

# How long to keep retrying a claim request the cluster answered 503 to.
# One reconcile period (five minutes) plus slack: see
# _claim_api_awaiting_accounting().
CAPACITY_ACCOUNTING_WAIT = 420

# How long to keep retrying a claim request the cluster answered 507 to,
# where the caller is about to assert success. The capacity the request
# needs is usually held by the rest of the suite, and instance deletion
# returns it asynchronously -- full-now is not full-soon (issue 3907).
# Comfortably longer than any sibling test holds its instances, far
# shorter than the job timeout.
CLUSTER_HEADROOM_WAIT = 420


class _ClaimTarget:
    """What a claim request is aimed at: a namespace, and maybe a claim.

    The request itself goes through the client verbs, which build their
    own paths; this carries the equivalent path only so that a failure
    message can name what was asked for.
    """

    def __init__(self, namespace, claim_uuid=None):
        self.namespace = namespace
        self.claim_uuid = claim_uuid

    def __str__(self):
        path = '/auth/namespaces/%s/claims' % self.namespace
        if self.claim_uuid:
            path = '%s/%s' % (path, self.claim_uuid)
        return path


class ClaimAPIMixin:
    """The claim endpoints, driven through the client verbs."""

    def setUp(self):
        super().setUp()
        self._created_claims = []

    def tearDown(self):
        # Claims are removed here rather than through addCleanup(),
        # because testtools runs cleanups *after* tearDown -- by which
        # point BaseNamespacedTestCase has deleted the namespace and
        # every claim endpoint answers 404 for it. The claim row would
        # then survive, holding cluster_capacity.claimed_* against the
        # rest of the suite, until the cluster daemon collected the
        # namespace CLEANER_DELAY (an hour, by default) later.
        problems = []
        try:
            for claim_uuid in getattr(self, '_created_claims', []):
                status, body = self._claim_api(
                    'DELETE', self._claim_target(claim_uuid))
                if status not in (200, 404):
                    problems.append(
                        '%s: DELETE answered %s'
                        % (claim_uuid, self._describe(status, body)))
        except Exception as e:
            # _claim_api() unwraps APIException and nothing else. A client
            # which predates the verbs (AttributeError), a connection
            # error or a client-side timeout would otherwise escape this
            # loop and skip the namespace delete below, leaking a
            # namespace and its instances on a shared CI cluster -- a
            # worse outcome than the leaked claim this method exists to
            # prevent.
            problems.append(
                'the claim delete loop raised %s: %s'
                % (type(e).__name__, e))
        finally:
            super().tearDown()

        if problems:
            self.fail(
                'Claims could not be removed, so they hold cluster capacity '
                'until they expire: %s' % '; '.join(problems))

    def _claims_target(self, namespace=None):
        return _ClaimTarget(
            self.namespace if namespace is None else namespace)

    def _claim_target(self, claim_uuid, namespace=None):
        return _ClaimTarget(
            self.namespace if namespace is None else namespace, claim_uuid)

    def _claim_api(self, method, target, data=None, client=None):
        """One claim request through the client verbs, as (status, body).

        The verbs map status codes onto typed exceptions, which is the
        wrong shape for a test whose assertion is often the code itself,
        so they are unwrapped here. The body is the decoded JSON where
        there is any -- a refusal body is ``{"error": ..., "status":
        ...}`` -- and the raw text otherwise, so a failure message can
        always print something.

        The success status is structural rather than observed: a verb
        returns decoded JSON and not a response, so a 200 from here
        means "the verb returned" rather than "the server said 200".
        That is exact for every claim endpoint today -- none of them is
        asynchronous -- but it does mean the success-path status
        assertions can no longer fail, and would not notice an endpoint
        which grew a 202.

        Keeping this adapter, rather than calling the verbs from each
        test, is deliberate. Every status assertion in this file stays
        as it was written; ``shakenfist_ci.retries`` keeps its
        (status, body) contract and its freedom from ``shakenfist_client``
        imports, which ``shakenfist/tests/test_ci_claims_headroom.py``
        checks by loading it by path; and every request in the file goes
        through a verb, which is the point of the exercise.
        """
        if client is None:
            client = self.system_client

        namespace = target.namespace
        claim_uuid = target.claim_uuid
        if data and method in ('GET', 'DELETE'):
            raise NotImplementedError(
                'no claim verb sends a request body with %s, so the data '
                'passed for %s would be silently discarded'
                % (method, target))
        data = data or {}

        if method == 'GET' and claim_uuid is None:
            call = functools.partial(client.get_namespace_claims, namespace)
        elif method == 'GET':
            call = functools.partial(
                client.get_namespace_claim, namespace, claim_uuid)
        elif method == 'POST':
            call = functools.partial(
                client.create_namespace_claim, namespace, **data)
        elif method == 'PUT':
            call = functools.partial(
                client.update_namespace_claim, namespace, claim_uuid, **data)
        elif method == 'DELETE':
            call = functools.partial(
                client.delete_namespace_claim, namespace, claim_uuid)
        else:
            raise NotImplementedError(
                'no claim verb for %s %s' % (method, target))

        try:
            # Every claim endpoint answers 200 on success -- none of them
            # is asynchronous, so there is no 202 to distinguish.
            return 200, call()
        except apiclient.APIException as e:
            try:
                return e.status_code, json.loads(e.text)
            except (TypeError, ValueError):
                return e.status_code, e.text

    def _describe(self, status, body):
        return 'HTTP %s with body %s' % (
            status, json.dumps(body, sort_keys=True, default=str))

    def _claim_api_awaiting_accounting(self, method, target, data=None):
        """A claim request, retried for as long as the cluster says 503.

        503 is the transient half of the refusal mapping. It means
        either that the reconciler has not built the ``cluster_capacity``
        singleton yet (``no_cluster_capacity``) or that the claim row was
        being changed concurrently and the optimistic re-probe gave up
        (``conflict``). Both say "retry", and the first is a genuine cold
        start: the singleton exists only from the first reconcile pass,
        which runs every five minutes on the elected cluster node. A
        suite which starts inside that window would otherwise fail for a
        reason that is nothing to do with claims.

        Anything else is returned immediately, refusals included. Only
        503 is retried, so a 507 capacity refusal is not silently waited
        out -- which makes this the wrapper for every request whose
        *refusal* is the assertion.
        """
        return retries.retry_while_transient(
            lambda: self._claim_api(method, target, data=data),
            transient_statuses=(503,),
            deadline=time.time() + CAPACITY_ACCOUNTING_WAIT)

    def _claim_api_awaiting_headroom(self, method, target, data=None):
        """A claim request the caller will assert succeeded, retried
        while the cluster is full as well as while it says 503.

        Creating or growing a claim is a guarded admission against the
        cluster_capacity singleton, so it needs free cluster capacity
        at that instant -- capacity this suite never reserved, and
        which the sibling tests running concurrently on the same
        cluster are free to be holding. A 507 here is the server being
        right about a moment rather than about the cluster: instances
        the rest of the suite is already deleting return their capacity
        asynchronously, so the refusal is transient in exactly the way
        the 503s are (issue 3907). A cluster which stays full for the
        whole wait still fails the caller's assertion, with the refusal
        body in the failure message.

        Only for callers asserting success. A caller asserting a 507
        refusal (the IMPOSSIBLE_CPUS probe) must use
        _claim_api_awaiting_accounting instead: this wrapper would
        retry that refusal for the whole wait before handing it back.
        """
        return retries.retry_while_transient(
            lambda: self._claim_api(method, target, data=data),
            transient_statuses=(503, 507),
            deadline=time.time() + CLUSTER_HEADROOM_WAIT)

    def _create_claim(self, limit_cpus, limit_memory_mb, limit_disk_gb,
                      expires_in_seconds=CLAIM_EXPIRY_SECONDS,
                      detail='claim as created'):
        body = {
            'limit_cpus': limit_cpus,
            'limit_memory_mb': limit_memory_mb,
            'limit_disk_gb': limit_disk_gb,
            'expires_in_seconds': expires_in_seconds
        }
        status, claim = self._claim_api_awaiting_headroom(
            'POST', self._claims_target(), data=body)
        self.assertEqual(
            200, status,
            'POST %s with %s did not create a claim, it answered %s'
            % (self._claims_target(), json.dumps(body, sort_keys=True),
               self._describe(status, claim)))

        self.addDetail(detail, content.text_content(json.dumps(
            claim, indent=4, sort_keys=True, default=str)))
        self._created_claims.append(claim['uuid'])
        return claim

    def _get_claim(self, claim_uuid):
        status, claim = self._claim_api(
            'GET', self._claim_target(claim_uuid))
        self.assertEqual(
            200, status,
            'GET %s did not return the claim, it answered %s'
            % (self._claim_target(claim_uuid), self._describe(status, claim)))
        return claim

    def _claim_used(self, claim):
        """A claim's drawdown, in the dimension order used everywhere."""
        return (claim['used_cpus'], claim['used_memory_mb'],
                claim['used_disk_gb'])

    def _claim_limits(self, claim):
        return (claim['limit_cpus'], claim['limit_memory_mb'],
                claim['limit_disk_gb'])

    def _await_claim_used(self, claim_uuid, expected, why, timeout=120):
        """Poll a claim until its drawdown is expected, then assert it.

        The counters are written inside the placement transaction, so
        they are already correct by the time an instance reaches the
        created state -- but polling costs nothing when they are, and
        turns "the assertion ran a second early" into a real failure
        rather than a flake.
        """
        expected = tuple(expected)
        deadline = time.time() + timeout
        while True:
            claim = self._get_claim(claim_uuid)
            observed = self._claim_used(claim)
            if observed == expected or time.time() > deadline:
                break
            time.sleep(2)

        self.assertEqual(
            expected, observed,
            'Claim %s does not hold the drawdown %s should have left it '
            'with. Expected (cpus, memory_mb, disk_gb) of %s but the '
            'claim reports %s after polling for up to %d seconds. The '
            'whole claim reads %s'
            % (claim_uuid, why, expected, observed, timeout,
               json.dumps(claim, sort_keys=True, default=str)))
        return claim

    def _instance_footprint(self, inst):
        """What the capacity ledger charges for an instance.

        The same three numbers Instance._capacity_claim computes: cpus,
        memory in megabytes, and the summed virtual size of the disk
        spec (mariadb.disk_spec_virtual_gb, simplified here to the
        integer sizes the suite actually creates).
        """
        disk_gb = 0
        for disk in inst.get('disk_spec') or []:
            size = disk.get('size')
            if isinstance(size, (int, float)) and not isinstance(size, bool):
                disk_gb += int(size)
        return (inst['cpus'], inst['memory'], disk_gb)

    def _claim_over_limit_events(self, instance_uuid, wait=0):
        """The advisory audit events on an instance, optionally awaited.

        Read at the API's maximum limit rather than its default of 100.
        The events come back newest first, and the advisory event is
        written at placement -- which is the *oldest* thing that happens
        to an instance -- so a busy instance could push it off the end
        of a default read and turn a working feature into an absence.
        """
        deadline = time.time() + wait
        while True:
            events = self.system_client.get_instance_events(
                instance_uuid, limit=1000)
            matched = [
                e for e in events
                if str(e.get('message', '')) == CLAIM_OVER_LIMIT_MESSAGE]
            if matched or time.time() > deadline:
                self.addDetail(
                    'events for instance %s' % instance_uuid,
                    content.text_content(json.dumps(
                        events, indent=4, sort_keys=True, default=str)))
                return matched
            time.sleep(2)


class TestNamespaceClaimLifecycle(ClaimAPIMixin, base.BaseNamespacedTestCase):
    """Claim CRUD and the refusals, with no instances involved."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'claimcrud'
        super().__init__(*args, **kwargs)

    def test_claim_lifecycle_and_refusals(self):
        # A namespace with no claim lists none. Asserted rather than
        # assumed, because every count below is a delta from here.
        status, listing = self._claim_api('GET', self._claims_target())
        self.assertEqual(
            200, status,
            'GET %s answered %s' % (self._claims_target(),
                                    self._describe(status, listing)))
        self.assertEqual(
            [], listing,
            'A namespace which has never claimed anything listed claims: %s'
            % json.dumps(listing, sort_keys=True, default=str))

        # A claim the cluster cannot promise is refused with 507, the
        # answer this API gives for every other capacity exhaustion. Run
        # before the real claim is created, because a namespace which
        # already holds an active claim is refused with 409 by the
        # earlier probe and would never reach the capacity guard.
        status, body = self._claim_api_awaiting_accounting(
            'POST', self._claims_target(),
            data={'limit_cpus': IMPOSSIBLE_CPUS, 'limit_memory_mb': 1,
                  'limit_disk_gb': 1,
                  'expires_in_seconds': CLAIM_EXPIRY_SECONDS})
        self.assertEqual(
            507, status,
            'A claim for %d cpus should not fit in any cluster, but the '
            'API answered %s'
            % (IMPOSSIBLE_CPUS, self._describe(status, body)))

        # Created with the cpu limit it will hold at its largest, so
        # the growth exercised below is re-taking capacity its own
        # shrink just released rather than competing with the rest of
        # the suite for a free cpu this test never reserved (issue
        # 3907).
        claim = self._create_claim(
            3 * INSTANCE_CPUS, 2 * INSTANCE_MEMORY_MB, 2 * INSTANCE_DISK_GB)

        # The two states are two different facts (D2): existence in
        # state, coverage beside it in coverage_state. A view which
        # collapsed them would be the first step towards the code doing
        # the same, so both are asserted.
        self.assertEqual(
            'created', claim.get('state'),
            'A new claim should be a created object: %s'
            % json.dumps(claim, sort_keys=True, default=str))
        self.assertEqual(
            'active', claim.get('coverage_state'),
            'A new claim should cover placements: %s'
            % json.dumps(claim, sort_keys=True, default=str))
        self.assertEqual(
            self.namespace, claim.get('namespace'),
            'The claim was created in the wrong namespace: %s'
            % json.dumps(claim, sort_keys=True, default=str))
        self.assertEqual(
            (3 * INSTANCE_CPUS, 2 * INSTANCE_MEMORY_MB,
             2 * INSTANCE_DISK_GB), self._claim_limits(claim),
            'The claim does not carry the limits it was asked for: %s'
            % json.dumps(claim, sort_keys=True, default=str))
        self.assertEqual(
            (0, 0, 0), self._claim_used(claim),
            'A claim created for a namespace holding no instances should '
            'have drawn nothing down: %s'
            % json.dumps(claim, sort_keys=True, default=str))

        # Fetchable by uuid, and listed.
        fetched = self._get_claim(claim['uuid'])
        self.assertEqual(
            claim['uuid'], fetched['uuid'],
            'GET of claim %s returned claim %s'
            % (claim['uuid'], fetched['uuid']))

        status, listing = self._claim_api('GET', self._claims_target())
        self.assertEqual(
            [claim['uuid']], [c['uuid'] for c in listing],
            'The namespace should list exactly the claim it was given, '
            'but %s answered %s'
            % (self._claims_target(), self._describe(status, listing)))

        # The namespace segment of the claim URL is not decorative: a
        # claim addressed through a namespace which does not own it is
        # the same 404 a missing claim is, so the URL cannot be used to
        # discover which claims exist elsewhere.
        other = self._claim_target(claim['uuid'], namespace='system')
        status, body = self._claim_api('GET', other)
        self.assertEqual(
            404, status,
            'Claim %s was served through the system namespace\'s URL, '
            'which does not own it: %s'
            % (claim['uuid'], self._describe(status, body)))

        # One active claim per namespace.
        status, body = self._claim_api(
            'POST', self._claims_target(),
            data={'limit_cpus': 1, 'limit_memory_mb': 1, 'limit_disk_gb': 1,
                  'expires_in_seconds': CLAIM_EXPIRY_SECONDS})
        self.assertEqual(
            409, status,
            'A second claim for a namespace which already holds an active '
            'one should be refused, but the API answered %s'
            % self._describe(status, body))

        # An update with no fields is a client error, not a silent
        # zeroing of every dimension the caller did not mention (the
        # field mask, CLAUDE.md pitfall 3).
        status, body = self._claim_api(
            'PUT', self._claim_target(claim['uuid']))
        self.assertEqual(
            400, status,
            'An update naming no fields should be refused, but the API '
            'answered %s' % self._describe(status, body))
        unchanged = self._get_claim(claim['uuid'])
        self.assertEqual(
            self._claim_limits(claim), self._claim_limits(unchanged),
            'An update naming no fields changed the claim\'s limits, from '
            '%s to %s'
            % (self._claim_limits(claim), self._claim_limits(unchanged)))

        # Shrinking one dimension leaves the others where they were:
        # the update is field masked, so an unmentioned limit is
        # untouched rather than reset. A shrink to a limit at or above
        # the claim's usage is always admissible, so it needs no free
        # cluster capacity and no headroom tolerance.
        status, shrunk = self._claim_api_awaiting_accounting(
            'PUT', self._claim_target(claim['uuid']),
            data={'limit_cpus': 2 * INSTANCE_CPUS})
        self.assertEqual(
            200, status,
            'Shrinking the cpu limit of claim %s was refused: %s'
            % (claim['uuid'], self._describe(status, shrunk)))
        self.assertEqual(
            (2 * INSTANCE_CPUS, 2 * INSTANCE_MEMORY_MB,
             2 * INSTANCE_DISK_GB), self._claim_limits(shrunk),
            'Shrinking only the cpu limit changed something else: %s'
            % json.dumps(shrunk, sort_keys=True, default=str))

        # And growing it back, which is field masked the same way. The
        # growth is a guarded admission, so it does need a free cpu --
        # the one the shrink above released a moment ago. The
        # awaiting-headroom wrapper covers the window in which a
        # concurrent test takes even that.
        status, grown = self._claim_api_awaiting_headroom(
            'PUT', self._claim_target(claim['uuid']),
            data={'limit_cpus': 3 * INSTANCE_CPUS})
        self.assertEqual(
            200, status,
            'Growing the cpu limit of claim %s was refused: %s'
            % (claim['uuid'], self._describe(status, grown)))
        self.assertEqual(
            (3 * INSTANCE_CPUS, 2 * INSTANCE_MEMORY_MB,
             2 * INSTANCE_DISK_GB), self._claim_limits(grown),
            'Growing only the cpu limit changed something else: %s'
            % json.dumps(grown, sort_keys=True, default=str))

        # Expiry is re-datable, and is computed from the server's clock.
        # The two values are compared against each other rather than
        # against this host's clock for exactly that reason.
        status, redated = self._claim_api_awaiting_accounting(
            'PUT', self._claim_target(claim['uuid']),
            data={'expires_in_seconds': 2 * CLAIM_EXPIRY_SECONDS})
        self.assertEqual(
            200, status,
            'Re-dating claim %s was refused: %s'
            % (claim['uuid'], self._describe(status, redated)))
        self.assertGreater(
            redated['expires_at'], grown['expires_at'],
            'Doubling expires_in_seconds did not move the claim\'s expiry '
            'forward: it was %s and is now %s'
            % (grown['expires_at'], redated['expires_at']))

        # A namespace owner is not a cluster administrator. The claim
        # endpoints are admin only, and the namespace's own key must not
        # reach them.
        status, body = self._claim_api(
            'GET', self._claims_target(), client=self.test_client)
        self.assertEqual(
            401, status,
            'The namespace\'s own credential reached the admin-only claim '
            'listing: %s' % self._describe(status, body))

        # Deletion answers with the claim as it was, and then there is
        # nothing left: no row to fetch, nothing in the listing, and a
        # second delete is harmless.
        status, deleted = self._claim_api(
            'DELETE', self._claim_target(claim['uuid']))
        self.assertEqual(
            200, status,
            'Deleting claim %s answered %s'
            % (claim['uuid'], self._describe(status, deleted)))
        self.assertEqual(
            claim['uuid'], deleted.get('uuid'),
            'Deleting claim %s answered with claim %s'
            % (claim['uuid'], deleted.get('uuid')))

        status, body = self._claim_api('GET', self._claim_target(claim['uuid']))
        self.assertEqual(
            404, status,
            'Claim %s is still fetchable after being deleted: %s'
            % (claim['uuid'], self._describe(status, body)))

        status, listing = self._claim_api('GET', self._claims_target())
        self.assertEqual(
            [], listing,
            'The namespace still lists claims after its only claim was '
            'deleted: %s' % json.dumps(listing, sort_keys=True, default=str))

        status, body = self._claim_api(
            'DELETE', self._claim_target(claim['uuid']))
        self.assertEqual(
            404, status,
            'Deleting claim %s twice should be harmless, but the second '
            'delete answered %s'
            % (claim['uuid'], self._describe(status, body)))

        # And the namespace can claim again. This is the observable end
        # of "the delete gave the capacity back": the create is refused
        # with 409 while any active claim exists, so a claim which had
        # merely been hidden rather than removed would refuse this.
        replacement = self._create_claim(
            INSTANCE_CPUS, INSTANCE_MEMORY_MB, INSTANCE_DISK_GB,
            detail='replacement claim')
        self.assertNotEqual(
            claim['uuid'], replacement['uuid'],
            'The replacement claim reused the deleted claim\'s uuid, %s'
            % claim['uuid'])


class TestNamespaceClaimAccounting(ClaimAPIMixin,
                                   base.BaseNamespacedTestCase):
    """What placements do to a claim, and what a claim does about them."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'claimaccount'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def _create_instance(self, name):
        inst = self.test_client.create_instance(
            name, INSTANCE_CPUS, INSTANCE_MEMORY_MB,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': INSTANCE_DISK_GB,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        # The created state is reached only once the instance has been
        # placed, and placement is what charges the claim. Waiting for
        # the agent would cost minutes and prove nothing extra here.
        self._await_instance_create(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        self.addDetail(name, content.text_content(json.dumps(
            inst, indent=4, sort_keys=True, default=str)))
        return inst

    def test_placements_draw_down_the_claim_and_report_exceedance(self):
        # A claim sized for exactly one of these instances. Two are then
        # created: the first fits, the second does not.
        claim = self._create_claim(
            INSTANCE_CPUS, INSTANCE_MEMORY_MB, INSTANCE_DISK_GB)
        self.assertEqual(
            (0, 0, 0), self._claim_used(claim),
            'A claim created for an empty namespace should have drawn '
            'nothing down: %s' % json.dumps(claim, sort_keys=True,
                                            default=str))

        within = self._create_instance('claim-within')
        footprint = self._instance_footprint(within)
        # The premise of everything below: the instance really does cost
        # exactly what the claim promises. If instance creation ever
        # starts adding a disk of its own, this is the assertion that
        # says so rather than the exceedance assertions failing
        # mysteriously.
        self.assertEqual(
            (INSTANCE_CPUS, INSTANCE_MEMORY_MB, INSTANCE_DISK_GB), footprint,
            'The test instance does not cost what this test assumes. The '
            'claim is denominated in (cpus, memory_mb, disk_gb) of %s but '
            'the instance reports %s. The instance reads %s'
            % ((INSTANCE_CPUS, INSTANCE_MEMORY_MB, INSTANCE_DISK_GB),
               footprint, json.dumps(within, sort_keys=True, default=str)))

        # The claim is charged for the placement. Because the claim
        # branch and the cluster's unclaimed branch of the admission
        # transaction are mutually exclusive, this is also the closest
        # this suite can get to "and the cluster's unclaimed usage did
        # not move" -- see the module docstring.
        self._await_claim_used(
            claim['uuid'], footprint,
            'one placement in a namespace holding an active claim')

        exceeds = self._create_instance('claim-exceeds')
        self._await_claim_used(
            claim['uuid'], tuple(2 * n for n in footprint),
            'a second placement in a namespace holding an active claim')

        # The create succeeded, which is what advisory means -- but a
        # create succeeding is also what happens with no claim at all,
        # so the assertion that matters is the audit event.
        self.assertEqual(
            'created', exceeds['state'],
            'An instance create which exceeds its namespace\'s claim must '
            'still succeed while claim ceilings are advisory, but instance '
            '%s is in state %s' % (exceeds['uuid'], exceeds['state']))

        matched = self._claim_over_limit_events(exceeds['uuid'], wait=60)
        self.assertNotEqual(
            [], matched,
            'Instance %s drew its namespace past a claim of %s to a usage '
            'of %s, and no "%s" audit event was recorded for it within 60 '
            'seconds. Advisory mode which records nothing is '
            'indistinguishable from no claim at all.'
            % (exceeds['uuid'], self._claim_limits(claim),
               tuple(2 * n for n in footprint), CLAIM_OVER_LIMIT_MESSAGE))

        extra = matched[0].get('extra') or {}
        self.assertEqual(
            self.namespace, extra.get('namespace'),
            'The advisory event names namespace %s rather than %s: %s'
            % (extra.get('namespace'), self.namespace,
               json.dumps(matched[0], sort_keys=True, default=str)))

        dimensions = extra.get('claim_dimensions') or []
        exceeded = sorted(d.get('dimension') for d in dimensions
                          if d.get('exceeded'))
        self.assertEqual(
            ['cpus', 'disk_gb', 'memory_mb'], exceeded,
            'The second instance doubles a claim sized for one, so every '
            'dimension should be reported as exceeded. The event reported '
            '%s. The event reads %s'
            % (exceeded, json.dumps(matched[0], sort_keys=True, default=str)))

        # And the detail describes this instance's request, not some
        # other quantity: a per-dimension report an operator cannot size
        # a claim from is not worth emitting.
        requested = dict(zip(('cpus', 'memory_mb', 'disk_gb'), footprint))
        for detail in dimensions:
            self.assertEqual(
                float(requested[detail['dimension']]),
                float(detail['requested']),
                'The advisory event\'s %s detail does not describe this '
                'instance\'s request of %s: %s'
                % (detail['dimension'], requested[detail['dimension']],
                   json.dumps(detail, sort_keys=True, default=str)))

        # The event fires because the claim was exceeded, not because a
        # claim exists. The first instance fitted exactly inside the
        # claim and must carry no advisory event. Checked only now, once
        # the second instance's event has been seen: the event pipeline
        # is demonstrably flowing, and the first placement happened
        # strictly before the second one did, so an empty list here is
        # absence rather than latency.
        self.assertEqual(
            [], self._claim_over_limit_events(within['uuid']),
            'Instance %s fitted exactly inside the claim, so it should '
            'carry no "%s" audit event. Advisory accounting which fires on '
            'every create in a claimed namespace would report nothing '
            'useful.' % (within['uuid'], CLAIM_OVER_LIMIT_MESSAGE))

    def test_claim_creation_migrates_existing_drawdown(self):
        # The namespace holds an instance *before* it holds a claim.
        # This is the case D3 exists for -- and the case the guard was
        # originally getting wrong, by refusing an operator the capacity
        # their namespace was already using.
        existing = self._create_instance('claim-predates')
        footprint = self._instance_footprint(existing)

        claim = self._create_claim(
            4 * INSTANCE_CPUS, 4 * INSTANCE_MEMORY_MB, 4 * INSTANCE_DISK_GB)
        self.assertEqual(
            footprint, self._claim_used(claim),
            'A claim created for a namespace which already holds instance '
            '%s should have been seeded with that drawdown of %s, but it '
            'reports %s. Without the migration the namespace can place its '
            'whole claim a second time before the reconciler notices. The '
            'claim reads %s'
            % (existing['uuid'], footprint, self._claim_used(claim),
               json.dumps(claim, sort_keys=True, default=str)))

        # A claim cannot be shrunk below what it is already using. One
        # cpu below the drawdown is refused...
        below = footprint[0] - 1
        status, body = self._claim_api_awaiting_accounting(
            'PUT', self._claim_target(claim['uuid']),
            data={'limit_cpus': below})
        self.assertEqual(
            409, status,
            'Shrinking claim %s to %d cpus when it is using %d should be '
            'refused, but the API answered %s'
            % (claim['uuid'], below, footprint[0],
               self._describe(status, body)))

        still = self._get_claim(claim['uuid'])
        self.assertEqual(
            4 * INSTANCE_CPUS, still['limit_cpus'],
            'A refused shrink still changed the claim\'s cpu limit, to %s. '
            'The claim reads %s'
            % (still['limit_cpus'],
               json.dumps(still, sort_keys=True, default=str)))

        # ...and shrinking to exactly the drawdown is allowed.
        status, shrunk = self._claim_api_awaiting_accounting(
            'PUT', self._claim_target(claim['uuid']),
            data={'limit_cpus': footprint[0]})
        self.assertEqual(
            200, status,
            'Shrinking claim %s to exactly the %d cpus it is using should '
            'be allowed, but the API answered %s'
            % (claim['uuid'], footprint[0], self._describe(status, shrunk)))
        self.assertEqual(
            footprint[0], shrunk['limit_cpus'],
            'The claim was shrunk to %d cpus but reports a limit of %s: %s'
            % (footprint[0], shrunk['limit_cpus'],
               json.dumps(shrunk, sort_keys=True, default=str)))
        self.assertEqual(
            footprint, self._claim_used(shrunk),
            'Shrinking the claim changed what it is using, from %s to %s: '
            '%s' % (footprint, self._claim_used(shrunk),
                    json.dumps(shrunk, sort_keys=True, default=str)))
