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
    # ... verify_size_and_checksum
    # ... ensure_local

    node_inst_op = 2
    # ... collect_billing_statistics
    # ... health_check_kvm_process
    # ... instance_delete

    node_inst_netdesc_op = 3
    # ... instance_preflight
    # ... instance_start

    artifact_fetch_op = 4
    # ... image_fetch

    node_inst_net_iface_op = 5
    # ... hot_plug_instance_interface

    node_inst_snap_op = 6
    # ... instance_snapshot

    imgcache_op = 7
    # ... archive_transcode

    node_aop_op = 8
    # ... preflight

    node_net_op = 9
    # ... network_destroy

    net_op = 10
    # ... network_deploy
    # ... network_destroy
    # ... network_update_dnsmasq
    # ... network_remove_dnsmasq
    # ... network_remove_nat

    net_macaddr_ip_op = 11
    # ... remove_dhcp_lease

    net_ip_op = 12
    # ... route_address
    # ... unroute_address

    net_iface_op = 13
    # ... interface_float

    net_iface_ip_op = 14
    # ... interface_defloat


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
