# Pydantic schema for object type validation.
#
# This module defines an enum of all valid object types in Shaken Fist. This
# provides type safety and validation for fields that reference object types,
# such as user_type in IPAM reservations.
#
# This is the single source of truth for object type names AND their protobuf
# integer identifiers. The protobuf enum generator reads these values.

from enum import Enum
from typing import NamedTuple
from typing import Optional


class ObjectTypeValue(NamedTuple):
    """Value type for ObjectType enum members.

    Each ObjectType has both a string value (for database/API use) and a
    protobuf ID (for gRPC communication). This NamedTuple makes both values
    explicit and self-documenting.

    Attributes:
        string: The string value used in databases, etcd paths, and APIs.
        proto_id: The integer ID used in protobuf messages. These values are
            stable and must never be changed or reused once assigned.
    """
    string: str
    proto_id: int


# Lookup table for proto_id -> ObjectType (module-level to avoid Enum issues)
_OBJECT_TYPE_PROTO_MAP: dict[int, 'ObjectType'] = {}


class ObjectType(str, Enum):
    """Enum of all valid object types in Shaken Fist.

    This enum inherits from str so that the value can be used directly as a
    string in SQL queries and JSON serialization. For example:
        ObjectType.INSTANCE.value == 'instance'
        ObjectType.INSTANCE == 'instance'  # Also works due to str inheritance
        str(ObjectType.INSTANCE) == 'instance'  # Works due to __str__ override
        f'{ObjectType.INSTANCE}' == 'instance'  # Works in f-strings too

    Each member also has a proto_id attribute containing the protobuf integer
    value. This ensures protobuf enum values remain stable even if the Python
    enum is reordered. Use ObjectType.from_proto_id() to convert from protobuf.

    The enum values match the object_type class attribute on each
    DatabaseBackedObject subclass.
    """

    _proto_id: int

    def __new__(cls, string: str, proto_id: int) -> 'ObjectType':
        """Create an ObjectType with both string and protobuf values.

        Args:
            string: The string value for database/API use.
            proto_id: The stable integer ID for protobuf messages.

        Note: When defining enum members with ObjectTypeValue(...), Python's
        enum machinery unpacks the NamedTuple as positional arguments.
        """
        obj = str.__new__(cls, string)
        obj._value_ = string
        obj._proto_id = proto_id
        return obj

    def __str__(self) -> str:
        """Return the enum value as a string.

        This override is needed because the default str(Enum) returns
        'EnumName.MEMBER_NAME' rather than the value. Since we want to use
        these values in etcd paths, error messages, and other string contexts,
        we override __str__ to return the value directly.

        Note: self.value is the string (e.g., 'instance'), not the
        ObjectTypeValue NamedTuple. The NamedTuple is unpacked by Python's
        enum machinery in __new__, which sets _value_ to just the string.
        """
        return str(self.value)

    @property
    def proto_id(self) -> int:
        """Return the protobuf integer ID for this type."""
        return self._proto_id

    @classmethod
    def from_proto_id(cls, proto_id: int) -> Optional['ObjectType']:
        """Get an ObjectType from its protobuf ID.

        Args:
            proto_id: The protobuf enum integer value.

        Returns:
            The corresponding ObjectType, or None if proto_id is 0 (UNSPECIFIED)
            or unknown.
        """
        global _OBJECT_TYPE_PROTO_MAP
        if proto_id == 0:
            return None
        if not _OBJECT_TYPE_PROTO_MAP:
            _OBJECT_TYPE_PROTO_MAP = {m.proto_id: m for m in cls}
        return _OBJECT_TYPE_PROTO_MAP.get(proto_id)

    # Core objects (proto_id values are stable - never reorder or change!)
    AGENTOPERATION = ObjectTypeValue(string='agentoperation', proto_id=1)
    ARTIFACT = ObjectTypeValue(string='artifact', proto_id=2)
    BLOB = ObjectTypeValue(string='blob', proto_id=3)
    DHCP = ObjectTypeValue(string='dhcp', proto_id=4)
    INSTANCE = ObjectTypeValue(string='instance', proto_id=5)
    INTERFACE = ObjectTypeValue(string='interface', proto_id=6)
    IPAM = ObjectTypeValue(string='ipam', proto_id=7)
    NAMESPACE = ObjectTypeValue(string='namespace', proto_id=8)
    NETWORK = ObjectTypeValue(string='network', proto_id=9)
    NODE = ObjectTypeValue(string='node', proto_id=10)
    UPLOAD = ObjectTypeValue(string='upload', proto_id=11)

    # Operation objects
    ARTIFACT_FETCH_OP = ObjectTypeValue(string='artifact_fetch_op', proto_id=12)
    IMGCACHE_OP = ObjectTypeValue(string='imgcache_op', proto_id=13)
    NET_IFACE_OP = ObjectTypeValue(string='net_iface_op', proto_id=14)
    NET_IFACE_IP_OP = ObjectTypeValue(string='net_iface_ip_op', proto_id=15)
    NET_IP_OP = ObjectTypeValue(string='net_ip_op', proto_id=16)
    NET_MACADDR_IP_OP = ObjectTypeValue(string='net_macaddr_ip_op', proto_id=17)
    NET_OP = ObjectTypeValue(string='net_op', proto_id=18)
    NODE_AOP_OP = ObjectTypeValue(string='node_aop_op', proto_id=19)
    NODE_BLOB_OP = ObjectTypeValue(string='node_blob_op', proto_id=20)
    NODE_INST_NET_IFACE_OP = ObjectTypeValue(
        string='node_inst_net_iface_op', proto_id=21)
    NODE_INST_NETDESC_OP = ObjectTypeValue(
        string='node_inst_netdesc_op', proto_id=22)
    NODE_INST_OP = ObjectTypeValue(string='node_inst_op', proto_id=23)
    NODE_INST_SNAP_OP = ObjectTypeValue(string='node_inst_snap_op', proto_id=24)
    NODE_NET_OP = ObjectTypeValue(string='node_net_op', proto_id=25)

    # Meta object for API request tracing
    API_REQUESTS = ObjectTypeValue(string='api-requests', proto_id=26)

    # Base/placeholder types (used by base classes before subclass override)
    UNKNOWN = ObjectTypeValue(string='unknown', proto_id=27)
    UNKNOWN_MANAGED_EXECUTABLE = ObjectTypeValue(
        string='unknown_managed_executable', proto_id=28)

    # Authentication objects. NAMESPACE_KEY arrived after the core block
    # above was written, so its proto_id continues the sequence rather
    # than sitting alphabetically alongside NAMESPACE.
    NAMESPACE_KEY = ObjectTypeValue(string='namespace_key', proto_id=29)
    TRUSTED_ISSUER = ObjectTypeValue(
        string='trusted_issuer', proto_id=30)
    MAPPING_RULE = ObjectTypeValue(string='mapping_rule', proto_id=31)

    # Scheduler capacity objects. A claim is a namespace's promise of
    # aggregate capacity from the cluster; see
    # docs/plans/PLAN-scheduler-reservations-phase-04-claims-api.md.
    NAMESPACE_CLAIM = ObjectTypeValue(string='namespace_claim', proto_id=32)
