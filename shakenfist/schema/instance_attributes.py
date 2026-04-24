# Copyright 2019 Michael Still and contributors
# Pydantic schema for Instance mutable attributes in MariaDB.
#
# This schema defines the structure for storing Instance mutable
# attributes. These are values that change during the lifetime of
# an Instance (placement, power state, block devices, etc.).
#
# This is separate from InstanceData (static values) per the
# architecture decision to keep mutable and immutable data in
# separate tables.
# See docs/operator_guide/database.md for the rationale.
#
# This model serves as both:
# 1. The source of truth for the instance_attributes table schema
# 2. A typed data transfer object for Instance attributes
#
# Consolidates separate etcd attributes into a single row:
# - attribute/instance/{uuid}/placement -> placement
# - attribute/instance/{uuid}/power_state -> power_state
# - attribute/instance/{uuid}/ports -> ports
# - attribute/instance/{uuid}/enforced_deletes -> enforced_deletes
# - attribute/instance/{uuid}/block_devices -> block_devices
# - attribute/instance/{uuid}/agent_state -> agent_state
# - attribute/instance/{uuid}/agent_attributes -> agent_attributes
# - attribute/instance/{uuid}/agent_operations -> agent_operations
# - attribute/instance/{uuid}/kvm_pid -> kvm_pid
# - attribute/instance/{uuid}/error -> error_message
#
# NOTE: metadata and last_cluster_operation are stored in the shared
# object_metadata table (phase 14).

from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class InstanceAttributesData(BaseModel):
    """Schema for Instance attributes in MariaDB.

    This model represents the mutable attributes for an Instance
    object. Unlike InstanceData (static values), these can be
    updated after creation.

    Table: instance_attributes
    Primary key: uuid (references instances.uuid)

    Attributes:
        uuid: The Instance UUID (references instances.uuid).
        placement: Dict with node UUID and placement attempts.
        power_state: Dict with current/previous power state and
            update timestamp.
        ports: Dict with console_port, vdi_port, vdi_tls_port,
            or None if not yet allocated.
        enforced_deletes: Dict with count of enforced delete
            attempts.
        block_devices: Dict with devices list, extracommands,
            and finalized flag.
        agent_state: Dict with state value and update_time.
        agent_attributes: Dict with start_time, system_boot_time,
            and facts.
        agent_operations: Dict with queue, queued, running, and
            completed operation UUID lists.
        kvm_pid: KVM process PID, or None if not running.
        error_message: Error message string, or None.
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # Primary key — references instances.uuid
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Placement: {node: <uuid>, placement_attempts: <int>}
    placement: Optional[dict[str, Any]] = None

    # Power state: {power_state, power_state_previous, power_state_updated}
    power_state: Optional[dict[str, Any]] = None

    # Console/VDI ports: {console_port, vdi_port, vdi_tls_port}
    ports: Optional[dict[str, Any]] = None

    # Enforced delete tracking: {count: <int>}
    enforced_deletes: Optional[dict[str, Any]] = None

    # Block devices: {devices: [...], extracommands: [...], finalized: bool}
    block_devices: Optional[dict[str, Any]] = None

    # Agent state: {value: <str>, update_time: <float>}
    agent_state: Optional[dict[str, Any]] = None

    # Agent attributes: {start_time, system_boot_time, facts}
    agent_attributes: Optional[dict[str, Any]] = None

    # Agent operations: {queue, queued, running, completed}
    agent_operations: Optional[dict[str, Any]] = None

    # KVM process PID (None if not running)
    kvm_pid: Optional[int] = None

    # Error message (None if no error)
    error_message: Optional[str] = None

    # Vsock CID mappings: {channel_name: cid_value}
    # e.g. {'sf-agent2': 12345}
    vsock_cids: Optional[dict[str, int]] = None
