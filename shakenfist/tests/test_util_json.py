# Copyright 2019 Michael Still and contributors
"""Tests for the JSON utility helpers, especially json_sanitise().

json_sanitise() is the source-level fix for issue 3573: event 'extra'
dicts containing raw uuid.UUID values crashed the log shipper's JSON
formatter mid-emit, dumping a raw traceback per event and dropping the
structured record.
"""
import json
import uuid
from enum import Enum

from shakenfist.baseobject import State
from shakenfist.tests import base
from shakenfist.util import json as util_json


class _AnEnum(Enum):
    FIRST = 1


class _Opaque:
    def __str__(self):
        return 'opaque-object'


class JsonSanitiseTestCase(base.ShakenFistTestCase):
    def test_primitives_pass_through(self):
        data = {'a': 1, 'b': 'two', 'c': None, 'd': [1.5, True]}
        self.assertEqual(data, util_json.json_sanitise(data))

    def test_uuid_leaf_is_stringified(self):
        u = uuid.UUID('12345678-1234-5678-1234-567812345678')
        self.assertEqual(
            {'network_uuid': str(u)},
            util_json.json_sanitise({'network_uuid': u}))

    def test_nested_uuid_in_tuple_is_stringified(self):
        # The shape baseoperation.defer() logs: a list of
        # (object_type, uuid) unique_label tuples.
        u = uuid.UUID('12345678-1234-5678-1234-567812345678')
        out = util_json.json_sanitise({'waiting_on': [('instance', u)]})
        self.assertEqual({'waiting_on': [['instance', str(u)]]}, out)
        # The result must be plain-json.dumps safe.
        json.dumps(out)

    def test_state_and_enum_leaves(self):
        s = State(value='created', update_time=123.0)
        out = util_json.json_sanitise({'state': s, 'kind': _AnEnum.FIRST})
        self.assertEqual(s.obj_dict(), out['state'])
        self.assertEqual('FIRST', out['kind'])

    def test_unknown_object_falls_back_to_str(self):
        self.assertEqual(
            {'thing': 'opaque-object'},
            util_json.json_sanitise({'thing': _Opaque()}))

    def test_result_is_a_copy(self):
        data = {'nested': {'key': 'value'}}
        out = util_json.json_sanitise(data)
        out['nested']['key'] = 'changed'
        self.assertEqual('value', data['nested']['key'])
