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

from shakenfist.external_api import util as api_util
from shakenfist.instance import Instance
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
