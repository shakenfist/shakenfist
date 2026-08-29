# Copyright 2019 Michael Still and contributors
# Pydantic schema for Instance object storage in MariaDB.
#
# This schema defines the structure for storing Instance static values.
# Instances represent virtual machines managed by Shaken Fist, with
# CPU, memory, disk, and network configuration.
#
# This model serves as both:
# 1. The source of truth for the instances table schema (used by
#    pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for Instance static values

from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLLongText
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class InstanceData(BaseModel):
    """Schema for Instance static values in MariaDB.

    This model represents the static (immutable) values for an
    Instance object. It replaces the dict-based static_values
    pattern with a type-safe Pydantic model.

    Table: instances
    Primary key: uuid

    Attributes:
        uuid: The Instance's unique identifier.
        cpus: Number of virtual CPUs.
        disk_spec: List of disk specification dicts (stored as JSON).
        memory: Memory allocation in MB.
        name: Human-readable instance name.
        namespace: The namespace this instance belongs to.
        requested_placement: The node uuid the user requested placement
            on, or None.
        ssh_key: SSH public key to inject, or None.
        user_data: Cloud-init user data string, or None.
        video: Video configuration dict (model, memory, vdi type).
        uefi: Whether UEFI boot is enabled.
        configdrive: Config drive type (e.g., 'openstack-disk').
        nvram_template: Blob UUID for UEFI NVRAM template, or None.
        secure_boot: Whether secure boot is enabled.
        machine_type: QEMU machine type (e.g., 'pc', 'q35').
        side_channels: List of side channel configurations.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The Instance's UUID - primary key, stored as native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Virtual CPU count
    cpus: int

    # Disk specification (variable-length list of dicts, stored as JSON)
    disk_spec: list[dict[str, Any]]

    # Memory in MB
    memory: int

    # Human-readable instance name (indexed for name-lookup queries)
    name: Annotated[str, SQLIndex()]

    # Tenant namespace (indexed for filtered listings)
    namespace: Annotated[str, SQLIndex()]

    # The node uuid the user requested placement on, or None. The API
    # resolves the caller's placed_on to a node uuid string before
    # Instance.new(). Mistyping this as a dict silently discarded every
    # requested placement at the MariaDB write, which let preflight
    # redirect targeted creates to other nodes (issue 3496). LONGTEXT is
    # deliberate: the column was created as LONGTEXT when the field was
    # dict-typed, and _ensure_*_schema() has no ALTER path, so the
    # marker keeps the generated DDL byte-identical.
    requested_placement: Annotated[Optional[str], SQLLongText()] = None

    # SSH public key to inject (nullable, can be large)
    ssh_key: Annotated[Optional[str], SQLLongText()] = None

    # Cloud-init user data (nullable, can be large)
    user_data: Annotated[Optional[str], SQLLongText()] = None

    # Video configuration dict (model, memory, vdi type)
    video: dict[str, Any] = Field(default_factory=dict)

    # UEFI boot enabled
    uefi: bool = False

    # Config drive type
    configdrive: str = 'openstack-disk'

    # UEFI NVRAM template blob UUID (nullable)
    nvram_template: Optional[str] = None

    # Secure boot enabled
    secure_boot: bool = False

    # QEMU machine type
    machine_type: str = 'pc'

    # Side channel configurations (stored as JSON)
    side_channels: list[Any] = Field(default_factory=list)

    # Object version number for schema migrations
    version: int
