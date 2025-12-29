# Pydantic schema for IPAM reservation storage in MariaDB.
#
# This schema defines the structure for storing IP address reservations.
# Each IPAM (IP Address Manager) can have multiple reservations, one per
# address. The combination of (ipam_uuid, address) forms a unique key.
#
# The reservation_type field uses a dedicated enum (not the object_type enum)
# because reservation types are semantic categories of how an IP is used,
# not object types.

from enum import Enum
from ipaddress import IPv4Address
from typing import Annotated
from typing import Any
from typing import NamedTuple
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4
from shakenfist_utilities import logs

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.sqlalchemy import SQLIndex


LOG, _ = logs.setup(__name__)


# Lookup table for proto_id -> ReservationType (module-level to avoid Enum issues)
_RESERVATION_TYPE_PROTO_MAP: dict[int, 'ReservationType'] = {}


class ReservationTypeValue(NamedTuple):
    """Value type for ReservationType enum members.

    Each ReservationType has both a string value (for database/API use) and a
    protobuf ID (for gRPC communication). This NamedTuple makes both values
    explicit and self-documenting.

    Attributes:
        string: The string value used in databases and APIs.
        proto_id: The integer ID used in protobuf messages. These values are
            stable and must never be changed or reused once assigned.
    """
    string: str
    proto_id: int


class ReservationType(str, Enum):
    """Enum of valid IPAM reservation types.

    These describe how an IP address is being used within a network.

    Each member also has a proto_id attribute containing the protobuf integer
    value. This ensures protobuf enum values remain stable even if the Python
    enum is reordered. Use ReservationType.from_proto_id() to convert from
    protobuf.

    Attributes:
        NETWORK: The network address (e.g., 10.0.0.0 for a /24).
        BROADCAST: The broadcast address (e.g., 10.0.0.255 for a /24).
        GATEWAY: The gateway address for the network.
        FLOATING: A floating IP that can be moved between instances.
        ROUTED: A routed IP address for external connectivity.
        INSTANCE: An IP assigned to an instance interface.
        DELETION_HALO: A recently-released address in the deletion halo.
        UNKNOWN: An unknown or legacy reservation type.
    """

    _proto_id: int

    def __new__(cls, val: ReservationTypeValue) -> 'ReservationType':
        """Create a ReservationType with both string and protobuf values."""
        obj = str.__new__(cls, val.string)
        obj._value_ = val.string
        obj._proto_id = val.proto_id
        return obj

    def __str__(self) -> str:
        """Return the enum value as a string."""
        return str(self.value)

    @property
    def proto_id(self) -> int:
        """Return the protobuf integer ID for this type."""
        return self._proto_id

    @classmethod
    def from_proto_id(cls, proto_id: int) -> Optional['ReservationType']:
        """Get a ReservationType from its protobuf ID.

        Args:
            proto_id: The protobuf enum integer value.

        Returns:
            The corresponding ReservationType, or None if proto_id is 0
            (UNSPECIFIED) or unknown.
        """
        global _RESERVATION_TYPE_PROTO_MAP
        if proto_id == 0:
            return None
        if not _RESERVATION_TYPE_PROTO_MAP:
            _RESERVATION_TYPE_PROTO_MAP = {m.proto_id: m for m in cls}
        return _RESERVATION_TYPE_PROTO_MAP.get(proto_id)

    # Reservation types (proto_id values are stable - never reorder or change!)
    NETWORK = ReservationTypeValue(string='network', proto_id=1)
    BROADCAST = ReservationTypeValue(string='broadcast', proto_id=2)
    GATEWAY = ReservationTypeValue(string='gateway', proto_id=3)
    FLOATING = ReservationTypeValue(string='floating', proto_id=4)
    ROUTED = ReservationTypeValue(string='routed', proto_id=5)
    INSTANCE = ReservationTypeValue(string='instance', proto_id=6)
    DELETION_HALO = ReservationTypeValue(string='deletion-halo', proto_id=7)
    UNKNOWN = ReservationTypeValue(string='unknown', proto_id=8)


class IPAMReservation(BaseModel):
    """Schema for IPAM reservation storage in MariaDB.

    This model represents a single IP address reservation within an IPAM.
    The combination of (ipam_uuid, address) is unique - each address can
    only be reserved once within a given IPAM.

    Attributes:
        ipam_uuid: The UUID of the IPAM this reservation belongs to.
        address: The IPv4 address being reserved.
        reservation_type: How this address is being used.
        user_type: The type of object using this address (if any).
        user_uuid: The UUID of the object using this address (if any).
        reserved_at: Unix timestamp when the reservation was made.
        comment: Optional comment describing the reservation.
    """

    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                # Compound unique index for the primary key equivalent
                ['ipam_uuid', 'address'],
                # Index for querying by user
                ['user_type', 'user_uuid'],
            ]
        }
    )

    # The IPAM this reservation belongs to
    ipam_uuid: Annotated[UUID4, SQLIndex()]

    # The IPv4 address - uses Python's ipaddress.IPv4Address type
    # This maps to MariaDB INET4 column type for efficient storage and indexing
    address: Annotated[IPv4Address, SQLIndex()]

    # How this address is being used
    reservation_type: Annotated[ReservationType, SQLIndex(), Field(max_length=32)]

    # The object using this address (e.g., an instance or network)
    # user_type is an ObjectType enum for type safety and efficient storage
    user_type: Optional[ObjectType] = None
    user_uuid: Optional[UUID4] = None

    # When the reservation was made (Unix epoch seconds)
    reserved_at: float

    # Optional description
    comment: Optional[str] = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Convert to the legacy etcd reservation format.

        The legacy format uses:
        - 'address': the IP address as a string
        - 'user': tuple of (object_type, object_uuid) or None
        - 'when': Unix timestamp
        - 'type': reservation type string
        - 'comment': optional comment

        Returns:
            A dict in the legacy etcd format.
        """
        user = None
        if self.user_type and self.user_uuid:
            # Convert ObjectType enum and UUID4 to strings for legacy format
            user = (str(self.user_type), str(self.user_uuid))

        return {
            'address': str(self.address),
            'user': user,
            'when': self.reserved_at,
            'type': self.reservation_type.value,
            'comment': self.comment or ''
        }

    @classmethod
    def from_legacy_dict(
        cls, ipam_uuid: str, data: dict[str, Any]
    ) -> 'IPAMReservation':
        """Create an IPAMReservation from the legacy etcd format.

        Args:
            ipam_uuid: The UUID of the IPAM this reservation belongs to.
            data: The legacy reservation dict from etcd.

        Returns:
            An IPAMReservation instance.
        """
        user = data.get('user')
        user_type = None
        user_uuid = None
        if user:
            if isinstance(user, (list, tuple)) and len(user) == 2:
                user_type_str, user_uuid = user
                # Convert string to ObjectType enum if possible
                try:
                    user_type = ObjectType(user_type_str)
                except ValueError:
                    LOG.error(f'Unknown user_type in legacy IPAM reservation: '
                              f'{user_type_str}')
            elif isinstance(user, str):
                # Very old format might have user as a string
                user_uuid = user

        # Convert address string to IPv4Address
        address_str = data['address']
        address = IPv4Address(address_str)

        return cls(
            ipam_uuid=ipam_uuid,
            address=address,
            reservation_type=data.get('type', ReservationType.UNKNOWN.value),
            user_type=user_type,
            user_uuid=user_uuid,
            reserved_at=data.get('when', 0.0),
            comment=data.get('comment')
        )

    def to_state_dict(self) -> dict[str, Any]:
        """Convert to a simple dict for storage and API responses.

        Returns:
            A dict with all reservation fields.
        """
        return {
            'ipam_uuid': str(self.ipam_uuid),
            'address': str(self.address),
            'reservation_type': self.reservation_type,
            'user_type': str(self.user_type) if self.user_type else None,
            'user_uuid': str(self.user_uuid) if self.user_uuid else None,
            'reserved_at': self.reserved_at,
            'comment': self.comment
        }
