from uuid import uuid4

from pydantic import ValidationError

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.tests import base


class BaseClusterOperationTestCase(base.ShakenFistTestCase):
    def test_dependency_serialization_uuid4(self):
        dependency(
            op_type=ObjectType.ARTIFACT_FETCH_OP,
            op_uuid=uuid4())

    def test_dependency_serialization_str_uuid(self):
        dependency(
            op_type=ObjectType.ARTIFACT_FETCH_OP,
            op_uuid=str(uuid4()))

    def test_dependency_serialization_bad_op_type(self):
        self.assertRaises(
            ValidationError, dependency, op_type='banana', op_uuid=uuid4())

    def test_dependency_serialization_bad_uuid(self):
        self.assertRaises(
            ValidationError, dependency,
            op_type=ObjectType.ARTIFACT_FETCH_OP, op_uuid='banana')
