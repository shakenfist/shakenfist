# Copyright 2019 Michael Still and contributors
"""Regression tests for UUID handling at the MariaDB JSON boundary.

BaseObject stores uuids as uuid.UUID objects (95974afc9), so any JSON
payload handed to the mariadb layer -- sa.JSON column values, and the
*_json string fields built for gRPC protos -- may contain them. The
stdlib json encoder raises TypeError on UUID, which surfaced in
production as sf-queues crashing at startup while caching its
instance list onto node_attributes (StatementError wrapping
'Object of type UUID is not JSON serializable', 2026-07-12). The
mariadb layer must therefore always serialize with _json_dumps.
"""

import json
import pathlib
import re
from unittest import mock
from uuid import uuid4

from shakenfist import mariadb
from shakenfist.tests import base


class UUIDEncoderTestCase(base.ShakenFistTestCase):
    def test_bare_uuid(self):
        u = uuid4()
        self.assertEqual(f'"{u}"', mariadb.json_dumps(u))

    def test_nested_uuids(self):
        u1 = uuid4()
        u2 = uuid4()
        encoded = mariadb.json_dumps({'instances': [u1, u2]})
        self.assertEqual(
            {'instances': [str(u1), str(u2)]}, json.loads(encoded))

    def test_other_unserializable_types_still_raise(self):
        self.assertRaises(TypeError, mariadb.json_dumps, object())


class EngineJSONSerializerTestCase(base.ShakenFistTestCase):
    def test_engine_uses_uuid_aware_serializer(self):
        # sa.JSON column binds are serialized by the engine's
        # json_serializer; the default (plain json.dumps) crashes on
        # UUID payloads.
        if hasattr(mariadb._local, 'engine'):
            del mariadb._local.engine

        with mock.patch.object(
                mariadb.sa, 'create_engine') as mock_create:
            with mock.patch.object(
                    mariadb, '_get_connection_url',
                    return_value='mysql+pymysql://x:y@localhost/z'):
                with mock.patch.object(mariadb, 'sa_event'):
                    mariadb._get_engine()

        self.assertEqual(
            mariadb.json_dumps,
            mock_create.call_args.kwargs.get('json_serializer'))

        if hasattr(mariadb._local, 'engine'):
            del mariadb._local.engine


class NoPlainJSONDumpsTestCase(base.ShakenFistTestCase):
    def test_mariadb_has_no_plain_json_dumps(self):
        # Guard against new call sites regressing to the stdlib
        # encoder. The only legitimate use is inside _json_dumps
        # itself, which passes cls=_UUIDEncoder.
        source = pathlib.Path(mariadb.__file__).read_text()
        plain = [
            line for line in source.splitlines()
            if re.search(r'(?<![_\w])json\.dumps\(', line)
            and '_UUIDEncoder' not in line
        ]
        self.assertEqual(
            [], plain,
            'plain json.dumps in mariadb.py cannot serialize UUID '
            'payloads; use _json_dumps instead')
