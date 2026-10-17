# Copyright 2019 Michael Still and contributors

"""A null inside a structure, answered where the caller feels it.

``required`` inside an object fragment means present *and not an
explicit null*, the same meaning ``required`` has at the top level. The
fragment branch was first built with ``required`` meaning presence
only, so a required ``network_uuid`` still accepted ``null`` -- and
that was a reachable path to the wrong network:
``Network.from_db_by_ref(None, namespace)`` reads a null name as *no*
name filter, returns every active network in the namespace, and one of
them is used as though the caller had named it.

The compiler's own assertions are in ``test_validation_compiler.py``.
This file exists because those cannot see what that bug actually cost:
on ``POST /instances/{ref}/interfaces`` there is no scheduler in the
way, so the null answered **200 and created an interface on an
arbitrary network**. A status code is a weak regression guard for that;
an object that exists afterwards is not.

**The declarations are pinned, not assumed.** These tests were written
before ``network`` on these two handlers was retyped from ``dict`` to
``networkspec`` and ``arrayofnetworkspec``. So each test recompiles the
handler's *own* published parameters with that one property swapped for
the rendered fragment, and patches the result into the registry for the
duration -- the same ``compile_parameters()`` the registry is built
with, reading the same ``ARGTYPES`` entry the handler names. Against the
current declarations the patch is a no-op; if a declaration moves to a
different token, the swap here is what says so.
"""

import copy
import json
from unittest import mock

from shakenfist.external_api import base as api_base
from shakenfist.external_api import instance as instance_api
from shakenfist.external_api import validation
from shakenfist.tests.external_api.test_required_sweep import (
    SweepFixtureTestCase)


class NestedRequiredNullTestCase(SweepFixtureTestCase):
    """Required-means-not-null, end to end, at the mode which acts on a finding.

    Runs at ``enforce`` -- the shipped default -- because
    ``warn`` records a finding and calls through, so the interface
    would still be created. SweepFixtureTestCase carries the instance,
    network and authentication fixtures; it is the fixture half of
    ``test_required_sweep.py``, split from the sweep's own tests
    precisely so this class can choose a different mode.
    """

    mode = 'enforce'

    def _retyped(self, cls, method, parameter, token):
        """The handler's published parameters, one property swapped.

        Deep copied before anything is written, because ``specs_dict``
        is the live object flasgger publishes from and the swagger
        endpoint runs in the same process as every other test.
        """
        specs = copy.deepcopy(getattr(cls, method).specs_dict)
        swapped = 0
        for parameter_spec in specs.get('parameters', []):
            properties = parameter_spec.get('schema', {}).get('properties', {})
            if parameter in properties:
                properties[parameter] = api_base.ARGTYPES[token]
                swapped += 1
        # A declaration which stopped carrying this parameter would
        # otherwise leave the test compiling the unmodified endpoint and
        # passing for the wrong reason.
        self.assertEqual(
            1, swapped,
            'expected exactly one published %s property to retype' % parameter)
        return mock.patch.dict(
            validation.REGISTRY,
            {(cls.__name__, method): validation.compile_parameters(
                specs['parameters'])})

    def _post(self, url, body):
        response = self.client.post(
            url, headers={'Authorization': self.token},
            content_type='application/json', data=json.dumps(body))
        return response.status_code, response.get_json()

    def test_a_null_network_uuid_creates_no_interface(self):
        """The null-to-arbitrary-network path, measured on the route it was worst on.

        The control is asserted in the same test rather than in a
        neighbouring one: "the null answers 400" is not evidence unless
        the same fixture, the same registry patch and the same request
        shape answer 200 for a netdesc which names a network. Without
        it a broken patch reads as a fixed bug.
        """
        url = '/instances/%s/interfaces' % self.instance.uuid
        before = len(self.instance.interfaces)

        with self._retyped(instance_api.InstanceInterfacesEndpoint,
                           'post', 'network', 'networkspec'):
            status, body = self._post(url, {'network': {'network_uuid': None}})
            self.assertEqual(400, status, body)
            self.assertEqual(
                'network.network_uuid: Missing data for required field.',
                body['error'])
            # The point of this file. This used to be a 200 and
            # an interface on whichever network the namespace query
            # happened to return first.
            self.assertEqual(before, len(self.instance.interfaces))

            status, body = self._post(
                url, {'network': {'network_uuid': str(self.network.uuid)}})
            self.assertEqual(200, status, body)
            self.assertEqual(before + 1, len(self.instance.interfaces))

    def test_a_null_and_an_omission_read_the_same(self):
        """One fact, one message.

        An omitted ``network_uuid`` and a null one are the same thing
        from the handler's side -- it was not given a network -- so a
        caller gets one answer for them. The malformed-input response
        shape was deliberately made uniform, and the comment on
        ``compiled.required_names`` argues exactly this for the top
        level; marshmallow's untouched default would have said
        ``Field may not be null.`` here and ``Missing data for required
        field.`` for the omission.

        Note which layer answers. The omission used to be the
        handler's own ``_netdesc_safety_checks`` refusal, ``network
        specification is missing network_uuid``; with the fragment
        typed, validation answers first and both read alike. That handler
        guard is kept regardless, for the callers validation does not
        stand in front of.
        """
        url = '/instances/%s/interfaces' % self.instance.uuid

        with self._retyped(instance_api.InstanceInterfacesEndpoint,
                           'post', 'network', 'networkspec'):
            null = self._post(url, {'network': {'network_uuid': None}})
            omitted = self._post(url, {'network': {}})

        self.assertEqual(400, null[0], null[1])
        self.assertEqual(null, omitted)

    def test_an_element_of_an_array_names_its_index(self):
        """The same null on instance create, where the netdesc is an
        element rather than the whole parameter.

        The finding has to name which netdesc was wrong -- D48's rule,
        and the reason ``_flatten_messages`` renders an integer key as
        ``name[0]``. A second, valid netdesc is sent first so that the
        index in the message is a real index and not the only one
        available.

        This also asserts the refusal happens before placement: the
        create fixture's single node is not a hypervisor, so anything
        reaching the scheduler answers 507 (the control
        ``test_required_sweep.py`` uses for this route).
        """
        with self._retyped(instance_api.InstancesEndpoint,
                           'post', 'network', 'arrayofnetworkspec'):
            status, body = self._post('/instances', {
                'name': 'nullnetdesc', 'cpus': 1, 'memory': 1024,
                'disk': [{'size': 8}],
                'network': [{'network_uuid': str(self.network.uuid)},
                            {'network_uuid': None}]})

        self.assertEqual(400, status, body)
        self.assertEqual(
            'network[1].network_uuid: Missing data for required field.',
            body['error'])
