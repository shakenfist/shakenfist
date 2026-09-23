# Copyright 2019 Michael Still and contributors

"""Null equals absent, asked of the server as a derived differential.

Finding B4 of the phase 8 push audit of ``PLAN-api-input-validation``
(issue 4252). ``test_nested_sweep.py``'s observable is ``(status,
exception, message)``, and 35 of its 43 accepted rows resolve to a
scheduler 507 -- so that table is structurally blind to **accepted with
the wrong meaning**, which is the class of both defects the phase 7
review found (a null ``video.model`` stored and rendered into the
domain XML as ``type='None'``; ``{"float": "false"}`` floating the
interface). The two tests which do observe an effect are hand-written
one-offs, and nothing derives the obligation to have one.

This file tests the api_reference's promise as a general property: a
JSON null inside a ``diskspec``, ``networkspec`` or ``videospec``
"means not supplied" and gets you the same behaviour as omitting the
key. Three things about its construction are the point:

* **The enumeration is derived.** The keys come from the rendered
  schemas in ``api_base.ARGTYPES``, so a key added to a spec joins the
  sweep without this file changing -- it fails, loudly, until it is
  given a probe value below. ``test_every_structured_schema_is_swept``
  closes the other gap: a *new* spec token with a property vocabulary
  fails until it is routed here.
* **It is differential.** Each row sends the key as an explicit null
  and then omits it entirely, and requires the two requests to produce
  the same observable. There is no hand-written expected value, so a
  row cannot pin a wrong answer the way ``net.float.yes`` did for
  years.
* **Vacuity fails instead of passing.** Before the null and the
  omission are compared, the same observable must distinguish a real
  value of the key (the probe) from the omission. A row whose
  observable cannot see the key at all -- which is exactly what a
  status code is for most of these -- is a failure, not a pass.

The observable is deeper than a status code because it has to be: it
records what the handler passed to ``Instance.new`` (the disk and
video specs after the handler's transformations) and to
``NetworkInterface.new`` (the netdesc at the moment of interface
creation, before ``new()`` mutates it), and whether the handler
decided to float an interface. Values the server chose -- a randomly
allocated address, a generated default -- are collapsed to a
placeholder, because two same-meaning requests must not differ just
because a random allocator ran twice; values the caller sent are kept
verbatim. A null-valued key is dropped, so "stored an explicit null"
and "never had the key" read identically exactly when they mean the
same thing to every ``.get()`` downstream, and differ when the null
changed what the server did.

The one live defect this differential found on being written is fixed
in the same change (issue 4252): ``noneish(None)`` is truthy, so an
explicit null ``address`` took the no-address special case reserved
for the literal string ``'none'``, and a caller the published schema
promised a random address got an addressless interface. Both routes
showed it; nothing in the 99-row nested sweep could.
"""

import copy
import json
from collections import namedtuple
from unittest import mock

from shakenfist.external_api import base as api_base
from shakenfist.external_api import util as api_util
from shakenfist.instance import Instance
from shakenfist.network.interface import NetworkInterface
from shakenfist.tests.external_api.test_required_sweep import (
    SweepFixtureTestCase)


CREATE = 'create'
HOTPLUG = 'hotplug'

#: Substituted for the sweep network's uuid when a request is sent.
NETWORK = '{network}'

#: What a recorded value becomes when the caller did not send it: a
#: random allocation or a server default. Two same-meaning requests
#: must compare equal even though the allocator gave them different
#: addresses, and must compare different when one was allocated an
#: address and the other was not.
SERVER_CHOSEN = '<server-chosen>'

#: Which endpoints carry which structured spec, and under which body
#: parameter. This is the one hand-written mapping, and
#: test_every_structured_schema_is_swept is what keeps it complete: a
#: new property-carrying ARGTYPES token fails there until it appears
#: here. The keys of each spec are never listed in this file at all.
ROUTES = [
    ('diskspec', CREATE, 'disk', True),
    ('networkspec', CREATE, 'network', True),
    ('networkspec', HOTPLUG, 'network', False),
    ('videospec', CREATE, 'video', False),
]

#: A complete, valid example of each spec. The base request for a key
#: is this exemplar with that key removed, so the exemplar must stay
#: valid with any single key missing (network_uuid's row measures two
#: refusals against each other, which is the property holding too).
EXEMPLARS = {
    'diskspec': {'size': 8, 'base': 'http://example.com/null-sweep.qcow2'},
    'networkspec': {'network_uuid': NETWORK},
    'videospec': {'model': 'vga', 'memory': 16384},
}

#: The anti-vacuity probe: a real value of the key which the
#: observable must distinguish from omitting the key, or the row fails
#: as vacuous. A key added to a schema has no entry here and fails the
#: sweep until it is given one -- that failure is the derived
#: obligation the issue asks for, replacing the hand-written one-off
#: tests nothing used to demand. A dict value is keyed by route,
#: needed where a value is unique-constrained (a MAC address) or
#: reserved by use (an IP address) and so cannot be sent twice.
PROBES = {
    ('diskspec', 'size'): 12,
    ('diskspec', 'base'): 'http://example.com/null-sweep-probe.qcow2',
    ('diskspec', 'bus'): 'sata',
    ('diskspec', 'type'): 'cdrom',
    ('networkspec', 'network_uuid'): NETWORK,
    ('networkspec', 'macaddress'): {
        CREATE: '02:00:00:55:66:77', HOTPLUG: '02:00:00:55:66:78'},
    # High in the /16 below, where a collision with the random
    # allocations the other rows perform is vanishingly unlikely.
    ('networkspec', 'address'): {
        CREATE: '10.77.99.55', HOTPLUG: '10.77.99.56'},
    ('networkspec', 'model'): 'e1000',
    ('networkspec', 'float'): True,
    ('videospec', 'model'): 'qxl',
    ('videospec', 'memory'): 32768,
    ('videospec', 'vdi'): 'vnc',
}

#: Everything one request did, canonicalised. ``instances`` is what
#: reached Instance.new (disk_spec and video, the two structured
#: kwargs); ``interfaces`` is each netdesc as NetworkInterface.new
#: received it; ``floats`` is how many interfaces the handler decided
#: to float (assign_floating_ip is stubbed to succeed, because whether
#: the fixture carries a floating network is not the property under
#: test).
Observable = namedtuple(
    'Observable',
    ['status', 'recorded', 'error', 'instances', 'interfaces', 'floats'])


def _leaves(value, out):
    """Every scalar the caller put in the request body."""
    if isinstance(value, dict):
        for v in value.values():
            _leaves(v, out)
    elif isinstance(value, list):
        for v in value:
            _leaves(v, out)
    elif value is not None:
        out.append(value)


def _canonical(value, sent):
    """Null-valued keys dropped, server-chosen scalars collapsed.

    Dropping a null makes "the null was stored" and "the key was never
    there" read identically, which is the promise under test read at
    the point of consumption -- every consumer of these structures
    reads them with ``.get()``. The placeholder is what makes the
    comparison immune to randomness without going blind: an allocated
    address differs per request, so two allocations must compare
    equal, but an allocation and an absence must not.
    """
    if isinstance(value, dict):
        return {k: _canonical(v, sent)
                for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_canonical(v, sent) for v in value]
    if any(v == value for v in sent):
        return value
    return SERVER_CHOSEN


class _NullAbsentMixin:
    """The runner, mode-less for the reason _NestedSweepMixin is."""

    def setUp(self):
        super().setUp()
        # A /16 of its own rather than SweepFixtureTestCase's /24:
        # every accepted netdesc row allocates a random address which
        # is never released within the test, and the fixed probe
        # addresses must not flake on colliding with one.
        self.nullnet = self.mock_mariadb.create_network(
            'nullsweepnet', namespace='system', netblock='10.77.0.0/16',
            provide_dhcp=True, provide_dns=True)

    def _resolve(self, value):
        if isinstance(value, str):
            return value.replace(NETWORK, str(self.nullnet.uuid))
        if isinstance(value, dict):
            return {k: self._resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def _observe(self, route, overlay, sequence):
        """Drive one request and say everything it did."""
        overlay = self._resolve(overlay)
        if route == CREATE:
            # A distinct name per request: a repeated name answers 409,
            # which would measure the collision rather than the null.
            body = {'name': 'nullsweep%d' % sequence, 'cpus': 1,
                    'memory': 1024, 'disk': [{'size': 8}]}
            body.update(overlay)
            url = '/instances'
        else:
            body = dict(overlay)
            url = '/instances/%s/interfaces' % self.instance.uuid

        sent = []
        _leaves(body, sent)

        instances = []
        interfaces = []
        floats = []
        real_instance_new = Instance.new
        real_interface_new = NetworkInterface.new

        def recording_instance_new(*args, **kwargs):
            # Snapshotted at call time: the handler and Instance.new
            # both mutate these structures afterwards, and the fact
            # under test is what the handler passed.
            instances.append({
                'disk_spec': copy.deepcopy(kwargs.get('disk_spec')),
                'video': copy.deepcopy(kwargs.get('video'))})
            return real_instance_new(*args, **kwargs)

        def recording_interface_new(interface_uuid, netdesc, instance_uuid,
                                    order):
            # Before the call, deliberately: new() writes a generated
            # MAC into the netdesc, which is server-chosen noise.
            interfaces.append(copy.deepcopy(netdesc))
            return real_interface_new(
                interface_uuid, netdesc, instance_uuid, order)

        def recording_float(ni):
            floats.append(str(ni.uuid))

        before_records = self.mock_record_exception.call_count
        with mock.patch.object(
                Instance, 'new', side_effect=recording_instance_new), \
                mock.patch.object(
                    NetworkInterface, 'new',
                    side_effect=recording_interface_new), \
                mock.patch.object(
                    api_util, 'assign_floating_ip',
                    side_effect=recording_float):
            response = self.client.post(
                url, headers={'Authorization': self.token},
                content_type='application/json', data=json.dumps(body))

        payload = response.get_json()
        error = payload.get('error') if isinstance(payload, dict) else None
        return Observable(
            response.status_code,
            self.mock_record_exception.call_count > before_records,
            error,
            _canonical(instances, sent),
            _canonical(interfaces, sent),
            len(floats))

    def test_the_controls_still_work(self):
        """A rotted fixture must fail here, not read as a verdict."""
        create = self._observe(CREATE, {}, 0)
        self.assertEqual(
            (507, False,
             'No nodes remaining at scheduling stage is_hypervisor'),
            (create.status, create.recorded, create.error),
            'the minimal create did not reach placement, so no verdict '
            'in this file can be trusted: %r' % (create,))

        hotplug = self._observe(
            HOTPLUG, {'network': {'network_uuid': NETWORK}}, 0)
        self.assertEqual(
            (200, False), (hotplug.status, hotplug.recorded),
            'the minimal hotplug was refused: %r' % (hotplug,))
        self.assertEqual(
            1, len(hotplug.interfaces),
            'the hotplug control created no interface, so an accepted '
            'row here proves nothing')

    def test_every_structured_schema_is_swept(self):
        """The derived obligation: a new spec cannot arrive unswept.

        Walks ARGTYPES for every token which renders an object carrying
        a property vocabulary -- directly or as the element type of an
        array -- and requires the schema to be one this sweep routes.
        Identity rather than equality, because the array tokens nest
        the very same constant as their single forms (D40), and a
        copied-but-equal schema would be exactly the drift D40 exists
        to prevent.
        """
        swept = [api_base.ARGTYPES[spec] for spec, _, _, _ in ROUTES]
        for token, rendered in sorted(api_base.ARGTYPES.items()):
            schema = rendered
            if schema.get('type') == 'array':
                schema = schema.get('items') or {}
            if not isinstance(schema, dict) or not schema.get('properties'):
                continue
            self.assertTrue(
                any(schema is s for s in swept),
                'ARGTYPES token %r renders a property-carrying schema '
                'this sweep does not cover. Add it to ROUTES, give it '
                'an exemplar in EXEMPLARS and a probe per key in '
                'PROBES, so the null-equals-absent property is tested '
                'for it from its first release' % token)

    def test_null_equals_absent(self):
        """The property, measured; and vacuity measured first."""
        failures = []
        sequence = 0
        for spec, route, parameter, as_list in ROUTES:
            for key in api_base.ARGTYPES[spec]['properties']:
                row = '%s.%s on %s' % (spec, key, route)
                if (spec, key) not in PROBES:
                    failures.append(
                        '%s: no anti-vacuity probe. A new schema key '
                        'joins this sweep automatically, but must be '
                        'given a real value in PROBES that the '
                        'observable can see' % row)
                    continue
                probe = PROBES[(spec, key)]
                if isinstance(probe, dict):
                    probe = probe[route]

                base_spec = dict(EXEMPLARS[spec])
                base_spec.pop(key, None)
                observed = {}
                for label, spec_body in (
                        ('absent', base_spec),
                        ('null', {**base_spec, key: None}),
                        ('probe', {**base_spec, key: probe})):
                    sequence += 1
                    wrapped = [spec_body] if as_list else spec_body
                    observed[label] = self._observe(
                        route, {parameter: wrapped}, sequence)

                if observed['probe'] == observed['absent']:
                    failures.append(
                        '%s [vacuous]: the observable cannot tell the '
                        'probe value %r from omission, so this row '
                        'would pass no matter what a null means. Both '
                        'answered: %r'
                        % (row, probe, observed['absent']))
                    continue

                if observed['null'] != observed['absent']:
                    failures.append(
                        '%s: an explicit null and an omission mean '
                        'different things, where the api_reference '
                        'promises a null "means not supplied":\n'
                        '    null:   %r\n    absent: %r'
                        % (row, observed['null'], observed['absent']))

        self.assertEqual(
            [], failures,
            'the null-equals-absent differential found rows where the '
            'server disagrees with the api_reference promise (or rows '
            'it cannot see, which are as bad):\n' + '\n'.join(failures))


class NullAbsentEnforceTestCase(_NullAbsentMixin, SweepFixtureTestCase):
    """The shipped default."""

    mode = 'enforce'


class NullAbsentWarnTestCase(_NullAbsentMixin, SweepFixtureTestCase):
    """The operator's rollback.

    The property is mostly a statement about handler guards rather
    than about the schema layer -- a guard which treats a null and an
    omission as one fact does so in every mode -- so it must survive
    the rollback. This is also what proves the issue 4252 address fix
    is a handler fix rather than a schema artifact: `warn` hands the
    handler exactly what the caller sent.
    """

    mode = 'warn'
