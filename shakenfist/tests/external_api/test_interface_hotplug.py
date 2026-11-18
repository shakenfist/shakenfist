# Copyright 2019 Michael Still and contributors

"""A refused interface hot plug must not delete the target instance.

Issue 4366. ``_netdesc_allocate_address()`` is shared between the
instance create route and the interface hot plug route, and every
failure arm in it called ``inst.enqueue_delete_due_error()``. That is
the right cleanup on the create path, where ``inst`` is the half-built
instance this very request made; on the hot plug path ``inst`` is the
pre-existing -- and possibly running -- target instance, so an address
collision (409), a congested network (507) or a failed float
error-deleted the whole instance.

The flake the issue reports is this defect observed from
``test_null_absent_sweep.py``: a random address allocation in the
sweep's /16 landed on the fixed probe address for the hot plug
``address`` row, the 409 arm error-deleted the sweep's fixture
instance into ``created-error`` -- a terminal state -- and every later
hot plug row answered 406 'instance in invalid state for hot plug',
which the sweep correctly reported as vacuous rows.

The fixture is SweepFixtureTestCase's for the reason that class
documents: it carries an authenticated client and an instance placed
on this node, so a hot plug request runs the whole deployed stack. The
tests below use a private /24 with no other allocations on it, so
their fixed addresses cannot collide with anything -- which is exactly
the flake mechanism this file must not reproduce.
"""

import json
from unittest import mock

from shakenfist_utilities import api as sf_api

from shakenfist import exceptions
from shakenfist.external_api import util as api_util
from shakenfist.instance import Instance
from shakenfist.network.interface import NetworkInterface
from shakenfist.tests.external_api.test_required_sweep import (
    SweepFixtureTestCase)


class HotplugRefusalTestCase(SweepFixtureTestCase):

    def setUp(self):
        super().setUp()
        self.hotnet = self.mock_mariadb.create_network(
            'hotplugnet', namespace='system', netblock='10.20.30.0/24',
            provide_dhcp=True, provide_dns=True)

    def _hotplug(self, netdesc):
        return self.client.post(
            '/instances/%s/interfaces' % self.instance.uuid,
            headers={'Authorization': self.token},
            content_type='application/json',
            data=json.dumps({'network': netdesc}))

    def test_address_in_use_answers_409_and_instance_survives(self):
        first = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                               'address': '10.20.30.20'})
        self.assertEqual(200, first.status_code,
                         first.get_data(as_text=True))

        second = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                                'address': '10.20.30.20'})
        self.assertEqual(409, second.status_code,
                         second.get_data(as_text=True))
        self.assertEqual(
            'created', self.instance.state.value,
            'a refused hot plug must not change the state of its '
            'pre-existing target instance')

        # The refusal must be recoverable: the same instance accepts a
        # non-colliding plug afterwards, where the error-deleted
        # instance answered 406 for everything.
        third = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                               'address': '10.20.30.21'})
        self.assertEqual(200, third.status_code,
                         third.get_data(as_text=True))

    def test_float_failure_cleans_up_interface_not_instance(self):
        before = {str(ni.uuid) for ni in self.instance.interfaces}

        with mock.patch.object(
                api_util, 'assign_floating_ip',
                side_effect=lambda ni: sf_api.error(
                    507, 'floating pool congested')):
            response = self._hotplug({
                'network_uuid': str(self.hotnet.uuid),
                'address': '10.20.30.40',
                'float': True})

        self.assertEqual(507, response.status_code,
                         response.get_data(as_text=True))
        self.assertEqual(
            'created', self.instance.state.value,
            'a failed float on hot plug must not delete the instance')
        self.assertEqual(
            before, {str(ni.uuid) for ni in self.instance.interfaces},
            'the half-created interface must not survive a failed float')

        # The failed plug's address reservation was released with the
        # interface: the same address plugs cleanly afterwards.
        retry = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                               'address': '10.20.30.40'})
        self.assertEqual(200, retry.status_code,
                         retry.get_data(as_text=True))

    def test_create_address_in_use_still_deletes_the_new_instance(self):
        """The create path's cleanup contract, pinned as a floor.

        On the create route the refused instance is this request's own
        half-built one, and error-deleting it is what releases its
        addresses -- PLAN-transient-capacity-refusals documents that as
        the only release path. The hot plug fix must not loosen it.
        """
        first = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                               'address': '10.20.30.50'})
        self.assertEqual(200, first.status_code,
                         first.get_data(as_text=True))

        created = []
        real_new = Instance.new

        def spy_new(*args, **kwargs):
            inst = real_new(*args, **kwargs)
            created.append(inst)
            return inst

        with mock.patch.object(Instance, 'new', side_effect=spy_new):
            response = self.client.post(
                '/instances', headers={'Authorization': self.token},
                content_type='application/json',
                data=json.dumps({
                    'name': 'clashcreate', 'cpus': 1, 'memory': 1024,
                    'disk': [{'size': 8}],
                    'network': [{'network_uuid': str(self.hotnet.uuid),
                                 'address': '10.20.30.50'}]}))

        self.assertEqual(409, response.status_code,
                         response.get_data(as_text=True))
        self.assertEqual(1, len(created))
        self.assertIn(created[0].state.value, Instance.TERMINAL_STATES)


class HotplugDeviceIndexTestCase(SweepFixtureTestCase):
    """The hot plug endpoint does not choose a device index (issue 4946).

    Reading the highest index in use and then creating the interface was
    two steps with nothing serialising them, so overlapping plugs took
    the same index. The endpoint now leaves the choice to
    NetworkInterface.new(), inside the insert's own retry loop under the
    UNIQUE constraint. The cluster CI test which races real plugs only
    runs in the merge queue; these are the pull request time guard on
    the caller.
    """

    def setUp(self):
        super().setUp()
        self.hotnet = self.mock_mariadb.create_network(
            'indexnet', namespace='system', netblock='10.20.31.0/24',
            provide_dhcp=True, provide_dns=True)

    def _hotplug(self, netdesc):
        return self.client.post(
            '/instances/%s/interfaces' % self.instance.uuid,
            headers={'Authorization': self.token},
            content_type='application/json',
            data=json.dumps({'network': netdesc}))

    def test_the_endpoint_leaves_the_index_to_new(self):
        real_new = NetworkInterface.new
        orders = []

        def spy_new(*args, **kwargs):
            orders.append(args[3] if len(args) > 3 else kwargs.get('order'))
            return real_new(*args, **kwargs)

        with mock.patch.object(NetworkInterface, 'new', side_effect=spy_new):
            response = self._hotplug(
                {'network_uuid': str(self.hotnet.uuid)})

        self.assertEqual(200, response.status_code,
                         response.get_data(as_text=True))
        self.assertEqual([None], orders)

    def test_sequential_plugs_get_distinct_increasing_indexes(self):
        plugged = []
        for _ in range(3):
            response = self._hotplug(
                {'network_uuid': str(self.hotnet.uuid)})
            self.assertEqual(200, response.status_code,
                             response.get_data(as_text=True))
            plugged.append(response.get_json()['order'])

        self.assertEqual(sorted(set(plugged)), plugged)

    def test_an_exhausted_retry_budget_releases_the_address(self):
        """No interface row exists to release the address, so the API does.

        Every refusal arm in _netdesc_allocate_address() cleans up what
        it reserved. NetworkInterface.new() running out of attempts --
        more overlapping plugs than its budget covers, or a caller's MAC
        already in use -- was the one which let an exception escape as a
        500 with the address still reserved and nothing left to ever
        release it.
        """
        with mock.patch.object(
                NetworkInterface, 'new',
                side_effect=exceptions.NetworkInterfaceCollision(
                    'every attempt collided')):
            response = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                                      'address': '10.20.31.60'})

        self.assertEqual(409, response.status_code,
                         response.get_data(as_text=True))
        self.assertEqual(
            'created', self.instance.state.value,
            'a refused hot plug must not change the state of its '
            'pre-existing target instance')

        retry = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                               'address': '10.20.31.60'})
        self.assertEqual(200, retry.status_code,
                         retry.get_data(as_text=True))

    def test_a_refused_plug_with_no_address_releases_nothing(self):
        # address 'none' reserves nothing, so there is nothing to give
        # back -- and asking IPAM to release None is not a no-op.
        with mock.patch.object(
                NetworkInterface, 'new',
                side_effect=exceptions.NetworkInterfaceCollision(
                    'every attempt collided')), \
             mock.patch('shakenfist.ipam.IPAM.release') as release:
            response = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                                      'address': 'none'})

        self.assertEqual(409, response.status_code,
                         response.get_data(as_text=True))
        release.assert_not_called()

    def test_an_outage_answers_503_and_keeps_the_address(self):
        """A database outage is not a collision, and proves nothing.

        It used to read as a collision: ten retries, then a 409 naming a
        MAC or index race. It is retryable, so a 503. And whether the
        row was written is unknown, so the address must stay reserved:
        released under a committed row it would go to a second guest.
        """
        with mock.patch.object(
                NetworkInterface, 'new',
                side_effect=exceptions.DatabaseUnavailable('away')), \
             mock.patch('shakenfist.ipam.IPAM.release') as release:
            response = self._hotplug({'network_uuid': str(self.hotnet.uuid),
                                      'address': '10.20.31.61'})

        self.assertEqual(503, response.status_code,
                         response.get_data(as_text=True))
        self.assertIn('Retry-After', response.headers)
        release.assert_not_called()
        self.assertEqual(
            'created', self.instance.state.value,
            'a hot plug refused by an outage must not change the state '
            'of its pre-existing target instance')

    def _create_instance(self, new_side_effect):
        created = []
        real_new = Instance.new

        def spy_new(*args, **kwargs):
            inst = real_new(*args, **kwargs)
            created.append(inst)
            return inst

        with mock.patch.object(Instance, 'new', side_effect=spy_new), \
             mock.patch.object(NetworkInterface, 'new',
                               side_effect=new_side_effect):
            response = self.client.post(
                '/instances', headers={'Authorization': self.token},
                content_type='application/json',
                data=json.dumps({
                    'name': 'refusedcreate', 'cpus': 1, 'memory': 1024,
                    'disk': [{'size': 8}],
                    'network': [{'network_uuid': str(self.hotnet.uuid)}]}))
        self.assertEqual(1, len(created))
        return response, created[0]

    def test_a_refused_create_deletes_the_new_instance(self):
        # The create arm of the same handler. Its instance is this
        # request's own half-built one, so unlike a hot plug's target it
        # is error-deleted.
        response, inst = self._create_instance(
            exceptions.NetworkInterfaceCollision('every attempt collided'))

        self.assertEqual(409, response.status_code,
                         response.get_data(as_text=True))
        self.assertIn(inst.state.value, Instance.TERMINAL_STATES)

    def test_an_outage_on_create_answers_503_and_deletes_the_instance(self):
        response, inst = self._create_instance(
            exceptions.DatabaseUnavailable('away'))

        self.assertEqual(503, response.status_code,
                         response.get_data(as_text=True))
        self.assertIn(str(inst.uuid), response.get_data(as_text=True))
        self.assertIn(inst.state.value, Instance.TERMINAL_STATES)
