# Copyright 2019 Michael Still and contributors
# Pydantic schema for cluster operation target storage in MariaDB.
#
# This schema defines the structure for tracking which cluster operations
# target which objects. Every time an operation is enqueued against an
# object (Instance, Artifact, Network), a row is inserted into the
# cluster_operation_targets table. This replaces the single-pointer
# last_cluster_operation column in object_metadata with a full append-only
# history of operations per target object.
#
# The table uses AUTO_INCREMENT sequence_number for efficient ordering
# and supports queries like:
#   - "most recent operation on this instance" (MAX sequence_number)
#   - "all operations on this instance in order" (ORDER BY sequence_number)
#   - "outstanding operations" (join with object_states)
#   - "prune old completed operations" (DELETE by created_at threshold)

from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class ClusterOperationTargetData(BaseModel):
    """Schema for cluster operation targets in MariaDB.

    Each row records one operation targeting one object. The combination
    of (target_object_type, target_uuid) identifies the target, while
    operation_uuid is the primary key. sequence_number is assigned by
    the database via AUTO_INCREMENT for implicit ordering.

    Table: cluster_operation_targets
    Primary key: operation_uuid

    Attributes:
        operation_uuid: UUID of the cluster operation.
        operation_type: The type of operation (e.g. 'instance_preflight').
        target_object_type: The ObjectType of the target object.
        target_uuid: UUID of the target object.
        sequence_number: Auto-assigned sequence for ordering within a
            target. None on insert (assigned by database).
        created_at: Unix timestamp when the operation was enqueued.
    """

    model_config = ConfigDict(frozen=True)

    operation_uuid: str = Field(max_length=36)
    operation_type: str = Field(max_length=64)
    target_object_type: str
    target_uuid: str = Field(max_length=36)
    sequence_number: Optional[int] = Field(default=None)
    created_at: float

    def external_view(self) -> dict[str, Any]:
        """Serialize for JSON API responses."""
        return {
            'operation_uuid': self.operation_uuid,
            'operation_type': self.operation_type,
            'target_object_type': self.target_object_type,
            'target_uuid': self.target_uuid,
            'sequence_number': self.sequence_number,
            'created_at': self.created_at,
        }
