"""The namespace capacity claim CRUD API.

A claim is a promise of cluster capacity, so these tests care about
three things beyond "does the verb work": that only a cluster
administrator can make the promise, that a refusal reaches the caller as
a status code they can act on rather than a generic failure, and that
the namespace in the URL is load bearing rather than decorative.

The response shape gets its own attention because a claim publishes two
states which are two different facts (D2): ``state`` is the object's
existence and ``coverage_state`` is whether the claim still covers
placements. A view which collapsed them would be the first step towards
the code doing the same.
"""

import json
import logging
import sys
from unittest import mock

from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.namespace_claim import NamespaceClaim
from shakenfist.schema.event import EventReadRow
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class ClaimEndpointTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False
        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('ci', 'key1', 'bacon')
        self.mock_mariadb.create_namespace('other', 'key1', 'sausage')

        self.client = external_api.app.test_client()
        self.admin = self._token('system', 'bar')
        self.owner = self._token('ci', 'bacon')
        self.stranger = self._token('other', 'sausage')

    def _token(self, namespace, key):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': namespace, 'key': key}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _create(self, token=None, namespace='ci', **overrides):
        body = {
            'limit_cpus': 40,
            'limit_memory_mb': 81920,
            'limit_disk_gb': 2000,
            'expires_in_seconds': 86400
        }
        body.update(overrides)
        return self.client.post(
            '/auth/namespaces/%s/claims' % namespace,
            headers={'Authorization': token or self.admin},
            data=json.dumps(body))

    def _created(self, **kwargs):
        """Create a claim and return its uuid, asserting it worked."""
        resp = self._create(**kwargs)
        self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))
        return resp.get_json()['uuid']

    def _row(self, claim_uuid):
        """The stored claim row, reached around the object deliberately.

        A test which moves a used_* counter is standing in for the
        admission path, which does not go through the object either.
        """
        return self.mock_mariadb.namespace_claims[str(claim_uuid)]


class ClaimCreateTestCase(ClaimEndpointTestCase):
    def test_an_admin_can_claim_capacity(self):
        resp = self._create()
        self.assertEqual(200, resp.status_code)

        body = resp.get_json()
        self.assertEqual('ci', body['namespace'])
        self.assertEqual(40, body['limit_cpus'])
        self.assertEqual(81920, body['limit_memory_mb'])
        self.assertEqual(2000, body['limit_disk_gb'])

        # Two states, two facts. The object exists, and the claim is
        # covering placements; neither field says the other's business.
        self.assertEqual('created', body['state'])
        self.assertEqual('active', body['coverage_state'])

        # A fresh claim reports its drawdown, which the mock seeds at
        # zero because it has no cluster singleton to migrate from.
        self.assertEqual(0, body['used_cpus'])
        self.assertEqual(0, body['used_memory_mb'])
        self.assertEqual(0, body['used_disk_gb'])

        self.assertIsNotNone(body['expires_at'])

    def test_expiry_is_a_duration_measured_from_the_server(self):
        # The body carries seconds from now, not an absolute time, and
        # the expiry that lands is computed from the cluster's clock.
        # Asserted through the stored row rather than the response so a
        # rendering change cannot hide it.
        claim_uuid = self._created(expires_in_seconds=3600)
        row = self._row(claim_uuid)
        self.assertAlmostEqual(3600, row['expires_at'] - row['updated_at'],
                               delta=5)

    def test_a_missing_field_is_refused(self):
        for field in ['limit_cpus', 'limit_memory_mb', 'limit_disk_gb',
                      'expires_in_seconds']:
            body = {
                'limit_cpus': 4, 'limit_memory_mb': 1024,
                'limit_disk_gb': 20, 'expires_in_seconds': 60
            }
            del body[field]
            resp = self.client.post(
                '/auth/namespaces/ci/claims',
                headers={'Authorization': self.admin},
                data=json.dumps(body))
            self.assertEqual(400, resp.status_code, field)
            self.assertIn(field, resp.get_json()['error'])

    def test_a_negative_limit_is_refused(self):
        resp = self._create(limit_cpus=-1)
        self.assertEqual(400, resp.status_code)
        self.assertIn('cannot be negative', resp.get_json()['error'])

    def test_a_boolean_limit_is_refused(self):
        # bool is an int in Python, so "limit_cpus": true would
        # otherwise quietly claim one cpu.
        resp = self._create(limit_cpus=True)
        self.assertEqual(400, resp.status_code)
        self.assertIn('not an integer', resp.get_json()['error'])

    def test_a_non_positive_expiry_is_refused(self):
        # A claim that is expired the moment it exists holds capacity
        # for nobody and cannot be grown, so it is a trap rather than a
        # shorthand.
        for value in [0, -60]:
            resp = self._create(expires_in_seconds=value)
            self.assertEqual(400, resp.status_code, value)
            self.assertIn('must be positive', resp.get_json()['error'])

    def test_an_unknown_namespace_is_not_found(self):
        resp = self._create(namespace='nosuchnamespace')
        self.assertEqual(404, resp.status_code)


class ClaimReadTestCase(ClaimEndpointTestCase):
    def test_claims_are_listed_and_fetched(self):
        claim_uuid = self._created()

        listed = self.client.get(
            '/auth/namespaces/ci/claims',
            headers={'Authorization': self.admin})
        self.assertEqual(200, listed.status_code)
        self.assertEqual([claim_uuid], [c['uuid'] for c in listed.get_json()])
        self.assertEqual(['active'],
                         [c['coverage_state'] for c in listed.get_json()])
        self.assertEqual(['created'], [c['state'] for c in listed.get_json()])

        fetched = self.client.get(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin})
        self.assertEqual(200, fetched.status_code)
        self.assertEqual(claim_uuid, fetched.get_json()['uuid'])
        self.assertEqual('created', fetched.get_json()['state'])
        self.assertEqual('active', fetched.get_json()['coverage_state'])

    def test_an_expired_claim_is_still_listed_and_still_created(self):
        # The two states pulling apart is the case the API exists to
        # describe: an expired claim is still a created object, still
        # has a row, and is the only thing that explains why a
        # namespace's placements stopped being charged to it.
        claim_uuid = self._created()
        self._row(claim_uuid)['state'] = 'expired'

        fetched = self.client.get(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin})
        self.assertEqual(200, fetched.status_code)
        self.assertEqual('created', fetched.get_json()['state'])
        self.assertEqual('expired', fetched.get_json()['coverage_state'])

        listed = self.client.get(
            '/auth/namespaces/ci/claims',
            headers={'Authorization': self.admin})
        self.assertEqual([claim_uuid], [c['uuid'] for c in listed.get_json()])

    def test_an_empty_namespace_lists_nothing(self):
        self._created()
        listed = self.client.get(
            '/auth/namespaces/other/claims',
            headers={'Authorization': self.admin})
        self.assertEqual(200, listed.status_code)
        self.assertEqual([], listed.get_json())

    def test_a_claim_reference_which_is_not_a_uuid_is_not_found(self):
        # A claim has no name, so there is nothing to fall back to.
        resp = self.client.get(
            '/auth/namespaces/ci/claims/not-a-uuid',
            headers={'Authorization': self.admin})
        self.assertEqual(404, resp.status_code)


class ClaimNamespaceScopingTestCase(ClaimEndpointTestCase):
    """The namespace segment of the URL has to mean something.

    A claim is addressed by uuid, and a uuid is enough to find the row
    on its own. If the namespace in the path were not checked against
    the claim's own then any namespace's URL would reach any claim,
    which would make the segment decorative -- and decorative is a
    cross-tenant read the day D15's delegated claim creation lands.
    """

    def test_a_claim_cannot_be_read_through_another_namespace(self):
        claim_uuid = self._created(namespace='ci')

        resp = self.client.get(
            '/auth/namespaces/other/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin})
        self.assertEqual(404, resp.status_code)

    def test_a_claim_cannot_be_updated_through_another_namespace(self):
        claim_uuid = self._created(namespace='ci')

        resp = self.client.put(
            '/auth/namespaces/other/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin},
            data=json.dumps({'limit_cpus': 8}))
        self.assertEqual(404, resp.status_code)
        # And the claim was not touched.
        self.assertEqual(40, self._row(claim_uuid)['limit_cpus'])

    def test_a_claim_cannot_be_deleted_through_another_namespace(self):
        claim_uuid = self._created(namespace='ci')

        resp = self.client.delete(
            '/auth/namespaces/other/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin})
        self.assertEqual(404, resp.status_code)
        self.assertIn(claim_uuid, self.mock_mariadb.namespace_claims)


class ClaimUpdateTestCase(ClaimEndpointTestCase):
    def _put(self, claim_uuid, token=None, **body):
        return self.client.put(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': token or self.admin},
            data=json.dumps(body))

    def test_a_claim_can_be_grown(self):
        claim_uuid = self._created()
        resp = self._put(claim_uuid, limit_cpus=80)

        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(80, body['limit_cpus'])
        # Unmentioned dimensions are untouched: the field mask is what
        # stops an update shrinking everything the caller did not name.
        self.assertEqual(81920, body['limit_memory_mb'])
        self.assertEqual(2000, body['limit_disk_gb'])
        self.assertEqual('created', body['state'])
        self.assertEqual('active', body['coverage_state'])

    def test_a_claim_can_be_shrunk_to_its_usage(self):
        claim_uuid = self._created()
        self._row(claim_uuid)['used_cpus'] = 12

        resp = self._put(claim_uuid, limit_cpus=12)
        self.assertEqual(200, resp.status_code)
        self.assertEqual(12, resp.get_json()['limit_cpus'])

    def test_a_claim_can_be_re_dated(self):
        claim_uuid = self._created(expires_in_seconds=60)
        resp = self._put(claim_uuid, expires_in_seconds=7200)

        self.assertEqual(200, resp.status_code)
        row = self._row(claim_uuid)
        self.assertAlmostEqual(7200, row['expires_at'] - row['updated_at'],
                               delta=5)

    def test_an_update_which_asks_for_nothing_is_refused(self):
        claim_uuid = self._created()
        resp = self._put(claim_uuid)
        self.assertEqual(400, resp.status_code)
        self.assertIn('no claim fields to update', resp.get_json()['error'])

    def test_a_malformed_update_is_refused(self):
        claim_uuid = self._created()
        self.assertEqual(400, self._put(claim_uuid, limit_cpus=-1).status_code)
        self.assertEqual(
            400, self._put(claim_uuid, expires_in_seconds=0).status_code)


class ClaimDeleteTestCase(ClaimEndpointTestCase):
    def test_deleting_a_claim_returns_what_it_was(self):
        claim_uuid = self._created()

        resp = self.client.delete(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code)

        # The view is read before the row goes, because there is no
        # soft delete of a claim and afterwards there is nothing left to
        # describe.
        body = resp.get_json()
        self.assertEqual(claim_uuid, body['uuid'])
        self.assertEqual(40, body['limit_cpus'])
        self.assertEqual('active', body['coverage_state'])

        self.assertNotIn(claim_uuid, self.mock_mariadb.namespace_claims)

    def test_the_delete_request_is_audited_against_the_namespace(self):
        # Not against the claim: hard_delete() removes the claim's own
        # events along with the claim, so an event recorded there would
        # be destroyed by the call it exists to explain.
        claim_uuid = self._created()

        with mock.patch.object(Namespace, 'add_event') as add_event:
            self.assertEqual(200, self.client.delete(
                '/auth/namespaces/ci/claims/%s' % claim_uuid,
                headers={'Authorization': self.admin}).status_code)

        requests = [
            call for call in add_event.call_args_list
            if call.args[1] == 'delete namespace claim request from REST API']
        self.assertEqual(1, len(requests))
        self.assertEqual(claim_uuid, requests[0].kwargs['extra']['claim'])

    def test_deleting_twice_is_a_not_found(self):
        claim_uuid = self._created()
        self.assertEqual(200, self.client.delete(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin}).status_code)
        self.assertEqual(404, self.client.delete(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin}).status_code)


class ClaimAdminGatingTestCase(ClaimEndpointTestCase):
    """Only a cluster administrator may promise cluster capacity.

    Capacity promised to one namespace is capacity refused to every
    other one, so unlike the sibling key and rule endpoints -- which a
    namespace owner may drive for their own namespace -- these are
    admin only on every verb. The caller under test here is the *owner*
    of the namespace in the URL, so nothing but the admin gate can be
    refusing them.
    """

    def setUp(self):
        super().setUp()
        self.claim_uuid = self._created()

    def _assert_refused(self, resp):
        self.assertEqual(401, resp.status_code, resp.get_data(as_text=True))

    def test_the_namespace_owner_cannot_list_claims(self):
        self._assert_refused(self.client.get(
            '/auth/namespaces/ci/claims',
            headers={'Authorization': self.owner}))

    def test_the_namespace_owner_cannot_create_a_claim(self):
        self._assert_refused(self._create(token=self.owner))
        # And nothing was created by the attempt.
        self.assertEqual(1, len(self.mock_mariadb.namespace_claims))

    def test_the_namespace_owner_cannot_fetch_a_claim(self):
        self._assert_refused(self.client.get(
            '/auth/namespaces/ci/claims/%s' % self.claim_uuid,
            headers={'Authorization': self.owner}))

    def test_the_namespace_owner_cannot_update_a_claim(self):
        self._assert_refused(self.client.put(
            '/auth/namespaces/ci/claims/%s' % self.claim_uuid,
            headers={'Authorization': self.owner},
            data=json.dumps({'limit_cpus': 4000})))
        self.assertEqual(40, self._row(self.claim_uuid)['limit_cpus'])

    def test_the_namespace_owner_cannot_delete_a_claim(self):
        self._assert_refused(self.client.delete(
            '/auth/namespaces/ci/claims/%s' % self.claim_uuid,
            headers={'Authorization': self.owner}))
        self.assertIn(self.claim_uuid, self.mock_mariadb.namespace_claims)

    def test_a_stranger_is_refused_on_every_verb(self):
        for resp in [
            self.client.get('/auth/namespaces/ci/claims',
                            headers={'Authorization': self.stranger}),
            self._create(token=self.stranger),
            self.client.get(
                '/auth/namespaces/ci/claims/%s' % self.claim_uuid,
                headers={'Authorization': self.stranger}),
            self.client.put(
                '/auth/namespaces/ci/claims/%s' % self.claim_uuid,
                headers={'Authorization': self.stranger},
                data=json.dumps({'limit_cpus': 4000})),
            self.client.delete(
                '/auth/namespaces/ci/claims/%s' % self.claim_uuid,
                headers={'Authorization': self.stranger})
        ]:
            self._assert_refused(resp)

    def test_an_unauthenticated_caller_is_refused(self):
        self.assertEqual(401, self.client.get(
            '/auth/namespaces/ci/claims').status_code)


class ClaimRefusalStatusTestCase(ClaimEndpointTestCase):
    """Each refusal reason reaches the caller as its own status code.

    A refusal is not a failure -- the guarded transaction ran and
    decided no -- so the response has to say which kind of no it was,
    and in particular has to separate the transient ones a client should
    retry from the durable ones it must resolve first.
    """

    def test_capacity_is_insufficient_storage(self):
        # 507, which is what this API already answers for every other
        # capacity exhaustion.
        self.mock_mariadb.refuse_namespace_claims('capacity')
        resp = self._create()
        self.assertEqual(507, resp.status_code)
        self.assertIn('does not have the capacity', resp.get_json()['error'])

    def test_a_missing_cluster_singleton_is_service_unavailable(self):
        # 503 rather than 507: the cluster is not full, the reconciler
        # simply has not built the capacity singleton yet, and the
        # correct client behaviour is to retry.
        self.mock_mariadb.refuse_namespace_claims('no_cluster_capacity')
        resp = self._create()
        self.assertEqual(503, resp.status_code)
        self.assertIn('please retry', resp.get_json()['error'])

    def test_contention_is_service_unavailable(self):
        # 503 and not 409, which is the distinction that matters most
        # here: 'conflict' means the row kept moving under a concurrent
        # writer until the optimistic retry budget ran out, so a caller
        # which read it as a durable conflict would abandon a claim it
        # could have had a second later.
        self.mock_mariadb.refuse_namespace_claims('conflict')
        resp = self._create()
        self.assertEqual(503, resp.status_code)
        self.assertIn('please retry', resp.get_json()['error'])

    def test_a_second_claim_for_a_namespace_is_a_conflict(self):
        # 409, and deliberately a different code from 'conflict': this
        # is a durable state the caller has to resolve by deleting the
        # claim it already holds.
        self.assertEqual(200, self._create().status_code)
        resp = self._create()
        self.assertEqual(409, resp.status_code)
        self.assertIn('already holds an active claim',
                      resp.get_json()['error'])

    def test_shrinking_below_usage_is_a_conflict(self):
        claim_uuid = self._created()
        self._row(claim_uuid)['used_cpus'] = 12

        resp = self.client.put(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin},
            data=json.dumps({'limit_cpus': 4}))
        self.assertEqual(409, resp.status_code)
        self.assertIn('cannot be shrunk below', resp.get_json()['error'])

    def test_updating_an_inactive_claim_is_a_conflict(self):
        claim_uuid = self._created()
        self._row(claim_uuid)['state'] = 'expired'

        resp = self.client.put(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin},
            data=json.dumps({'limit_cpus': 80}))
        self.assertEqual(409, resp.status_code)
        self.assertIn('no longer active', resp.get_json()['error'])

    def test_a_claim_deleted_mid_request_is_not_found(self):
        # Only reachable as a race: the claim resolved through
        # arg_is_claim_ref and was deleted before the update reached
        # it. Forced here, because a test cannot delete a row between
        # two decorators.
        claim_uuid = self._created()
        with mock.patch('shakenfist.mariadb.update_namespace_claim',
                        return_value={
                            'success': True, 'error': '', 'updated': False,
                            'refused_reason': 'not_found', 'dimensions': [],
                            'claim': None}):
            resp = self.client.put(
                '/auth/namespaces/ci/claims/%s' % claim_uuid,
                headers={'Authorization': self.admin},
                data=json.dumps({'limit_cpus': 80}))
        self.assertEqual(404, resp.status_code)

    def test_an_unrecognised_reason_is_a_server_error(self):
        # A refusal reason the database layer grew and this module has
        # not been taught is a server side gap, and must not be
        # reported as the caller's fault.
        self.mock_mariadb.refuse_namespace_claims('something_new')
        resp = self._create()
        self.assertEqual(500, resp.status_code)
        self.assertIn('unrecognised reason', resp.get_json()['error'])

    def test_a_capacity_refusal_names_the_dimension_that_did_not_fit(self):
        # The whole point of a guard which reports per-dimension detail
        # is that the caller learns which dimension refused it, so the
        # detail has to reach the response body and not only the logs.
        # The mock does not model the cluster singleton's arithmetic,
        # so the reply is forced.
        with mock.patch('shakenfist.mariadb.create_namespace_claim',
                        return_value={
                            'success': True, 'error': '', 'created': False,
                            'refused_reason': 'capacity',
                            'dimensions': [
                                {'dimension': 'cpus', 'limit': 100.0,
                                 'used': 80.0, 'requested': 40.0,
                                 'exceeded': True},
                                {'dimension': 'memory_mb', 'limit': 500000.0,
                                 'used': 1000.0, 'requested': 81920.0,
                                 'exceeded': False}],
                            'claim': None}):
            resp = self._create()

        self.assertEqual(507, resp.status_code)
        error = resp.get_json()['error']
        self.assertIn('cpus (limit 100, used 80, requested 40)', error)
        # Only the dimension which actually failed; naming the ones
        # which fitted would make the message useless.
        self.assertNotIn('memory_mb', error)


class ClaimObjectStateTestCase(ClaimEndpointTestCase):
    def test_a_claim_whose_object_was_deleted_is_not_found(self):
        # The zombie repair path writes a deleted object state directly
        # while the claim row survives. Such a claim is gone as far as
        # the API is concerned; serving it would resurrect an object the
        # reaper is on its way to collect.
        claim_uuid = self._created()
        c = NamespaceClaim.from_db(claim_uuid)
        c.state = NamespaceClaim.STATE_DELETED

        resp = self.client.get(
            '/auth/namespaces/ci/claims/%s' % claim_uuid,
            headers={'Authorization': self.admin})
        self.assertEqual(404, resp.status_code)


class CapacityEventsTestCase(ClaimEndpointTestCase):
    """The namespace and claim events endpoints.

    A claim's accounting is only completely legible across two reads.
    The claim's own events explain what it did while it existed, and
    the namespace's outlive it -- which matters because growing a claim
    by deleting and recreating it is a thing operators do, and
    hard_delete() takes the claim's events with it.

    The reads themselves are mocked at the database layer: MockMariaDB
    does not store events, and what is under test here is routing,
    gating and which object each endpoint asks about, not the query.
    """

    def setUp(self):
        super().setUp()
        self.claim_uuid = self._created()

        self.events = mock.patch(
            'shakenfist.mariadb.get_object_events',
            return_value=[EventReadRow(
                event_uuid='2b1e1b1a-1c1d-4e1f-8a1b-1c1d1e1f2a3b',
                event_type='audit', timestamp=1755300000.0, fqdn='sf-1',
                message='namespace claim deleted, capacity returned',
                extra={'claim': self.claim_uuid})])
        self.mock_events = self.events.start()
        self.addCleanup(self.events.stop)

    def test_a_namespaces_events_are_readable(self):
        # The end to end case this endpoint exists for: the claim
        # deletion event namespace_claim.py records against the
        # namespace had no reader until now.
        resp = self.client.get(
            '/auth/namespaces/ci/events',
            headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))

        body = resp.get_json()
        self.assertEqual(1, len(body))
        self.assertEqual('namespace claim deleted, capacity returned',
                         body[0]['message'])

        # A namespace is keyed by its name, not a uuid.
        self.mock_events.assert_called_once_with(
            'namespace', 'ci', limit=100, event_type=None)

    def test_a_claims_events_are_readable(self):
        resp = self.client.get(
            '/auth/namespaces/ci/claims/%s/events' % self.claim_uuid,
            headers={'Authorization': self.admin})
        self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))
        self.assertEqual(1, len(resp.get_json()))

        object_type, object_uuid = self.mock_events.call_args.args
        self.assertEqual('namespace_claim', object_type)
        self.assertEqual(self.claim_uuid, str(object_uuid))

    def test_the_query_parameters_reach_the_read(self):
        # events-by-type behaviour, the same as the five endpoints that
        # predate these two.
        for path in ['/auth/namespaces/ci/events',
                     '/auth/namespaces/ci/claims/%s/events' % self.claim_uuid]:
            self.mock_events.reset_mock()
            resp = self.client.get(
                path, headers={'Authorization': self.admin},
                data=json.dumps({'event_type': 'audit', 'limit': 5}))
            self.assertEqual(200, resp.status_code, path)
            self.assertEqual(
                {'limit': 5, 'event_type': 'audit'},
                self.mock_events.call_args.kwargs)

    def test_an_unknown_namespace_or_claim_is_not_found(self):
        self.assertEqual(404, self.client.get(
            '/auth/namespaces/nosuch/events',
            headers={'Authorization': self.admin}).status_code)
        self.assertEqual(404, self.client.get(
            '/auth/namespaces/ci/claims/%s/events'
            % '1e1a4c4a-0f0e-4c4b-9a9b-0d0c0b0a0908',
            headers={'Authorization': self.admin}).status_code)

    def test_a_claim_is_not_readable_through_another_namespace(self):
        # The namespace segment is load bearing here exactly as it is
        # on the CRUD verbs; a claim addressed through a namespace it
        # does not belong to is a 404, not somebody else's events.
        self.assertEqual(404, self.client.get(
            '/auth/namespaces/other/claims/%s/events' % self.claim_uuid,
            headers={'Authorization': self.admin}).status_code)

    def test_events_are_admin_only(self):
        # Both endpoints are gated like the claim verbs they sit
        # beside, including against the owner of the namespace in the
        # URL: a namespace's event trail names the instances, nodes and
        # other namespaces its capacity accounting involved.
        for token in [self.owner, self.stranger]:
            for path in ['/auth/namespaces/ci/events',
                         '/auth/namespaces/ci/claims/%s/events'
                         % self.claim_uuid]:
                resp = self.client.get(
                    path, headers={'Authorization': token})
                self.assertEqual(401, resp.status_code, path)
        self.mock_events.assert_not_called()

    def test_an_unauthenticated_caller_is_refused(self):
        self.assertEqual(401, self.client.get(
            '/auth/namespaces/ci/events').status_code)
        self.assertEqual(401, self.client.get(
            '/auth/namespaces/ci/claims/%s/events'
            % self.claim_uuid).status_code)
