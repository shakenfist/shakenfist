from uuid import uuid4

from pydantic import ValidationError

from shakenfist.etcd_schema.operations.baseclusteroperation \
    import CLUSTER_OPERATIONS
from shakenfist.etcd_schema.operations.baseclusteroperation \
    import dependency
from shakenfist.tests import base


class BaseClusterOperationTestCase(base.ShakenFistTestCase):
    def test_dependency_serialization_uuid4(self):
        dependency(
            op_type=CLUSTER_OPERATIONS.artifact_fetch_op,
            op_uuid=uuid4())

    def test_dependency_serialization_str_uuid(self):
        dependency(
            op_type=CLUSTER_OPERATIONS.artifact_fetch_op,
            op_uuid=str(uuid4()))

    def test_dependency_serialization_bad_op_type(self):
        self.assertRaises(
            ValidationError, dependency, op_type='banana', op_uuid=uuid4())

    def test_dependency_serialization_bad_uuid(self):
        self.assertRaises(
            ValidationError, dependency,
            op_type=CLUSTER_OPERATIONS.artifact_fetch_op, op_uuid='banana')
