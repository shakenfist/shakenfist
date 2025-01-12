from enum import Enum

from pydantic import BaseModel
from pydantic import field_serializer
from pydantic import UUID4


class PRIORITY(Enum):
    user_waiting = 10
    user_facing = 20
    user_facing_high_io = 25
    background = 30
    background_high_io = 40


class CLUSTER_OPERATIONS(Enum):
    node_blob_op = 1
    node_inst_op = 2
    node_inst_netdesc_op = 3
    artifact_fetch_op = 4
    node_inst_iface_op = 5
    node_inst_snap_op = 6


class Dependency(BaseModel):
    op_type: CLUSTER_OPERATIONS
    op_uuid: UUID4

    @field_serializer('op_type')
    def serialize_op_type(self, op_type: CLUSTER_OPERATIONS, _info):
        return op_type.name


def _convert_deps(deps):
    if deps and len(deps) > 0:
        converted = []
        for op in deps:
            if op:
                converted.append(
                    Dependency(
                        op_type=CLUSTER_OPERATIONS[op['op_type']],
                        op_uuid=op['op_uuid']
                    )
                )
        return converted
    return None
