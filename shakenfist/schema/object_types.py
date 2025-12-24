# Pydantic schema for object type validation.
#
# This module defines an enum of all valid object types in Shaken Fist. This
# provides type safety and validation for fields that reference object types,
# such as user_type in IPAM reservations.
#
# This is the single source of truth for object type names.

from enum import Enum


class ObjectType(str, Enum):
    """Enum of all valid object types in Shaken Fist.

    This enum inherits from str so that the value can be used directly as a
    string in SQL queries and JSON serialization. For example:
        ObjectType.INSTANCE.value == 'instance'
        ObjectType.INSTANCE == 'instance'  # Also works due to str inheritance
        str(ObjectType.INSTANCE) == 'instance'  # Works due to __str__ override
        f'{ObjectType.INSTANCE}' == 'instance'  # Works in f-strings too

    The enum values match the object_type class attribute on each
    DatabaseBackedObject subclass.
    """

    def __str__(self) -> str:
        """Return the enum value as a string.

        This override is needed because the default str(Enum) returns
        'EnumName.MEMBER_NAME' rather than the value. Since we want to use
        these values in etcd paths, error messages, and other string contexts,
        we override __str__ to return the value directly.
        """
        return self.value

    # Core objects
    AGENTOPERATION = 'agentoperation'
    ARTIFACT = 'artifact'
    BLOB = 'blob'
    DHCP = 'dhcp'
    INSTANCE = 'instance'
    INTERFACE = 'interface'
    IPAM = 'ipam'
    NAMESPACE = 'namespace'
    NETWORK = 'network'
    NODE = 'node'
    UPLOAD = 'upload'

    # Operation objects
    ARTIFACT_FETCH_OP = 'artifact_fetch_op'
    IMGCACHE_OP = 'imgcache_op'
    NET_IFACE_OP = 'net_iface_op'
    NET_IFACE_IP_OP = 'net_iface_ip_op'
    NET_IP_OP = 'net_ip_op'
    NET_MACADDR_IP_OP = 'net_macaddr_ip_op'
    NET_OP = 'net_op'
    NODE_AOP_OP = 'node_aop_op'
    NODE_BLOB_OP = 'node_blob_op'
    NODE_INST_NET_IFACE_OP = 'node_inst_net_iface_op'
    NODE_INST_NETDESC_OP = 'node_inst_netdesc_op'
    NODE_INST_OP = 'node_inst_op'
    NODE_INST_SNAP_OP = 'node_inst_snap_op'
    NODE_NET_OP = 'node_net_op'

    # Meta object for API request tracing
    API_REQUESTS = 'api-requests'

    # Base/placeholder types (used by base classes before subclass override)
    UNKNOWN = 'unknown'
    UNKNOWN_MANAGED_EXECUTABLE = 'unknown_managed_executable'
