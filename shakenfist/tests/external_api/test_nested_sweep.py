# Copyright 2019 Michael Still and contributors

"""Every value inside a spec, asked of the server rather than of the schema.

Step 5 of ``docs/plans/PLAN-api-input-validation-phase-07-structured.md``.
Step 4 retyped the four structured declarations -- ``disk``,
``network`` and ``video`` on ``POST /instances``, and ``network`` on
``POST /instances/{ref}/interfaces`` -- so a diskspec, a networkspec
and a videospec are now described to the validation layer rather than
handed to the handler unexamined. That moved the contract for the
twenty values findings F5 and F6 of the plan measured. This file pins
the result.

Three things make a row here worth having, and each is a habit the
earlier phases had to learn the hard way:

* **The answer is measured, not derived.** Every row drives a real
  request through the whole authenticated decorator stack, because
  phase 3's review found a handler tested in isolation and the
  deployed behaviour giving different answers, and because phase 5's
  finding F5 is what happens when a well formed question is asked of
  the AST instead of the server.
* **Refusal is paired with acceptance.** Every key in every spec
  carries at least one row which is still *accepted*
  (``test_every_key_has_an_accepted_value`` derives that from the table
  rather than trusting it). A sweep which only proves things are
  refused would pass just as well against a schema which refused
  everything, and "the schema is no narrower than the handler" is the
  width rule phase 6 wrote down and this phase's two deliberate
  narrowings (D50) are measured against.
* **Both endpoints are swept.** A netdesc is declared twice, and the
  interface hotplug route has no scheduler in front of it: where
  instance create answers 507 for a request which survived every
  guard, hotplug answers 200 *and creates an interface*. So an
  accepted row there is checked by counting interfaces, not by reading
  a status code -- which is the regression guard finding F7 needed and
  did not have, since its symptom was an interface on an arbitrary
  network rather than a bad status.

The table carries two answers per row, and the difference between them
is decision D42. The ``enforce`` column is what a caller sees today.
The ``warn`` column is what the same request answered *before* this
phase, and an operator who
sets ``API_VALIDATION_MODE=warn`` because this phase broke their fleet
must get exactly that back. ``NestedSweepWarnTestCase`` is the proof,
and ``NestedSweepOffTestCase`` re-runs the same table at ``off``,
where ``check()`` does not run at all.

Ten rows deliberately answer 400 at ``warn`` where they used to
answer 507 or 200, and they are the phase's two new *handler* guards
rather than schema checks -- D42 keeps every existing guard, and step 4
added two more because a rollback must not hand back the bug the phase
just closed. They are marked ``moves at warn`` in the notes:
``{"network_uuid": null}`` on either route, and the six shapes of
diskspec which ask for neither a size nor a base -- one of which is a
diskspec whose only key is a typo, since a typo'd ``size`` leaves the
spec asking for nothing, and one of which asks for a base of the
literal string ``none``, which ``util_general.noneish`` reads as no
base at all.

A third handler guard joined them in review: the videospec's ``model``
and ``memory`` checks were presence tests, so an explicit null passed
them and was stored. They are value tests now, which is why
``video.model.null`` and ``video.memory.null`` are refused in every
column.

Six more rows moved at ``warn`` after the phase, when the issue #4223
fix taught ``from_db_by_ref`` to answer "not found" for a null or
non-string ref: the ``net.uuid``/``hotplug.uuid`` ``int``, ``dict``
and ``bool`` arms answer the handler's own 404 where
``util_general.valid_uuid4`` used to fault. That guard lives in the
lookup layer, so no validation-mode rollback restores the 500.

Mutation coverage lives in ``tools/mutate-nested-sweep.sh``, which
breaks each schema and each guard on purpose and names the row which
must fail. Reading a guard cannot distinguish "this holds" from "this
cannot fail"; a row which passes for the wrong reason is worse than a
missing row, because it reads as evidence.
"""

import json
from collections import namedtuple
from unittest import mock

from shakenfist.instance import Instance
from shakenfist.tests.external_api.test_required_sweep import (
    SweepFixtureTestCase)


CREATE = 'create'
HOTPLUG = 'hotplug'

#: Substituted for the fixture network's uuid when a row is sent.
NETWORK = '{network}'

#: What a request answered: the status, whether an exception record was
#: written (which is how this codebase records a server fault), and the
#: ``error`` key of the response body.
Answer = namedtuple('Answer', ['status', 'recorded', 'error'])

#: The request survived every guard. On instance create that is a 507,
#: because this fixture's single node is not a hypervisor -- the same
#: control test_required_sweep.py and test_instance_create_validation.py
#: use, and proof the request got past the whole handler. On interface
#: hotplug there is no scheduler, so it is a 200 and one more interface
#: on the instance; the runner asserts that count, not just the status.
ACCEPTED = 'accepted'

ACCEPTED_ON_CREATE = Answer(
    507, False, 'No nodes remaining at scheduling stage is_hypervisor')
ACCEPTED_ON_HOTPLUG = Answer(200, False, None)

#: The phase 5 shape a caller used to get for a nested value the
#: handler could not cope with: a bare ``server error``, an exception
#: record under /srv/shakenfist/exceptions/, and nothing naming the key
#: which caused it. Eleven rows of finding F5 answered this before the
#: phase, and the warn column is where most still do -- the six
#: non-string network_uuid rows answer a 404 there since the issue
#: #4223 fix, whose guard sits in from_db_by_ref below the validation
#: layer.
FAULTED = Answer(500, True, 'server error')


def refused(message, status=400):
    """A 4xx naming what was wrong, with no exception recorded."""
    return Answer(status, False, message)


Case = namedtuple(
    'Case', ['case_id', 'route', 'spec', 'key', 'body', 'enforce', 'warn',
             'note'])


# One row per (spec, key, sent value). `body` is an overlay onto the
# route's minimal valid request, so each row says only what it changes.
#
# The notes name the plan's finding a row closes. F5 is the eleven
# nested values which were a recorded 500; F6 is the nine which were
# accepted in silence; D4x names a decision the row is the measurement
# of. A row whose note says `width` is one this phase deliberately
# still accepts, and the reason is on the row: a schema narrower than
# its handler is a breaking change wearing the clothes of a
# correctness fix (phase 6's width rule).
CASES = [
    # ---------------------------------------------------------------
    # The controls. Asserted first by the runner, because a request
    # which 404s because a fixture rotted looks exactly like a handler
    # refusing a value, and no verdict from a broken fixture can be
    # trusted.
    # ---------------------------------------------------------------
    Case('control.create', CREATE, None, None,
         {},
         ACCEPTED, ACCEPTED,
         'control: the minimal valid create, which must reach placement'),
    Case('control.hotplug', HOTPLUG, None, None,
         {'network': {'network_uuid': NETWORK}},
         ACCEPTED, ACCEPTED,
         'control: the minimal valid hotplug, which must create an interface'),

    # ---------------------------------------------------------------
    # diskspec: base
    # ---------------------------------------------------------------
    Case('disk.base.int', CREATE, 'diskspec', 'base',
         {'disk': [{'size': 8, 'base': 5}]},
         refused('disk[0].base: Not a valid string.'), FAULTED,
         "F5 row 1: AttributeError: 'int' object has no attribute 'lower' "
         'in util_general.noneish'),
    Case('disk.base.bool', CREATE, 'diskspec', 'base',
         {'disk': [{'size': 8, 'base': True}]},
         refused('disk[0].base: Not a valid string.'), FAULTED,
         'F5 row 1, the bool arm'),
    Case('disk.base.list', CREATE, 'diskspec', 'base',
         {'disk': [{'size': 8, 'base': ['a']}]},
         refused('disk[0].base: Not a valid string.'), FAULTED,
         'F5 row 1, the list arm'),
    Case('disk.base.url', CREATE, 'diskspec', 'base',
         {'disk': [{'size': 8, 'base': 'http://example.com/i.qcow2'}]},
         ACCEPTED, ACCEPTED,
         'width: base is typed and never formatted, because the value is '
         'prefix dispatched and may be a URL, an sf:// reference, a label: '
         'reference or a bare artifact name'),
    Case('disk.base.null', CREATE, 'diskspec', 'base',
         {'disk': [{'size': 8, 'base': None}]},
         ACCEPTED, ACCEPTED,
         'width, census N1: the shipped CLI and the ansible collection both '
         'send an explicit null base on an ordinary request'),

    # ---------------------------------------------------------------
    # diskspec: size
    # ---------------------------------------------------------------
    Case('disk.size.badstring', CREATE, 'diskspec', 'size',
         {'disk': [{'size': 'banana'}]},
         refused('disk[0].size: Not a valid integer.'), FAULTED,
         'F5 row 2: ValueError from int() in instance._safe_int_cast'),
    Case('disk.size.dict', CREATE, 'diskspec', 'size',
         {'disk': [{'size': {'gb': 8}}]},
         refused('disk[0].size: Not a valid integer.'), FAULTED,
         'F5 row 2, the TypeError arm'),
    Case('disk.size.negative', CREATE, 'diskspec', 'size',
         {'disk': [{'size': -5}]},
         refused('disk[0].size: Must be greater than or equal to 0.'),
         ACCEPTED,
         'F6 row 1, and the whole argument for D45\'s minimum: a negative '
         'size corrupts the capacity ledger at scheduler.py:473'),
    Case('disk.size.fractional', CREATE, 'diskspec', 'size',
         {'disk': [{'size': 8.5}]},
         refused('disk[0].size: Not a valid integer.'), ACCEPTED,
         'F6 row 2 and D46: under a check-only layer a field which accepts '
         '8.5 as an integer hands the handler 8.5 and has validated nothing'),
    Case('disk.size.integral_float', CREATE, 'diskspec', 'size',
         {'disk': [{'size': 8.0}]},
         ACCEPTED, ACCEPTED,
         'width, D46: JSON has one numeric type, so 8.0 and 8 are the same '
         'number and reading one as the integer 8 invents nothing'),
    Case('disk.size.bool', CREATE, 'diskspec', 'size',
         {'disk': [{'size': True}]},
         refused('disk[0].size: Not a valid integer.'), ACCEPTED,
         'F6 row 2, the bool arm -- int(True) is 1, which is exactly the '
         'silent coercion D46 refuses'),
    Case('disk.size.numeric_string', CREATE, 'diskspec', 'size',
         {'disk': [{'size': '20'}]},
         ACCEPTED, ACCEPTED,
         'width, D46 and census N3: "20" reaches int("20") and works end to '
         'end today, so refusing it would be a validator narrower than its '
         'handler. Definition-of-done item 5 is wrong about this row'),
    Case('disk.size.zero_with_base', CREATE, 'diskspec', 'size',
         {'disk': [{'size': 0, 'base': 'http://example.com/i.qcow2'}]},
         ACCEPTED, ACCEPTED,
         'width, D45: the bound is minimum 0, not 1 -- a zero size means '
         '"the size of the base image", which four code paths skip '
         'explicitly and test_ci_capacity_wait.py relies on'),
    Case('disk.size.int', CREATE, 'diskspec', 'size',
         {'disk': [{'size': 8}]},
         ACCEPTED, ACCEPTED,
         'width: the ordinary case, and every first-party caller\'s'),
    Case('disk.size.null_with_base', CREATE, 'diskspec', 'size',
         {'disk': [{'size': None, 'base': 'http://example.com/i.qcow2'}]},
         ACCEPTED, ACCEPTED,
         'width, census N1: the ansible collection sends size: None on its '
         'diskspecs: path and apiclient.py deliberately leaves it uncoerced'),

    # ---------------------------------------------------------------
    # diskspec: bus
    # ---------------------------------------------------------------
    Case('disk.bus.nvme', CREATE, 'diskspec', 'bus',
         {'disk': [{'size': 8, 'bus': 'nvme'}]},
         ACCEPTED, ACCEPTED,
         'width: guest_ci_tests/test_disks.py sends nvme'),
    Case('disk.bus.null', CREATE, 'diskspec', 'bus',
         {'disk': [{'size': 8, 'bus': None}]},
         ACCEPTED, ACCEPTED,
         'width, census N1: the CLI sends bus: None on every -d disk'),
    Case('disk.bus.nonsense', CREATE, 'diskspec', 'bus',
         {'disk': [{'size': 8, 'bus': 'banana'}]},
         refused('disk[0].bus: Must be one of: sata, scsi, usb, virtio, '
                 'nvme.'),
         refused('invalid disk bus banana'),
         'D42 and D43: the enum publishes a refusal instance._get_disk_device '
         'already makes, so the guard answers at warn and the schema answers '
         'first at enforce. Two messages for one fact, which the plan\'s '
         '"duplicated checking drifts" risk asks be pinned'),
    Case('disk.bus.ide', CREATE, 'diskspec', 'bus',
         {'disk': [{'size': 8, 'bus': 'ide'}]},
         refused('disk[0].bus: Must be one of: sata, scsi, usb, virtio, '
                 'nvme.'),
         refused('invalid disk bus ide'),
         'ide is absent from the enum deliberately: support was removed in '
         'v0.7 and the bus check refuses it like any other unknown bus. The '
         'IDE-specific guard later in the handler was dead code on that '
         'account and has been deleted; this row pins that neither mode '
         'ever answered with its message'),
    Case('disk.bus.int', CREATE, 'diskspec', 'bus',
         {'disk': [{'size': 8, 'bus': 5}]},
         refused('disk[0].bus: Not a valid string.'),
         refused('invalid disk bus 5'),
         'a non-string bus was already guarded, so this row is about which '
         'layer answers rather than about a new refusal'),

    # ---------------------------------------------------------------
    # diskspec: type
    # ---------------------------------------------------------------
    Case('disk.type.cdrom', CREATE, 'diskspec', 'type',
         {'disk': [{'size': 8, 'type': 'cdrom'}]},
         ACCEPTED, ACCEPTED,
         'width: cluster_ci_tests/test_disk_specs.py sends cdrom'),
    Case('disk.type.disk', CREATE, 'diskspec', 'type',
         {'disk': [{'size': 8, 'type': 'disk'}]},
         ACCEPTED, ACCEPTED,
         'width: what the CLI and the ansible collection always send'),
    Case('disk.type.null', CREATE, 'diskspec', 'type',
         {'disk': [{'size': 8, 'type': None}]},
         ACCEPTED, ACCEPTED,
         'width, census N1'),
    Case('disk.type.nonsense', CREATE, 'diskspec', 'type',
         {'disk': [{'size': 8, 'type': 'nonsense'}]},
         refused('disk[0].type: Must be one of: disk, cdrom.'), ACCEPTED,
         'F6 row 4, and D50\'s first deliberate narrowing: only cdrom is '
         'special cased, so libvirt\'s floppy and lun were accepted and '
         'behaved as plain disks -- a silently wrong result, not a working '
         'one'),
    Case('disk.type.int', CREATE, 'diskspec', 'type',
         {'disk': [{'size': 8, 'type': 5}]},
         refused('disk[0].type: Not a valid string.'), ACCEPTED,
         'F6 row 4, the non-string arm: the type check runs before the enum, '
         'so the message names the type rather than listing the enum'),

    # ---------------------------------------------------------------
    # diskspec: the shape itself
    # ---------------------------------------------------------------
    Case('disk.unknown_key', CREATE, 'diskspec', None,
         {'disk': [{'size': 8, 'wombat': 1}]},
         refused('disk[0].wombat: Unknown field.'), ACCEPTED,
         'F6 row 5 and D41: the original complaint of issue #936, filed in '
         '2020 -- a caller who typed siz instead of size got a default '
         'sized disk and no indication anything was wrong'),
    Case('disk.schema_key', CREATE, 'diskspec', None,
         {'disk': [{'size': 8, '_schema': 1}]},
         refused('disk[0]._schema: Unknown field.'), ACCEPTED,
         'D41 and the flattener: _schema is marshmallow\'s sentinel for an '
         'error about an object rather than about one of its properties, '
         'and it is also a legal JSON key. The finding must name the '
         'caller\'s key here and must *not* name it in '
         'disk.element_not_a_mapping below, which is what the Mapping test in '
         '_flatten_messages discriminates'),
    Case('disk.unknown_key_only', CREATE, 'diskspec', None,
         {'disk': [{'siz': 20}]},
         refused('disk[0].siz: Unknown field.'),
         refused('disk specification must specify at least one of size or '
                 'base'),
         'moves at warn: the typo\'d key leaves the diskspec with neither a '
         'size nor a base, so D45\'s handler guard answers at warn where '
         'this used to be a 507. Deliberate, and the reason this row and '
         'disk.unknown_key are both in the table'),
    Case('disk.empty', CREATE, 'diskspec', None,
         {'disk': [{}]},
         refused('disk specification must specify at least one of size or '
                 'base'),
         refused('disk specification must specify at least one of size or '
                 'base'),
         'F6 row 3 and D45, moves at warn: a diskspec asking for nothing is '
         'refused. Swagger 2.0 has no anyOf, so this is a handler guard and '
         'therefore holds at every mode -- which is the point'),
    Case('disk.size_null_only', CREATE, 'diskspec', None,
         {'disk': [{'size': None}]},
         refused('disk specification must specify at least one of size or '
                 'base'),
         refused('disk specification must specify at least one of size or '
                 'base'),
         'D45, moves at warn: a null size and an absent one are one fact'),
    Case('disk.size_zero_only', CREATE, 'diskspec', None,
         {'disk': [{'size': 0}]},
         refused('disk specification must specify at least one of size or '
                 'base'),
         refused('disk specification must specify at least one of size or '
                 'base'),
         'D45, moves at warn. Note the interaction with disk.size.'
         'zero_with_base above: minimum 0 keeps a zero size *legal*, and '
         'the guard then refuses it only when there is no base to take a '
         'size from, which is the same fact as a sizeless disk with no base'),
    Case('disk.base_none_only', CREATE, 'diskspec', None,
         {'disk': [{'base': 'none'}]},
         refused('disk specification must specify at least one of size or '
                 'base'),
         refused('disk specification must specify at least one of size or '
                 'base'),
         'D45, moves at warn: usage.md documents the literal string "none" '
         'as "no base", and _diskspec_value_absent reads it through '
         'util_general.noneish, so this diskspec asks for nothing in exactly '
         'the way an empty one does. Swept because a reader of D45 would not '
         'guess that a spec with a key in it is refused'),
    Case('disk.size_and_base_null', CREATE, 'diskspec', None,
         {'disk': [{'size': None, 'base': None}]},
         refused('disk specification must specify at least one of size or '
                 'base'),
         refused('disk specification must specify at least one of size or '
                 'base'),
         'D45, moves at warn: both keys present, both effectively absent'),
    Case('disk.bus_and_type_null', CREATE, 'diskspec', None,
         {'disk': [{'size': 8, 'bus': None, 'type': None}]},
         ACCEPTED, ACCEPTED,
         'width, census N1: an enum and a null are not in conflict, because '
         'marshmallow runs no validator on a null'),
    Case('disk.second_element', CREATE, 'diskspec', None,
         {'disk': [{'size': 8}, {'size': 'banana'}]},
         refused('disk[1].size: Not a valid integer.'), FAULTED,
         'D48 and definition-of-done item 6: a finding inside an array names '
         'its index, or a diskspec list of six is undebuggable'),
    Case('disk.element_not_a_mapping', CREATE, 'diskspec', None,
         {'disk': ['wombat']},
         refused('disk[0]: Not a valid mapping type.'),
         refused('disk specification should contain JSON objects'),
         'regression guard, unchanged from before the phase: wrapping a spec '
         'in fields.Nested briefly leaked marshmallow\'s internal _schema '
         'sentinel here, fixed in step 4 by collapsing that key onto the '
         'container\'s own name'),
    Case('disk.not_a_list', CREATE, 'diskspec', None,
         {'disk': {'size': 8}},
         refused('disk: Not a valid list.'),
         refused('disk specification should contain JSON objects'),
         'F1: the outer shape was already checked with a good message, and '
         'this phase does not change it'),

    # ---------------------------------------------------------------
    # networkspec on instance create: network_uuid
    # ---------------------------------------------------------------
    Case('net.uuid.int', CREATE, 'networkspec', 'network_uuid',
         {'network': [{'network_uuid': 5}]},
         refused('network[0].network_uuid: Not a valid string.'),
         refused('network 5 not found', status=404),
         "F5 row 3, moves at warn: this was AttributeError: 'int' object "
         "has no attribute 'replace' in util_general.valid_uuid4 (item 3 "
         'of issue #4167). The issue #4223 fix made from_db_by_ref answer '
         '"not found" for any non-string ref, and that guard is below the '
         'validation layer, so a rollback keeps it'),
    Case('net.uuid.dict', CREATE, 'networkspec', 'network_uuid',
         {'network': [{'network_uuid': {'a': 1}}]},
         refused('network[0].network_uuid: Not a valid string.'),
         refused("network {'a': 1} not found", status=404),
         'F5 row 3, the dict arm, moves at warn (issue #4223)'),
    Case('net.uuid.bool', CREATE, 'networkspec', 'network_uuid',
         {'network': [{'network_uuid': True}]},
         refused('network[0].network_uuid: Not a valid string.'),
         refused('network True not found', status=404),
         'F5 row 3, the bool arm, moves at warn (issue #4223)'),
    Case('net.uuid.null', CREATE, 'networkspec', 'network_uuid',
         {'network': [{'network_uuid': None}]},
         refused('network[0].network_uuid: Missing data for required '
                 'field.'),
         refused('network specification is missing network_uuid'),
         'F7 and D44, moves at warn: before the phase this was a 507, having '
         'resolved an arbitrary network in the namespace. Issue #4223 stays '
         'open -- the lookup is still wrong for the next caller who reaches '
         'it another way -- so step 4 added a value test to the handler '
         'guard, which is what answers at warn'),
    Case('net.uuid.missing', CREATE, 'networkspec', 'network_uuid',
         {'network': [{}]},
         refused('network[0].network_uuid: Missing data for required '
                 'field.'),
         refused('network specification is missing network_uuid'),
         'D44: an omitted network_uuid and a null one are one fact from the '
         'handler\'s side, so a caller gets one answer for them'),
    Case('net.uuid.valid', CREATE, 'networkspec', 'network_uuid',
         {'network': [{'network_uuid': NETWORK}]},
         ACCEPTED, ACCEPTED,
         'width: the ordinary case'),
    Case('net.uuid.by_name', CREATE, 'networkspec', 'network_uuid',
         {'network': [{'network_uuid': 'sweepfixturenet'}]},
         ACCEPTED, ACCEPTED,
         'width, D44: no uuid format, despite the name -- usage.md documents '
         'naming a network and cluster_ci_tests/test_networking.py sends '
         "'barry_net'"),

    # ---------------------------------------------------------------
    # networkspec on instance create: address, model, macaddress, float
    # ---------------------------------------------------------------
    Case('net.address.int', CREATE, 'networkspec', 'address',
         {'network': [{'network_uuid': NETWORK, 'address': 5}]},
         refused('network[0].address: Not a valid string.'), FAULTED,
         'F5 row 4: AttributeError in util_general.noneish at '
         'external_api/instance.py:369, above is_in_range -- the '
         'attribution the census corrected'),
    Case('net.address.list', CREATE, 'networkspec', 'address',
         {'network': [{'network_uuid': NETWORK, 'address': ['10.9.8.5']}]},
         refused('network[0].address: Not a valid string.'), FAULTED,
         'F5 row 4, the list arm'),
    Case('net.address.literal_none', CREATE, 'networkspec', 'address',
         {'network': [{'network_uuid': NETWORK, 'address': 'none'}]},
         ACCEPTED, ACCEPTED,
         'width: the literal string "none" is documented at usage.md:281 and '
         'means "this interface has no address", which is why address is not '
         'typed with the ipv4 format'),
    Case('net.address.null', CREATE, 'networkspec', 'address',
         {'network': [{'network_uuid': NETWORK, 'address': None}]},
         ACCEPTED, ACCEPTED,
         'width, census N1: guest_ci_tests/test_cloudinit.py sends an '
         'explicit null address'),
    Case('net.address.in_range', CREATE, 'networkspec', 'address',
         {'network': [{'network_uuid': NETWORK, 'address': '10.9.8.55'}]},
         ACCEPTED, ACCEPTED,
         'width: an ordinary static address'),
    Case('net.address.out_of_range', CREATE, 'networkspec', 'address',
         {'network': [{'network_uuid': NETWORK, 'address': '192.168.1.1'}]},
         refused('network specification requests an address outside the '
                 'range of the network'),
         refused('network specification requests an address outside the '
                 'range of the network'),
         'D42: a guard a schema cannot express, because whether an address '
         'is in range is a property of the network rather than of the '
         'request. Unchanged at every mode'),
    Case('net.model.int', CREATE, 'networkspec', 'model',
         {'network': [{'network_uuid': NETWORK, 'model': 5}]},
         refused('network[0].model: Not a valid string.'), FAULTED,
         'F5 row 5: pydantic, building the NetworkInterface'),
    Case('net.model.nonsense', CREATE, 'networkspec', 'model',
         {'network': [{'network_uuid': NETWORK, 'model': 'nonsense'}]},
         ACCEPTED, ACCEPTED,
         'width, D43 and the key that decision exists for: the value is '
         'rendered into libvirt.tmpl:139, so the vocabulary is the '
         "hypervisor's qemu build. Our own two documentation pages disagree "
         'about it, which is proof neither is a specification'),
    Case('net.model.virtio', CREATE, 'networkspec', 'model',
         {'network': [{'network_uuid': NETWORK, 'model': 'virtio'}]},
         ACCEPTED, ACCEPTED,
         'width: what the CLI and the ansible collection send'),
    Case('net.model.injection', CREATE, 'networkspec', 'model',
         {'network': [{'network_uuid': NETWORK, 'model': "virtio'/><x"}]},
         refused('network[0].model: String does not match expected '
                 'pattern.'),
         ACCEPTED,
         'issue #4242: a model which could close the attribute it is '
         'quoted into at libvirt.tmpl:139 is refused by '
         'DEVICE_MODEL_PATTERN. Accepted at warn deliberately, because a '
         'schema pattern rolls back with the mode; what holds there is '
         'the render-time escaping in instance._xml_attribute_escape(), '
         'which this table cannot see and '
         'test_instance.InstanceDomainXMLEscapingTestCase pins'),
    Case('net.macaddress.malformed', CREATE, 'networkspec', 'macaddress',
         {'network': [{'network_uuid': NETWORK, 'macaddress': 'banana'}]},
         refused('network[0].macaddress: String does not match expected '
                 'pattern.'),
         refused('network specification requests a malformed MAC address'),
         'D42: the pattern is the one util_network already enforces (PR '
         '#4183), so the published contract and the guard cannot drift'),
    Case('net.macaddress.int', CREATE, 'networkspec', 'macaddress',
         {'network': [{'network_uuid': NETWORK, 'macaddress': 5}]},
         refused('network[0].macaddress: Not a valid string.'),
         refused('network specification requests a malformed MAC address'),
         'not an F5 row: a non-string MAC was already guarded, because '
         'valid_macaddr does a fullmatch rather than an attribute access'),
    Case('net.macaddress.valid', CREATE, 'networkspec', 'macaddress',
         {'network': [{'network_uuid': NETWORK,
                       'macaddress': '02:00:00:55:66:77'}]},
         ACCEPTED, ACCEPTED,
         'width: what the CI suite sends'),
    Case('net.macaddress.null', CREATE, 'networkspec', 'macaddress',
         {'network': [{'network_uuid': NETWORK, 'macaddress': None}]},
         ACCEPTED, ACCEPTED,
         'width, census N1: the shipped CLI sends a null macaddress on every '
         'single call'),
    Case('net.float.yes', CREATE, 'networkspec', 'float',
         {'network': [{'network_uuid': NETWORK, 'float': 'yes'}]},
         ACCEPTED, ACCEPTED,
         'width: "yes" is in marshmallow\'s truthy set, so the schema calls '
         'it a boolean meaning True, and the handler agrees. '
         'Definition-of-done item 5 is wrong about this row'),
    Case('net.float.false', CREATE, 'networkspec', 'float',
         {'network': [{'network_uuid': NETWORK, 'float': False}]},
         ACCEPTED, ACCEPTED,
         'width: what the ansible collection sends on every network'),
    Case('net.float.false_string', CREATE, 'networkspec', 'float',
         {'network': [{'network_uuid': NETWORK, 'float': 'false'}]},
         ACCEPTED, ACCEPTED,
         'width: "false" is in marshmallow\'s falsy set, so the schema calls '
         'it a boolean meaning False. The handler used to read the raw '
         'string truthily and float the interface anyway -- the status code '
         'cannot see that, so what the value *means* is pinned by '
         'test_a_falsy_float_spelling_does_not_float below'),
    Case('net.float.true_string', CREATE, 'networkspec', 'float',
         {'network': [{'network_uuid': NETWORK, 'float': 'true'}]},
         ACCEPTED, ACCEPTED,
         'width: the spelling where a bare truthiness test and the schema '
         'happened to agree all along'),
    Case('net.float.int', CREATE, 'networkspec', 'float',
         {'network': [{'network_uuid': NETWORK, 'float': 5}]},
         refused('network[0].float: Not a valid boolean.'), ACCEPTED,
         'F6 row 7: a documented boolean accepted anything truthy, so 5 '
         'floated the instance'),
    Case('net.unknown_key', CREATE, 'networkspec', None,
         {'network': [{'network_uuid': NETWORK, 'wombat': 1}]},
         refused('network[0].wombat: Unknown field.'), ACCEPTED,
         'F6 row 8 and D41: the census found no first-party caller sending '
         'an undocumented netdesc key -- iface_uuid is written by the '
         'handler after every caller check and never appears in a response '
         'body a caller could echo back'),
    Case('net.element_not_a_mapping', CREATE, 'networkspec', None,
         {'network': ['wombat']},
         refused('network[0]: Not a valid mapping type.'),
         refused('network specification should contain JSON objects'),
         'regression guard for the _schema sentinel, the array form'),
    Case('net.not_a_list', CREATE, 'networkspec', None,
         {'network': {'network_uuid': NETWORK}},
         refused('network: Not a valid list.'),
         refused('network specification should contain JSON objects'),
         'F1: unchanged by this phase'),

    # ---------------------------------------------------------------
    # networkspec on interface hotplug. The same shape, declared as a
    # single object rather than an array, and with no scheduler in
    # front of it -- so every accepted row here is a created object
    # rather than a status code, which is the sharper regression guard.
    # ---------------------------------------------------------------
    Case('hotplug.uuid.int', HOTPLUG, 'networkspec', 'network_uuid',
         {'network': {'network_uuid': 5}},
         refused('network.network_uuid: Not a valid string.'),
         refused('network 5 not found', status=404),
         'F5 row 3 on the hotplug route: same guard, same construction, same '
         'answer, and the finding names network.network_uuid rather than '
         'network[0].network_uuid because here the netdesc is the parameter. '
         'Moves at warn (issue #4223): the from_db_by_ref non-string guard '
         'answers "not found" where valid_uuid4 used to fault'),
    Case('hotplug.uuid.dict', HOTPLUG, 'networkspec', 'network_uuid',
         {'network': {'network_uuid': {'a': 1}}},
         refused('network.network_uuid: Not a valid string.'),
         refused("network {'a': 1} not found", status=404),
         'F5 row 3, the dict arm, moves at warn (issue #4223)'),
    Case('hotplug.uuid.bool', HOTPLUG, 'networkspec', 'network_uuid',
         {'network': {'network_uuid': True}},
         refused('network.network_uuid: Not a valid string.'),
         refused('network True not found', status=404),
         'F5 row 3, the bool arm, moves at warn (issue #4223)'),
    Case('hotplug.uuid.null', HOTPLUG, 'networkspec', 'network_uuid',
         {'network': {'network_uuid': None}},
         refused('network.network_uuid: Missing data for required field.'),
         refused('network specification is missing network_uuid'),
         'F7 at its worst, moves at warn: before the phase this answered '
         '200 and created an interface on whichever network the namespace '
         'query happened to return. The runner asserts the interface count, '
         'because a status code is a weak guard for that and an object which '
         'exists afterwards is not'),
    Case('hotplug.uuid.missing', HOTPLUG, 'networkspec', 'network_uuid',
         {'network': {}},
         refused('network.network_uuid: Missing data for required field.'),
         refused('network specification is missing network_uuid'),
         'D44: one fact, one message'),
    Case('hotplug.uuid.valid', HOTPLUG, 'networkspec', 'network_uuid',
         {'network': {'network_uuid': NETWORK}},
         ACCEPTED, ACCEPTED,
         'width: and on this route "accepted" is an interface that exists'),
    Case('hotplug.uuid.by_name', HOTPLUG, 'networkspec', 'network_uuid',
         {'network': {'network_uuid': 'sweepfixturenet'}},
         ACCEPTED, ACCEPTED,
         'width, D44: a network name resolves here too'),
    Case('hotplug.address.int', HOTPLUG, 'networkspec', 'address',
         {'network': {'network_uuid': NETWORK, 'address': 5}},
         refused('network.address: Not a valid string.'), FAULTED,
         'F5 row 4 on the hotplug route'),
    Case('hotplug.address.list', HOTPLUG, 'networkspec', 'address',
         {'network': {'network_uuid': NETWORK, 'address': ['10.9.8.5']}},
         refused('network.address: Not a valid string.'), FAULTED,
         'F5 row 4, the list arm'),
    Case('hotplug.address.literal_none', HOTPLUG, 'networkspec', 'address',
         {'network': {'network_uuid': NETWORK, 'address': 'none'}},
         ACCEPTED, ACCEPTED,
         'width: an interface with no address, thanks OpenStack Kolla'),
    Case('hotplug.macaddress_and_address_null', HOTPLUG, 'networkspec', None,
         {'network': {'network_uuid': NETWORK, 'macaddress': None,
                      'address': None}},
         ACCEPTED, ACCEPTED,
         'width, census N1: exactly what the shipped CLI\'s add-interface '
         'puts on the wire'),
    Case('hotplug.model.int', HOTPLUG, 'networkspec', 'model',
         {'network': {'network_uuid': NETWORK, 'model': 5}},
         refused('network.model: Not a valid string.'), FAULTED,
         'F5 row 5 on the hotplug route'),
    Case('hotplug.model.nonsense', HOTPLUG, 'networkspec', 'model',
         {'network': {'network_uuid': NETWORK, 'model': 'nonsense'}},
         ACCEPTED, ACCEPTED,
         'width, D43: no enum, and the interface is really created'),
    Case('hotplug.model.injection', HOTPLUG, 'networkspec', 'model',
         {'network': {'network_uuid': NETWORK, 'model': "virtio'/><x"}},
         refused('network.model: String does not match expected pattern.'),
         ACCEPTED,
         'issue #4242 on the hotplug route, where the same value reaches '
         'hot_plug_interface() and its double-quoted attribute -- which '
         'is why _xml_attribute_escape() escapes both quote styles. At '
         'warn the interface is really created, so the escaping is the '
         'only thing between this value and the domain'),
    Case('hotplug.macaddress.malformed', HOTPLUG, 'networkspec', 'macaddress',
         {'network': {'network_uuid': NETWORK, 'macaddress': 'banana'}},
         refused('network.macaddress: String does not match expected '
                 'pattern.'),
         refused('network specification requests a malformed MAC address'),
         'D42 on the hotplug route'),
    Case('hotplug.macaddress.valid', HOTPLUG, 'networkspec', 'macaddress',
         {'network': {'network_uuid': NETWORK,
                      'macaddress': '02:00:00:55:66:78'}},
         ACCEPTED, ACCEPTED,
         'width: smoke_ci_tests/test_agentops.py hotplugs with a real MAC'),
    Case('hotplug.float.false', HOTPLUG, 'networkspec', 'float',
         {'network': {'network_uuid': NETWORK, 'float': False}},
         ACCEPTED, ACCEPTED,
         'width. Deliberately the falsy value: a truthy float on this route '
         'reaches assign_floating_ip, which is a property of the floating '
         'network fixture rather than of the schema this file is about'),
    Case('hotplug.float.int', HOTPLUG, 'networkspec', 'float',
         {'network': {'network_uuid': NETWORK, 'float': 5}},
         refused('network.float: Not a valid boolean.'), ACCEPTED,
         'F6 row 7 on the hotplug route -- and at warn it is a *created '
         'interface*, which is what an accepted-in-silence row costs when '
         'there is no scheduler to refuse it afterwards'),
    Case('hotplug.unknown_key', HOTPLUG, 'networkspec', None,
         {'network': {'network_uuid': NETWORK, 'wombat': 1}},
         refused('network.wombat: Unknown field.'), ACCEPTED,
         'F6 row 8 on the hotplug route, and the same point: at warn the '
         'typo is discarded and an interface is created anyway'),
    Case('hotplug.not_a_mapping', HOTPLUG, 'networkspec', None,
         {'network': 'wombat'},
         refused('network: Not a valid mapping type.'),
         refused('network specification should contain JSON objects'),
         'regression guard for the _schema sentinel, the single-object form '
         '-- where the sentinel would have read network._schema for a '
         'parameter that has no such field'),

    # ---------------------------------------------------------------
    # videospec. The starkest of F6's rows: instance.py:833 checked
    # that model and memory were *present* and nothing else, so
    # {"model": 5, "memory": "lots", "vdi": 7} was stored verbatim and
    # surfaced much later and somewhere else -- an AttributeError in a
    # console request rather than in the create which accepted it.
    # ---------------------------------------------------------------
    Case('video.vdi.nonsense', CREATE, 'videospec', 'vdi',
         {'video': {'model': 'cirrus', 'memory': 16384, 'vdi': 'nonsense'}},
         refused('video.vdi: Must be one of: vnc, spice, spiceconcurrent, '
                 'spicedebug.'),
         ACCEPTED,
         'F6 row 9 and D43: the one key in this vocabulary where publishing '
         'the enum genuinely is the enforcement, because nothing else '
         'refuses a value outside it'),
    Case('video.vdi.int', CREATE, 'videospec', 'vdi',
         {'video': {'model': 'cirrus', 'memory': 16384, 'vdi': 7}},
         refused('video.vdi: Not a valid string.'), ACCEPTED,
         'F6 row 9: instance.py:1715 does vdi.startswith("spice"), which is '
         'an AttributeError on a non-string, in the console endpoint'),
    Case('video.vdi.valid', CREATE, 'videospec', 'vdi',
         {'video': {'model': 'cirrus', 'memory': 16384,
                    'vdi': 'spiceconcurrent'}},
         ACCEPTED, ACCEPTED,
         'width: cluster_ci_tests/test_vdi_console_file.py sends this'),
    Case('video.vdi.null', CREATE, 'videospec', 'vdi',
         {'video': {'model': 'cirrus', 'memory': 16384, 'vdi': None}},
         ACCEPTED, ACCEPTED,
         'width: an enum and a null are not in conflict, and an absent vdi '
         'is defaulted rather than refused -- so a null one is too. The '
         'status code cannot see the difference between storing the null '
         'and defaulting it, which is what '
         'test_a_null_video_key_is_never_stored below is for'),
    Case('video.memory.numeric_string', CREATE, 'videospec', 'memory',
         {'video': {'model': 'cirrus', 'memory': '65536'}},
         ACCEPTED, ACCEPTED,
         'width, D46 and D49: consoles.md:95 documents --videospec '
         'memory=65536 and the CLI parser does not coerce, so that '
         'documented command puts the string on the wire. Refusing it would '
         'break the documentation. Definition-of-done item 5 is wrong about '
         'this row'),
    Case('video.memory.int', CREATE, 'videospec', 'memory',
         {'video': {'model': 'cirrus', 'memory': 65536}},
         ACCEPTED, ACCEPTED,
         'width: the CLI default is the integer 16384'),
    Case('video.memory.badstring', CREATE, 'videospec', 'memory',
         {'video': {'model': 'cirrus', 'memory': 'lots'}},
         refused('video.memory: Not a valid integer.'), ACCEPTED,
         'F6 row 9 and D49: typed at last'),
    Case('video.memory.fractional', CREATE, 'videospec', 'memory',
         {'video': {'model': 'cirrus', 'memory': 8.5}},
         refused('video.memory: Not a valid integer.'), ACCEPTED,
         'D46 at depth: this is the nested integer F8 warned would inherit '
         'the lie the moment the phase declared one'),
    Case('video.model.int', CREATE, 'videospec', 'model',
         {'video': {'model': 5, 'memory': 16384}},
         refused('video.model: Not a valid string.'), ACCEPTED,
         'F6 row 9: a non-string model was stored verbatim and rendered raw '
         'into the domain XML'),
    Case('video.model.qxl', CREATE, 'videospec', 'model',
         {'video': {'model': 'qxl', 'memory': 16384}},
         ACCEPTED, ACCEPTED,
         'width: the model to pair with SPICE'),
    Case('video.model.nonsense', CREATE, 'videospec', 'model',
         {'video': {'model': 'nonsense', 'memory': 16384}},
         ACCEPTED, ACCEPTED,
         'width, D43: rendered at libvirt.tmpl:206, so the vocabulary is '
         "the hypervisor's"),
    Case('video.model.injection', CREATE, 'videospec', 'model',
         {'video': {'model': "cirrus'/><x", 'memory': 16384}},
         refused('video.model: String does not match expected pattern.'),
         ACCEPTED,
         'issue #4242: the same pattern as the netdesc model, for the '
         'same attribute-closing value, this time at libvirt.tmpl:206. '
         'Accepted at warn, where the render-time escaping is the guard'),
    Case('video.model.null', CREATE, 'videospec', 'model',
         {'video': {'model': None, 'memory': 16384}},
         refused('video specification requires "model"'),
         refused('video specification requires "model"'),
         'F6 row 9, moves at warn: the handler tested presence, so a null '
         'model passed its guard, was stored verbatim and was rendered raw '
         'into the domain XML at libvirt.tmpl:206 as type=\'None\'. A value '
         'test refuses it, and being a handler guard it refuses it at every '
         'mode -- a rollback must not hand back an instance which cannot '
         'start'),
    Case('video.memory.null', CREATE, 'videospec', 'memory',
         {'video': {'model': 'cirrus', 'memory': None}},
         refused('video specification requires "memory"'),
         refused('video specification requires "memory"'),
         'F6 row 9, moves at warn: the same fact about memory, which '
         'rendered as vram=\'None\''),
    Case('video.unknown_key', CREATE, 'videospec', None,
         {'video': {'model': 'vga', 'memory': 16384, 'wombat': 1}},
         refused('video.wombat: Unknown field.'), ACCEPTED,
         'F6 row 9 and D41: an unknown video key was stored on the instance, '
         'echoed back by external_view, and acted on nowhere'),
    Case('video.not_a_mapping', CREATE, 'videospec', None,
         {'video': [{'model': 'vga'}]},
         refused('video: Not a valid mapping type.'),
         refused('video specification should be a JSON object'),
         'regression guard for the _schema sentinel on a top-level object '
         'parameter. The warn message changed in review: the presence tests '
         'answered 400 here by accident, because "model" not in ["vga"] is a '
         'membership test which happens to be True, and the value tests '
         'which replaced them needed a real shape guard in front. Still a '
         '400 with no exception recorded, which is what D42 asks of warn'),
    Case('video.null', CREATE, 'videospec', None,
         {'video': None},
         ACCEPTED, ACCEPTED,
         'width, census: apiclient.create_instance always sends the video '
         'key, so a caller who passes no videospec sends an explicit null '
         'and the handler supplies the whole default'),
    Case('video.empty', CREATE, 'videospec', None,
         {'video': {}},
         ACCEPTED, ACCEPTED,
         'width: an empty dict is falsy, so instance.py:884 supplies the '
         'whole default before its presence checks run'),
    Case('video.missing_memory', CREATE, 'videospec', None,
         {'video': {'model': 'vga'}},
         refused('video specification requires "memory"'),
         refused('video specification requires "memory"'),
         'D42: the videospec declares nothing required, because the handler '
         'guard says it better -- and the guard therefore answers at every '
         'mode, including enforce'),
]


class _NestedSweepMixin:
    """The runner. Carries no fixtures and no mode of its own.

    Split from the three concrete classes below for the reason
    SweepFixtureTestCase exists at all: the mode is the load-bearing
    thing about each of them, and a class which held both the mode and
    the assertions could not be reused at a different one.
    """

    #: Which column of the table this class asserts against.
    expectation = 'enforce'

    def _expected(self, case):
        answer = getattr(case, self.expectation)
        if answer is not ACCEPTED:
            return answer
        return (ACCEPTED_ON_CREATE if case.route == CREATE
                else ACCEPTED_ON_HOTPLUG)

    def _resolve(self, value):
        """Substitute the fixture network's uuid into a row's body."""
        if isinstance(value, str):
            return value.replace(NETWORK, str(self.network.uuid))
        if isinstance(value, dict):
            return {k: self._resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def _send(self, case, sequence):
        """Drive one row, and say what came back.

        Returns ``(answer, interfaces_created)``. The second half is
        what makes a hotplug row evidence rather than a status code:
        finding F7's symptom was an interface on an arbitrary network,
        which a 200 alone cannot distinguish from a correct one, and
        which a refusal that quietly created one anyway would hide.
        """
        overlay = self._resolve(case.body)
        if case.route == CREATE:
            # A distinct name per request: every row sends a create and
            # a repeated name answers 409, which would measure the
            # collision rather than the value under test.
            body = {'name': 'nested%d' % sequence, 'cpus': 1, 'memory': 1024,
                    'disk': [{'size': 8}]}
            body.update(overlay)
            url = '/instances'
        else:
            body = dict(overlay)
            url = '/instances/%s/interfaces' % self.instance.uuid

        before_interfaces = len(self.instance.interfaces)
        before_records = self.mock_record_exception.call_count
        response = self.client.post(
            url, headers={'Authorization': self.token},
            content_type='application/json', data=json.dumps(body))
        recorded = self.mock_record_exception.call_count > before_records
        created = len(self.instance.interfaces) - before_interfaces

        payload = response.get_json()
        error = payload.get('error') if isinstance(payload, dict) else None
        return Answer(response.status_code, recorded, error), created

    def test_the_controls_still_work(self):
        """Nothing below this line means anything if these two do not.

        A request which 404s because a fixture rotted looks exactly
        like a handler refusing a value. The required sweep learned
        that and asserts its controls on every run; so does this.
        """
        for case in CASES:
            if not case.case_id.startswith('control.'):
                continue
            answer, created = self._send(case, 0)
            self.assertEqual(
                self._expected(case), answer,
                '%s: the control request answered %r -- the fixture is '
                'wrong, so no verdict in this file can be trusted'
                % (case.case_id, (answer,)))
            if case.route == HOTPLUG:
                self.assertEqual(
                    1, created,
                    'the hotplug control created no interface, so an '
                    'accepted row here proves nothing')

    def test_the_sweep(self):
        """The measurement, and the whole of this step's evidence.

        One test rather than a hundred, because the fixture is
        expensive and because the failure worth reading is the whole
        diff against the table rather than the first row which moved.
        """
        mismatches = []
        for sequence, case in enumerate(CASES):
            expected = self._expected(case)
            answer, created = self._send(case, sequence + 1)
            if answer != expected:
                mismatches.append(
                    '%s [%s]: answered %r, pinned as %r'
                    % (case.case_id, self.expectation, tuple(answer),
                       tuple(expected)))
                continue

            # An accepted hotplug row must have created exactly one
            # interface and a refused one exactly none. Derived from
            # the answer rather than declared per row, so a row cannot
            # be added which pins a refusal and forgets to say that
            # nothing was created.
            if case.route == HOTPLUG:
                wanted = 1 if expected == ACCEPTED_ON_HOTPLUG else 0
                if created != wanted:
                    mismatches.append(
                        '%s [%s]: answered %s but created %d interfaces, '
                        'not %d'
                        % (case.case_id, self.expectation, answer.status,
                           created, wanted))

        self.assertEqual(
            [], mismatches,
            'the nested sweep no longer agrees with the table in '
            'shakenfist/tests/external_api/test_nested_sweep.py, published '
            'as findings F5 and F6 of '
            'docs/plans/PLAN-api-input-validation-phase-07-structured.md:\n'
            + '\n'.join(mismatches))

    def test_a_null_video_key_is_never_stored(self):
        """What a status code cannot see about the videospec.

        A null ``vdi`` is accepted, as it was before, so ``video.vdi.
        null`` answers the same 507 either way. The difference is what
        gets stored: the handler used to test *presence*, so the null
        went onto the instance and surfaced a long way away -- an
        AttributeError in ``self.video['vdi'].startswith('spice')``
        during a console request, and ``type='None'`` rendered into
        the domain XML. The guard is a value test now, so an explicit
        null is defaulted exactly as an absent key is.

        Asserted at ``Instance.new`` rather than by reading the object
        back, because the create this fixture can reach ends at a 507
        and enqueues the instance for deletion; the argument the
        handler passed is the fact under test and is the one thing
        that survives either way.
        """
        with mock.patch.object(Instance, 'new', wraps=Instance.new) as new:
            response = self.client.post(
                '/instances', headers={'Authorization': self.token},
                content_type='application/json',
                data=json.dumps({
                    'name': 'videonull', 'cpus': 1, 'memory': 1024,
                    'disk': [{'size': 8}],
                    'video': {'model': 'vga', 'memory': 16384, 'vdi': None}}))

        self.assertEqual(
            507, response.status_code,
            'the request did not reach placement, so it never reached '
            'Instance.new either and this test proves nothing')
        self.assertEqual(
            {'model': 'vga', 'memory': 16384, 'vdi': 'spice'},
            new.call_args.kwargs['video'])

    def test_a_falsy_float_spelling_does_not_float(self):
        """What a status code cannot see about ``float``.

        marshmallow reads ``'false'`` as the boolean False, so the
        published schema calls it a valid boolean meaning "do not
        float". The handler read the raw body truthily -- and a
        non-empty string is truthy -- so it floated the interface,
        which is the schema blessing a value whose meaning the server
        inverts. Both spellings are asserted, because a test which only
        showed the falsy one not floating would pass just as well
        against a handler which had stopped floating anything.
        """
        for spelling, floats in (('false', False), ('true', True)):
            before = {str(i.uuid) for i in self.instance.interfaces}
            response = self.client.post(
                '/instances/%s/interfaces' % self.instance.uuid,
                headers={'Authorization': self.token},
                content_type='application/json',
                data=json.dumps({'network': {
                    'network_uuid': str(self.network.uuid),
                    'float': spelling}}))
            self.assertEqual(200, response.status_code,
                             'float=%r was not accepted' % spelling)

            created = [i for i in self.instance.interfaces
                       if str(i.uuid) not in before]
            self.assertEqual(1, len(created),
                             'float=%r created %d interfaces'
                             % (spelling, len(created)))
            address = created[0].floating.get('floating_address')
            if floats:
                self.assertIsNotNone(
                    address, 'float=%r did not float the interface, so the '
                    'falsy half of this test proves nothing' % spelling)
            else:
                self.assertIsNone(
                    address, 'float=%r floated the interface, which is the '
                    'opposite of what the published schema says it means'
                    % spelling)

    def test_every_key_has_an_accepted_value(self):
        """The schemas are shown not to be too narrow.

        A sweep which only proves things are refused would pass just
        as well against a schema which refused everything, so every
        (spec, key) which appears in the table carries at least one row
        which is still accepted at enforce. Derived from the table
        rather than asserted as a count, because the error mode is a
        key whose accepted row was deleted along with the schema change
        that made it fail.
        """
        keyed = {(c.spec, c.key) for c in CASES if c.key}
        accepted = {(c.spec, c.key) for c in CASES
                    if c.key and c.enforce is ACCEPTED}
        self.assertEqual(
            set(), keyed - accepted,
            'these keys are only ever refused in the table, so nothing here '
            'would notice a schema which refused every value of them')

    def test_both_routes_carry_the_networkspec(self):
        """A shape declared twice is swept twice.

        networkspec is the one shape with two declarations, and the
        hotplug one is where an accepted value costs a created object
        rather than a 507. A table which swept only instance create
        would be blind to exactly the failure F7 was.
        """
        routes = {c.route for c in CASES if c.spec == 'networkspec'}
        self.assertEqual({CREATE, HOTPLUG}, routes)
        for route in (CREATE, HOTPLUG):
            keys = {c.key for c in CASES
                    if c.spec == 'networkspec' and c.route == route and c.key}
            self.assertEqual(
                {'network_uuid', 'address', 'model', 'macaddress', 'float'},
                keys,
                'the %s route does not sweep every netdesc key' % route)


class NestedSweepEnforceTestCase(_NestedSweepMixin, SweepFixtureTestCase):
    """What a caller sees today, at the shipped default.

    Every row of findings F5 and F6, at its new answer.
    """

    mode = 'enforce'
    expectation = 'enforce'


class NestedSweepWarnTestCase(_NestedSweepMixin, SweepFixtureTestCase):
    """The operator's rollback, and decision D42's proof.

    An operator who sets ``API_VALIDATION_MODE=warn`` because this
    phase broke their fleet must get back the behaviour they had before
    it -- finding F5's recorded 500s included, which are ugly but are
    what they had. ``check()`` still runs and still logs every finding,
    so the observability is unchanged; it simply does not act on one.

    Ten rows deliberately do not roll back, and this class asserts
    that they do not. They are the three handler guards this phase
    added: a null ``network_uuid`` on either route (F7, which used to
    be a 200 and an interface on an arbitrary network), the six shapes
    of diskspec which ask for neither a size nor a base, and a
    ``videospec`` whose ``model`` or ``memory`` is an explicit null
    (which used to be stored and to reach the hypervisor). A guard is
    not a schema check and does not answer to ``API_VALIDATION_MODE`` --
    which is the whole reason step 4 wrote them as guards rather than
    leaving the schema to do it, since a rollback which handed back the
    bug the phase just closed would not be a rollback.
    """

    mode = 'warn'
    expectation = 'warn'


class NestedSweepOffTestCase(NestedSweepWarnTestCase):
    """The same table again with validation switched off entirely.

    ``warn`` runs ``check()`` and discards the findings; ``off`` does
    not run it at all. Those are different code paths to the same
    promised behaviour, and the promise is worth measuring rather than
    reasoning about: every row of this table answers identically at
    both, the handler guards included.
    """

    mode = 'off'
