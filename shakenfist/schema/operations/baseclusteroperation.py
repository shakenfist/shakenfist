from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel
from pydantic import field_serializer
from pydantic import field_validator
from pydantic import UUID4

from shakenfist.schema.object_types import ObjectType


class PRIORITY(Enum):
    user_waiting = 10
    user_facing = 20
    user_facing_high_io = 25
    background = 30
    background_high_io = 40


# The set of ObjectType values that are valid cluster operations.
VALID_CLUSTER_OPERATIONS: frozenset[ObjectType] = frozenset([
    ObjectType.NODE_BLOB_OP,
    # ... verify_size_and_checksum
    # ... ensure_local

    ObjectType.NODE_INST_OP,
    # ... collect_billing_statistics
    # ... health_check_kvm_process
    # ... instance_delete

    ObjectType.NODE_INST_NETDESC_OP,
    # ... instance_preflight
    # ... instance_start

    ObjectType.ARTIFACT_FETCH_OP,
    # ... image_fetch

    ObjectType.NODE_INST_NET_IFACE_OP,
    # ... hot_plug_instance_interface

    ObjectType.NODE_INST_SNAP_OP,
    # ... instance_snapshot

    ObjectType.IMGCACHE_OP,
    # ... archive_transcode

    ObjectType.NODE_AOP_OP,
    # ... preflight

    ObjectType.NODE_NET_OP,
    # ... network_destroy

    ObjectType.NET_OP,
    # ... network_deploy
    # ... network_destroy
    # ... network_update_dnsmasq
    # ... network_remove_dnsmasq
    # ... network_remove_nat

    ObjectType.NET_MACADDR_IP_OP,
    # ... remove_dhcp_lease

    ObjectType.NET_IP_OP,
    # ... route_address
    # ... unroute_address

    ObjectType.NET_IFACE_OP,
    # ... interface_float

    ObjectType.NET_IFACE_IP_OP,
    # ... interface_defloat
])

# Type alias for static type checking. This uses Literal to constrain the
# allowed ObjectType values at type-check time.
ClusterOperation = Literal[
    ObjectType.ARTIFACT_FETCH_OP,
    ObjectType.IMGCACHE_OP,
    ObjectType.NET_IFACE_OP,
    ObjectType.NET_IFACE_IP_OP,
    ObjectType.NET_IP_OP,
    ObjectType.NET_MACADDR_IP_OP,
    ObjectType.NET_OP,
    ObjectType.NODE_AOP_OP,
    ObjectType.NODE_BLOB_OP,
    ObjectType.NODE_INST_NET_IFACE_OP,
    ObjectType.NODE_INST_NETDESC_OP,
    ObjectType.NODE_INST_OP,
    ObjectType.NODE_INST_SNAP_OP,
    ObjectType.NODE_NET_OP,
]


class dependency(BaseModel):
    op_type: ObjectType
    op_uuid: UUID4

    @field_validator('op_type')
    @classmethod
    def validate_op_type(cls, v: ObjectType) -> ObjectType:
        if v not in VALID_CLUSTER_OPERATIONS:
            raise ValueError(
                f'{v} is not a valid cluster operation. '
                f'Valid operations: '
                f'{sorted(o.value for o in VALID_CLUSTER_OPERATIONS)}'
            )
        return v

    @field_serializer('op_type')
    def serialize_op_type(self, op_type: ObjectType, _info) -> str:
        return op_type.value


def _convert_deps(
        deps: Optional[list[dict[str, Any]]]
) -> Optional[list[dependency]]:
    if deps and len(deps) > 0:
        converted = []
        for op in deps:
            if op:
                converted.append(
                    dependency(
                        # ObjectType inherits from str, so we can look up by value
                        op_type=ObjectType(op['op_type']),  # type: ignore[call-arg]
                        op_uuid=op['op_uuid']
                    )
                )
        return converted
    return None
