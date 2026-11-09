# Copyright 2026 Michael Still and contributors

"""The placement query.

The question this endpoint answers is "would these fit", and the
things worth asserting about it are not the ones a CRUD endpoint's
tests assert. Four properties carry the contract:

* A set which does not fit is a **200**. Every other capacity refusal
  in this API is a 507, and the asymmetry is deliberate: the question
  was answered, and a caller's error handling has to be able to tell
  "the cluster is full" from "the query failed". So the tests below
  assert the 200 *and* assert that a 507 is not what arrives.
* The answer names a **stage** the scheduler already reports, and
  nothing here mints one. A prediction and a real refusal are compared
  label for label by a consumer which is already deployed, so a
  refusal on hard affinity has to be distinguishable from a refusal on
  capacity by the stage alone.
* **Nothing is written and nothing is published.** A speculative
  evaluation which left audit events behind would put records of
  placements that did not happen on somebody's instance, so what
  reached the event spool is counted rather than reasoned about.
* **Per-node detail is administrative.** A tenant learning which node
  UUIDs exist and how much room each has is a cluster topology
  disclosure no other endpoint gives them, so the non-administrative
  answer is checked by looking for the fixture's node UUIDs in the
  serialised body rather than by reading the keys it does carry.
"""

import json
from unittest import mock

from shakenfist import exceptions
from shakenfist import scheduler
from shakenfist.config import SFConfig
from shakenfist.constants import GiB
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import placement as placement_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


# The scheduler's own test fixture configuration: four schedulable
# threads and an overcommit ratio of 16 cap each node at 64 vCPUs, so
# the tests below can move memory and disk without a vCPU ceiling
# refusing first.
fake_config = SFConfig(
    NODE_NAME='node01',
    SCHEDULER_CACHE_TIMEOUT=30,
    CPU_OVERCOMMIT_RATIO=16.0,
    RAM_OVERCOMMIT_RATIO=1.5,
    NODE_RAM_RESERVATION_GB=5.0,
    NODE_CPU_RESERVATION_THREADS=2,
    NETWORK_NODE_IP='10.0.0.1',
)


class PlacementTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_config = mock.patch(
            'shakenfist.scheduler.config', fake_config)
        self.mock_config.start()
        self.addCleanup(self.mock_config.stop)

        # The handler caches a Scheduler in a module global, and a
        # Scheduler caches the cluster's metrics for
        # SCHEDULER_CACHE_TIMEOUT. One left behind would answer the
        # next test in this worker from the previous test's cluster.
        self.mock_scheduler = mock.patch.object(
            placement_api, 'SCHEDULER', None)
        self.mock_scheduler.start()
        self.addCleanup(self.mock_scheduler.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('ci', 'key1', 'bacon')

        self.client = external_api.app.test_client()
        self.admin = self._token('system', 'bar')
        self.tenant = self._token('ci', 'bacon')

    def _token(self, namespace, key):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': namespace, 'key': key}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _hypervisors(self):
        return [self.mock_mariadb.node_uuids[name]
                for name in self.mock_mariadb.node_names
                if name != 'node1_net']

    def _baseline(self, **overrides):
        """Every node measuring plenty of room, before a test takes some."""
        metrics = {
            'cpu_max_per_instance': 16,
            'cpu_max': 4,
            'cpu_schedulable': 4,
            'cpu_total_instance_vcpus': 0,
            'cpu_load_1': 0,
            'memory_available': 32768,
            'memory_reserved_mb': 0,
            'memory_max': 65536,
            'memory_total_instance_actual': 0,
            'disk_free_instances': 1000 * GiB,
            'disk_reservation_gb': 0,
        }
        metrics.update(overrides)
        self.mock_mariadb.set_node_metrics_same(metrics)

    def _shape(self, cpus=1, memory=1024, **extra):
        shape = {'cpus': cpus, 'memory': memory}
        shape.update(extra)
        return shape

    def _query(self, shapes, token=None):
        return self.client.post(
            '/placement',
            headers={'Authorization': token or self.admin},
            content_type='application/json',
            data=json.dumps({'shapes': shapes}))

    def _answer(self, shapes, token=None):
        """The parsed answer to a query which was asked successfully.

        A query is only ever "unsuccessful" when the request was
        malformed, so every test which is about the verdict asserts the
        200 here rather than restating it, and asserts that the 507
        every other capacity refusal in this API uses is not what
        arrived.
        """
        resp = self._query(shapes, token=token)
        body = resp.get_json()
        self.assertNotEqual(
            507, resp.status_code,
            'a placement query answered with the capacity refusal a create '
            'gets, which makes "the cluster is full" indistinguishable '
            'from "the query failed": %r' % (body,))
        self.assertEqual(200, resp.status_code, body)
        return body


class PlacementVerdictTestCase(PlacementTestCase):
    def test_a_set_which_fits_says_where_each_shape_would_go(self):
        self._baseline()

        body = self._answer([self._shape(), self._shape()])

        self.assertTrue(body['fits'])
        self.assertEqual(2, len(body['shapes']))
        for index, entry in enumerate(body['shapes']):
            self.assertIn(entry['node'], self._hypervisors(), 'shape %d'
                          % index)
            # The head of the candidate list is the node the shape was
            # charged to, and the rest are the room behind it.
            self.assertEqual(entry['node'], entry['candidates'][0])
            self.assertEqual(
                sorted(self._hypervisors()), sorted(entry['candidates']))

    def test_a_set_which_does_not_fit_is_a_200_naming_the_stage(self):
        # Three hypervisors, each measuring room for exactly one of
        # these shapes, and a fourth shape with nowhere to go.
        self._baseline(memory_available=8192)

        body = self._answer([self._shape(memory=8192)] * 4)

        self.assertFalse(body['fits'])
        self.assertEqual('sufficient_idle_memory', body['stage'])
        self.assertEqual(3, body['shape'])
        # All or nothing: a refused set is given no assignment at all,
        # not three quarters of one.
        self.assertNotIn('shapes', body)

    def test_the_refused_shape_is_named_in_the_order_it_was_asked(self):
        # The set is walked largest shape first, so the shape which
        # runs out of candidates here is the second one the caller sent
        # -- the only one too large for any node -- and it is neither
        # the first shape nor the last. An index which merely counted
        # the shapes that had been placed, or named the last shape in
        # the request, would be wrong in both directions.
        self._baseline(memory_available=8192)

        body = self._answer([
            self._shape(memory=1024),
            self._shape(memory=65536),
            self._shape(memory=1024)])

        self.assertFalse(body['fits'])
        self.assertEqual('sufficient_idle_memory', body['stage'])
        self.assertEqual(1, body['shape'])

    def test_a_hard_affinity_refusal_names_its_own_stage(self):
        # No instance anywhere carries the tag, so no node satisfies
        # the constraint. That is a conflict with the state of the
        # cluster rather than a shortage, and the stage has to say so:
        # a consumer scoring predictions against real refusals compares
        # the labels, so an affinity refusal reported as a capacity one
        # would be scored as a disagreement about capacity.
        self._baseline()

        affinity = self._answer([
            self._shape(affinity={'require_with_tag': ['database']})])
        capacity = self._answer([self._shape(memory=1048576)])

        self.assertFalse(affinity['fits'])
        self.assertEqual('affinity_constraints', affinity['stage'])
        self.assertFalse(capacity['fits'])
        self.assertEqual('sufficient_idle_memory', capacity['stage'])
        self.assertNotEqual(capacity['stage'], affinity['stage'])

    def test_a_count_is_charged_rather_than_merely_accepted(self):
        # Two nodes with ledger room for one of these shapes each, and
        # a third whose ledger has room for none. The measurements are
        # roomy, so only the charge the count produces can tell a count
        # of two from a count of three on this cluster.
        self._baseline()
        hypervisors = self._hypervisors()
        for node in hypervisors[:2]:
            self.mock_mariadb.set_node_capacity(
                node, limit_cpus=64, limit_memory_mb=8192)
        self.mock_mariadb.set_node_capacity(
            hypervisors[2], limit_cpus=64, limit_memory_mb=0)

        two = self._answer([self._shape(memory=8192, count=2)])
        self.assertTrue(two['fits'])
        self.assertEqual(2, len(two['shapes']))

        three = self._answer([self._shape(memory=8192, count=3)])
        self.assertFalse(three['fits'])
        self.assertEqual('sufficient_idle_memory', three['stage'])

        # The refusal index counts the expanded order, which for a
        # single shape asked for three times means the third copy is
        # shape 2 -- not shape 0, which is where an index counting the
        # caller's array entries would point, and which would tell a
        # caller the first copy was the problem when two of them fitted.
        # This is the one place the published "counted after count has
        # been expanded" contract is observable.
        self.assertEqual(2, three['shape'])

    def test_a_count_expands_into_separate_shapes(self):
        # The expansion is visible in the answer as well as in the
        # arithmetic: a count of three is three entries, each placed in
        # its own right.
        self._baseline()

        body = self._answer([self._shape(count=3)])

        self.assertTrue(body['fits'])
        self.assertEqual(3, len(body['shapes']))

    def test_the_shapes_array_is_in_the_order_the_caller_asked(self):
        # The set is walked largest shape first, so a successful answer
        # has to be put back into the caller's order before it is
        # rendered: an entry a caller cannot line up with the shape it
        # sent tells them nothing, and silently reversed would be worse
        # than missing.
        #
        # Each node's ledger has room for exactly one of these shapes
        # and for no larger one, which is what makes the node each
        # shape lands on predictable rather than a draw from a load
        # bucket: the largest shape fits only the roomiest node, which
        # its charge then fills, and so on down. The shapes are listed
        # smallest first, so the walk order is the exact reverse of the
        # request order and a render in walk order is unmistakable.
        self._baseline()
        hypervisors = self._hypervisors()
        for node, limit in zip(hypervisors, (2048, 4096, 8192)):
            self.mock_mariadb.set_node_capacity(
                node, limit_cpus=64, limit_memory_mb=limit)

        body = self._answer([self._shape(memory=1024),
                             self._shape(memory=4096),
                             self._shape(memory=8192)])

        self.assertTrue(body['fits'])
        self.assertEqual(
            hypervisors, [entry['node'] for entry in body['shapes']],
            'the shapes array is not in the order the caller asked about '
            'them: %r' % (body['shapes'],))
        # Each shape had exactly one node left to it, so the candidate
        # list is that node alone. Asserted as well as the head, because
        # it is what says the per-entry lists were not shuffled between
        # entries either.
        self.assertEqual(
            [[node] for node in hypervisors],
            [entry['candidates'] for entry in body['shapes']])


class PlacementDiskTestCase(PlacementTestCase):
    """The two ways to say how much disk a shape asks for.

    Each node measures exactly 100 GB free, so 100 fits and 101 does
    not. Both figures are asserted for both forms: a pair which only
    ever fitted would pass just as well against a handler which read
    no disk at all.
    """

    def setUp(self):
        super().setUp()
        self._baseline(disk_free_instances=100 * GiB)

    def test_a_summed_disk_and_a_disk_spec_agree(self):
        summed = self._answer([self._shape(disk=100)])
        spec = self._answer([
            self._shape(disk_spec=[{'size': 60}, {'size': 40}])])

        self.assertTrue(summed['fits'])
        self.assertTrue(spec['fits'])

    def test_a_summed_disk_and_a_disk_spec_agree_when_refused(self):
        summed = self._answer([self._shape(disk=101)])
        spec = self._answer([
            self._shape(disk_spec=[{'size': 60}, {'size': 41}])])

        self.assertFalse(summed['fits'])
        self.assertEqual('sufficient_free_disk', summed['stage'])
        self.assertFalse(spec['fits'])
        self.assertEqual('sufficient_free_disk', spec['stage'])

    def test_a_sizeless_disk_contributes_nothing(self):
        # The sum is mariadb.disk_spec_virtual_gb()'s, which reads a
        # disk with no size as the size of its base image and so
        # contributes zero -- the same reading the create path's own
        # capacity claim takes of the same list.
        body = self._answer([
            self._shape(disk_spec=[{'size': 100}, {'base': 'cirros'}])])

        self.assertTrue(body['fits'])

    def test_a_null_size_contributes_nothing(self):
        """A present-but-null size, at the shipped validation mode.

        Sending null is documented and supported: a size is optional,
        the schema accepts a null at every depth, and the key's own
        description says to send one for a disk the size of its base
        image. So this body reaches the handler with validation
        enforced, rather than only on the operator's rollback as the
        fractional and numeric-string rows below do.

        The helper skips it, because a JSON null casts to SQL NULL and
        SUM() ignores it -- the same answer it gives for a disk with no
        size key at all. That is why the two are tested apart: they are
        one fact to the ledger and two to anything which reads the key
        with a default, and a sum written here instead would evaluate
        int(None) and turn a documented request into a server error.
        """
        fits = self._answer(
            [self._shape(disk_spec=[{'size': 100}, {'size': None}])])
        self.assertTrue(
            fits['fits'],
            'a null size was not read as no disk at all: %r' % (fits,))

        # The anti-vacuity half: the null contributed zero, rather than
        # the whole spec having been ignored.
        refused = self._answer(
            [self._shape(disk_spec=[{'size': 101}, {'size': None}])])
        self.assertFalse(refused['fits'])
        self.assertEqual('sufficient_free_disk', refused['stage'])

    def test_a_fractional_size_rounds_the_way_the_ledger_rounds(self):
        """The sum is the ledger's sum, not an arithmetic of its own.

        mariadb.disk_spec_virtual_gb() follows MariaDB's
        JSON-number-to-BIGINT cast and rounds half away from zero, so
        10.5 is eleven gigabytes and not ten. That is the whole value
        of calling it rather than summing the sizes here.

        What the delegation buys is agreement with the *ledger claim* a
        create makes, and the agreement is not total. A create's own
        filter input is summed separately by
        PlacementShape.from_instance(), which truncates with int(): on
        this very list it charges the filters 10 where the query
        charges them 11. The two agree on every integral size, which is
        every size the shipped validation mode accepts -- the schema
        types a disk size as an integer and refuses a fractional one
        before any handler sees it -- and diverge by a gigabyte per
        fractional disk only under relaxed validation, which is the
        mode this test runs at in order to reach the sum at all.

        So this is not a test that the query predicts the create
        exactly for fractional sizes; it does not. It is a test that
        the sum here is the shared implementation's and not a fourth
        reading of how big a disk list is.
        """
        self.set_validation_mode('warn')
        self._baseline(disk_free_instances=10 * GiB)

        # Eleven gigabytes against ten free. Truncating to ten would
        # answer that it fits.
        rounded_up = self._answer([self._shape(disk_spec=[{'size': 10.5}])])
        self.assertFalse(
            rounded_up['fits'],
            'a disk of 10.5 GB was read as fitting in 10 GB, so the sum '
            'truncated rather than rounding as the ledger does')
        self.assertEqual('sufficient_free_disk', rounded_up['stage'])

        # And the other side of the rounding, so the row above is not
        # passing because every fractional size is refused.
        rounded_down = self._answer([self._shape(disk_spec=[{'size': 10.4}])])
        self.assertTrue(rounded_down['fits'])

    def test_a_numeric_string_size_is_summed_as_a_number(self):
        # The same helper reads a numeric string as the number it
        # spells and rounds it the same way, so "8.7" is nine. A sum
        # written here instead would either truncate it or raise on it,
        # and raising on a value the ledger accepts turns a caller's
        # question into a server error.
        self.set_validation_mode('warn')
        self._baseline(disk_free_instances=10 * GiB)

        refused = self._answer(
            [self._shape(disk_spec=[{'size': '8.7'}, {'size': 2}])])
        self.assertFalse(
            refused['fits'],
            'a disk spec summing to 11 GB was read as fitting in 10 GB: %r'
            % (refused,))
        self.assertEqual('sufficient_free_disk', refused['stage'])

        accepted = self._answer([self._shape(disk_spec=[{'size': '8'}])])
        self.assertTrue(accepted['fits'])

    def test_both_disk_forms_at_once_is_refused(self):
        resp = self._query([self._shape(disk=10, disk_spec=[{'size': 10}])])

        self.assertEqual(400, resp.status_code)
        self.assertIn('disk_spec', resp.get_json()['error'])


class PlacementRequestTestCase(PlacementTestCase):
    def setUp(self):
        super().setUp()
        self._baseline()

    def test_an_empty_request_is_refused(self):
        for body in ({}, {'shapes': []}):
            resp = self.client.post(
                '/placement', headers={'Authorization': self.admin},
                content_type='application/json', data=json.dumps(body))
            self.assertEqual(400, resp.status_code, body)

    def test_a_shapes_value_which_is_not_a_list_says_so(self):
        # The falsy ones are the point. A body sending 0 or "" has
        # supplied something and got the type wrong, which is a
        # different mistake from supplying nothing, and a caller told
        # "no shapes specified" would go looking for the wrong thing.
        self.set_validation_mode('warn')
        for value in (0, '', {}, 'two', 7):
            resp = self.client.post(
                '/placement', headers={'Authorization': self.admin},
                content_type='application/json',
                data=json.dumps({'shapes': value}))
            self.assertEqual(400, resp.status_code, value)
            self.assertEqual(
                'shapes is not a list of shapes',
                resp.get_json()['error'], value)

    def test_a_shape_without_its_figures_is_refused(self):
        # A null and an omission are one fact from the handler's side:
        # the shape did not say how large it is.
        self.set_validation_mode('warn')
        for shape in ({'memory': 1024}, {'cpus': 1},
                      {'cpus': None, 'memory': 1024},
                      {'cpus': 1, 'memory': None}):
            resp = self._query([shape])
            self.assertEqual(400, resp.status_code, shape)

    def test_an_unknown_key_is_refused(self):
        self.set_validation_mode('warn')
        resp = self._query([self._shape(memory_mb=1024)])

        self.assertEqual(400, resp.status_code)
        self.assertIn('memory_mb', resp.get_json()['error'])

    def test_the_shape_bound_is_enforced_on_the_expanded_total(self):
        # Two shapes, neither of which exceeds the bound on its own,
        # whose counts sum above it. A bound checked on the length of
        # the array, or on one count at a time, would admit this.
        over = api_base.MAX_PLACEMENT_SHAPES // 2 + 1
        resp = self._query([self._shape(count=over), self._shape(count=over)])

        self.assertEqual(400, resp.status_code)
        error = resp.get_json()['error']
        self.assertIn(str(api_base.MAX_PLACEMENT_SHAPES), error)

    def test_a_request_at_the_bound_is_accepted(self):
        # The anti-vacuity half: the bound refuses what is over it and
        # nothing else, so a request of exactly the published number is
        # answered.
        body = self._answer(
            [self._shape(count=api_base.MAX_PLACEMENT_SHAPES)])

        self.assertIn('fits', body)

    def test_a_negative_figure_is_refused(self):
        self.set_validation_mode('warn')

        for shape in (self._shape(cpus=-1), self._shape(memory=-1),
                      self._shape(disk=-1)):
            resp = self._query([shape])
            self.assertEqual(400, resp.status_code, shape)

    def test_a_boolean_is_not_an_integer(self):
        # bool is an int in Python, so a shape saying "cpus": true
        # would otherwise quietly ask about one vCPU.
        self.set_validation_mode('warn')
        resp = self._query([{'cpus': True, 'memory': 1024}])

        self.assertEqual(400, resp.status_code)

    def test_a_count_of_zero_is_refused(self):
        # A shape asked for zero times is not a shape. Accepting it and
        # expanding to nothing would answer a question about a smaller
        # set than the caller described -- and for a body of one such
        # shape, about no set at all.
        self.set_validation_mode('warn')
        resp = self._query([self._shape(count=0)])

        self.assertEqual(400, resp.status_code)
        self.assertIn('count', resp.get_json()['error'])

    def test_a_disk_spec_which_is_not_a_list_is_refused(self):
        # The sum is over a list of disks. A mapping or a bare number
        # reaches mariadb.disk_spec_virtual_gb(), which answers zero
        # with a warning for anything that is not a list -- a silent
        # "this shape wants no disk" where the caller plainly asked for
        # some.
        self.set_validation_mode('warn')
        for value in ({'size': 10}, 10, 'ten'):
            resp = self._query([self._shape(disk_spec=value)])
            self.assertEqual(400, resp.status_code, value)
            self.assertIn('disk_spec', resp.get_json()['error'], value)

    def test_a_shape_which_is_not_an_object_is_refused(self):
        # A list of shapes whose members are not shapes. Each of these
        # would otherwise reach .get() on something that has no .get(),
        # which is a 500 for a caller's mistake.
        self.set_validation_mode('warn')
        for value in ('shape', 7, ['cpus', 1], None):
            resp = self._query([value])
            self.assertEqual(400, resp.status_code, value)
            self.assertIn('shape 0', resp.get_json()['error'], value)


class PlacementAffinityTestCase(PlacementTestCase):
    """A malformed affinity document is refused, and refused once.

    The query and the create read the same document, so they must
    agree about what one may say. A query which accepted a document
    the create refuses would answer about a shape the caller cannot
    actually ask for -- and worse, the scheduler reads a *stored*
    document leniently, so a mistyped hard constraint would be dropped
    and the query would answer that a wholly unconstrained shape fits.
    """

    #: Documents POST /instances refuses, one per rule in the
    #: vocabulary: not a mapping, the two forms mixed, a binary
    #: constraint whose value is not a list, a tag which is not a
    #: non-empty string, and a weighted value which is not an integer.
    MALFORMED = [
        'require_with_tag',
        {'require_with_tag': ['db'], 'barry': 1},
        {'require_with_tag': 'db'},
        {'require_with_tag': ['']},
        {'barry': True},
        {'barry': 'three'},
    ]

    def setUp(self):
        super().setUp()
        self._baseline()
        self.mock_mariadb.create_network(
            'placementnet', namespace='ci', netblock='10.88.0.0/16',
            provide_dhcp=True, provide_dns=True)

    def _create_refusal(self, affinity):
        """What POST /instances answers for this affinity document."""
        resp = self.client.post(
            '/instances', headers={'Authorization': self.tenant},
            content_type='application/json',
            data=json.dumps({
                'name': 'affinity-probe', 'cpus': 1, 'memory': 1024,
                'disk': [{'size': 8}],
                'metadata': {'affinity': affinity}}))
        return resp.status_code, resp.get_json()

    def test_the_query_and_the_create_refuse_identically(self):
        # Run at warn: at enforce the schema layer answers the
        # non-mapping document before either handler guard is reached,
        # which would measure the schema rather than the agreement.
        self.set_validation_mode('warn')

        for affinity in self.MALFORMED:
            query = self._query([self._shape(affinity=affinity)],
                                token=self.tenant)
            create = self._create_refusal(affinity)

            self.assertEqual(400, query.status_code, affinity)
            self.assertEqual(
                create, (query.status_code, query.get_json()),
                'the query and the create disagree about %r' % (affinity,))

    def test_a_well_formed_affinity_document_is_answered(self):
        # The anti-vacuity half. Without it the test above would pass
        # against a handler which refused every affinity document.
        for affinity in ({}, {'require_with_tag': ['db']},
                         {'prefer_without_tag': ['batch']}, {'barry': 1}):
            body = self._answer([self._shape(affinity=affinity)],
                                token=self.tenant)
            self.assertIn('fits', body, affinity)


class PlacementDatabaseTestCase(PlacementTestCase):
    def setUp(self):
        super().setUp()
        self._baseline()

    def test_an_unreachable_database_is_a_503(self):
        # The question cannot be answered at all if the cluster's own
        # resources cannot be read, and a retry is the honest advice:
        # this is not a full cluster, and answering 200 with any
        # verdict would be making one up.
        with mock.patch.object(
                scheduler.Scheduler, 'refresh_metrics',
                side_effect=exceptions.DatabaseUnavailable('down')):
            resp = self._query([self._shape()])

        self.assertEqual(503, resp.status_code)
        self.assertEqual(
            'database unavailable, please retry', resp.get_json()['error'])

    def test_unreadable_capacity_counters_still_answer(self):
        # The case the 503 deliberately does *not* cover. A cluster
        # whose capacity counters cannot be read is still placed
        # against -- the walk falls back to the measured figures and
        # the admission guard is unchanged -- so a create would be
        # answered and the query has to be answered too, or it would
        # stop predicting the thing it exists to predict.
        self.mock_mariadb.node_capacity_read_degraded = True

        body = self._answer([self._shape()])

        self.assertTrue(body['fits'])


class PlacementDisclosureTestCase(PlacementTestCase):
    def setUp(self):
        super().setUp()
        self._baseline()

    def test_a_tenant_is_told_the_verdict_and_no_node_identity(self):
        body = self._answer([self._shape(), self._shape()],
                            token=self.tenant)

        self.assertTrue(body['fits'])
        # Asserted by looking for the nodes in the whole serialised
        # answer rather than by reading the keys it carries: a future
        # field which happened to name a node would be caught here and
        # would not be caught by a check against a list of keys.
        serialised = json.dumps(body)
        for node in self._hypervisors():
            self.assertNotIn(node, serialised)

    def test_a_tenant_is_told_which_stage_refused(self):
        # The set level verdict includes the stage, because a caller
        # which cannot tell "too much memory asked for" from "no node
        # carries the tag you required" cannot act on either.
        body = self._answer([self._shape(memory=1048576)],
                            token=self.tenant)

        self.assertFalse(body['fits'])
        self.assertEqual('sufficient_idle_memory', body['stage'])
        self.assertEqual(0, body['shape'])

    def test_an_administrator_is_told_about_the_nodes(self):
        # The other half of the boundary. Without this the test above
        # would pass against a handler which told nobody anything.
        body = self._answer([self._shape()], token=self.admin)

        self.assertIn(body['shapes'][0]['node'], self._hypervisors())

    def test_an_unauthenticated_query_is_refused(self):
        resp = self.client.post(
            '/placement', content_type='application/json',
            data=json.dumps({'shapes': [self._shape()]}))

        self.assertEqual(401, resp.status_code)


class PlacementSideEffectTestCase(PlacementTestCase):
    """A query writes nothing and publishes nothing.

    Measured at the point where an event becomes durable rather than
    at the point where one is described. The placement walk calls the
    event publisher at every stage whether or not it has anywhere to
    send the result -- it builds each payload first and the publisher
    returns immediately when the caller named no targets -- so counting
    those calls would measure the walk rather than the publication.
    What is asserted here is that nothing reached the event spool, and,
    beside it, the mechanism which makes that so: every call the walk
    made carried an empty target list.

    A refused query is measured as well as a successful one, because
    the refusal path asks the pipeline further questions of its own to
    attribute the refusal, and each of those is another walk which
    could publish.
    """

    def setUp(self):
        super().setUp()
        self._baseline(memory_available=8192)

    def _observed(self, shapes):
        """One query, with what it told the event machinery recorded."""
        targets = []
        real = placement_api.scheduler.add_event_multi

        def spy(event_type, objects, message, **kwargs):
            targets.append(objects)
            return real(event_type, objects, message, **kwargs)

        with mock.patch('shakenfist.scheduler.add_event_multi', spy), \
                mock.patch(
                    'shakenfist.eventlog.eventlog_spool.enqueue') as enqueue:
            body = self._answer(shapes)

        # The walk has to have reached the event sites at all, or an
        # empty spool would prove nothing about the query.
        self.assertNotEqual(
            [], targets,
            'the placement walk published nothing anywhere, so this test '
            'is measuring a query which never happened')
        return body, targets, enqueue

    def test_a_successful_query_publishes_nothing(self):
        body, targets, enqueue = self._observed(
            [self._shape(), self._shape()])

        self.assertTrue(body['fits'])
        self.assertEqual([], enqueue.call_args_list)
        self.assertEqual([[]] * len(targets), targets)

    def test_a_refused_query_publishes_nothing(self):
        body, targets, enqueue = self._observed(
            [self._shape(memory=8192)] * 4)

        self.assertFalse(body['fits'])
        self.assertEqual([], enqueue.call_args_list)
        self.assertEqual([[]] * len(targets), targets)

    def test_a_query_moves_no_capacity_counter(self):
        hypervisors = self._hypervisors()
        for node in hypervisors:
            self.mock_mariadb.set_node_capacity(
                node, limit_cpus=64, limit_memory_mb=65536)
        before = {node: dict(row)
                  for node, row in self.mock_mariadb.node_capacity.items()}

        self._answer([self._shape(memory=8192)] * 2)

        self.assertEqual(before, self.mock_mariadb.node_capacity)

    def test_a_query_creates_no_instance(self):
        before = dict(self.mock_mariadb.instance_objects)

        self._answer([self._shape()])

        self.assertEqual(before, self.mock_mariadb.instance_objects)
