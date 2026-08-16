# Pydantic schema for node attributes storage in MariaDB.
#
# This schema defines the structure for storing node mutable attributes.
# Attributes are values that can change during the node's lifetime,
# unlike static values which are immutable after creation.
#
# This is separate from NodeData (static values) per the architecture
# decision to keep mutable and immutable data in separate tables.
# See docs/operator_guide/database.md for the rationale.
#
# This model serves as both:
# 1. The source of truth for the node_attributes table schema
# 2. A typed data transfer object for node attributes

from typing import Annotated, Any, Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NodeAttributesData(BaseModel):
    """Schema for node attributes in MariaDB.

    This model represents the mutable attributes for a node object.
    Unlike NodeData (static values), these can be updated after
    creation.

    Consolidates many separate etcd attributes into a single row:
    - observed (at, release) -> last_seen, installed_version
    - roles -> is_etcd_master, is_hypervisor, etc.
    - daemons -> daemons (JSON list)
    - daemon:{name} -> daemon_states (JSON dict)
    - version tuples -> qemu_version, libvirt_version, etc.
    - process_metrics -> process_metrics (JSON dict)

    Table: node_attributes
    Primary key: uuid (references nodes.uuid)

    Attributes:
        uuid: The node's unique identifier (references nodes.uuid).
        last_seen: Unix timestamp when the node was last observed.
        installed_version: The Shaken Fist release version installed.
        spice_server_cert_subject: The subject of the node's SPICE server
            certificate as a SPICE host-subject string, or None.
        is_etcd_master: Vestigial etcd-era flag, always False.
        is_database_node: Whether this node is part of the database tier.
        is_hypervisor: Whether this node runs instances.
        is_network_node: Whether this node handles networking.
        is_eventlog_node: Whether this node runs eventlog.
        daemons: List of registered daemon names (JSON).
        daemon_states: Legacy per-daemon state info (JSON dict). No
            longer read or written (see node_daemon_states); retained
            for one release cycle as a rollback fallback.
        qemu_version: QEMU version as [major, minor, patch].
        libvirt_version: libvirt version as [major, minor, patch].
        python_version: Python version as [major, minor, patch].
        python_implementation: Python implementation name.
        dependency_versions: Dependency version info (JSON dict).
        process_metrics: Process-level metrics (JSON dict).
    """

    # NOTE: Not frozen - attributes are mutable
    model_config = ConfigDict(frozen=False)

    # Primary key - references nodes.uuid
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # Observation data
    last_seen: Annotated[float, SQLIndex()] = 0.0
    installed_version: Optional[str] = None

    # The subject of this node's SPICE server certificate
    # (/etc/pki/libvirt-spice/server-cert.pem), rendered as the
    # comma-separated OpenSSL short-name key=value string the SPICE
    # host-subject verifier compares against. None when the node has no
    # such certificate (a non-hypervisor, or one whose cert could not be
    # read or expressed in that form).
    spice_server_cert_subject: Optional[str] = None

    # Role flags. is_etcd_master and is_eventlog_node are vestigial (their
    # only writer hardcodes False) and are retained for one release before
    # removal; is_database_node is the live database-tier flag.
    is_etcd_master: bool = False
    is_hypervisor: bool = False
    is_network_node: bool = False
    is_eventlog_node: bool = False
    is_database_node: bool = False

    # Daemon management
    daemons: list[str] = Field(default_factory=list)
    # {daemon_name: {value: str, update_time: float, message: str}}
    daemon_states: dict[str, Any] = Field(default_factory=dict)

    # Software version tuples stored as JSON lists
    qemu_version: Optional[list[int]] = None
    libvirt_version: Optional[list[int]] = None
    python_version: Optional[list[int]] = None
    python_implementation: Optional[str] = None
    dependency_versions: dict[str, Any] = Field(default_factory=dict)

    # Process-level metrics
    process_metrics: dict[str, Any] = Field(default_factory=dict)
