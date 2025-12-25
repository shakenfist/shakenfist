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
from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.sqlalchemy import SQLIndex


class ReservationType(str, Enum):
    """Enum of valid IPAM reservation types.

    These describe how an IP address is being used within a network.

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

    def __str__(self) -> str:
        """Return the enum value as a string."""
        return self.value

    NETWORK = 'network'
    BROADCAST = 'broadcast'
    GATEWAY = 'gateway'
    FLOATING = 'floating'
    ROUTED = 'routed'
    INSTANCE = 'instance'
    DELETION_HALO = 'deletion-halo'
    UNKNOWN = 'unknown'


class IPAMReservation(BaseModel):
    """Schema for IPAM reservation storage in MariaDB.

    This model represents a single IP address reservation within an IPAM.
    The combination of (ipam_uuid, address) is unique - each address can
    only be reserved once within a given IPAM.

    Attributes:
        ipam_uuid: The UUID of the IPAM this reservation belongs to.
        address: The IP address being reserved (IPv4 or IPv6, max 45 chars).
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
    ipam_uuid: Annotated[str, SQLIndex(), Field(max_length=36)]

    # The IP address - 45 chars for IPv6 with zone ID
    address: Annotated[str, SQLIndex(), Field(max_length=45)]

    # How this address is being used
    reservation_type: Annotated[str, SQLIndex(), Field(max_length=32)]

    # The object using this address (e.g., an instance or network)
    user_type: Optional[str] = Field(default=None, max_length=32)
    user_uuid: Optional[str] = Field(default=None, max_length=36)

    # When the reservation was made (Unix epoch seconds)
    reserved_at: float

    # Optional description
    comment: Optional[str] = None

    def to_legacy_dict(self) -> dict[str, Any]:
        """Convert to the legacy etcd reservation format.

        The legacy format uses:
        - 'address': the IP address
        - 'user': tuple of (object_type, object_uuid) or None
        - 'when': Unix timestamp
        - 'type': reservation type string
        - 'comment': optional comment

        Returns:
            A dict in the legacy etcd format.
        """
        user = None
        if self.user_type and self.user_uuid:
            user = (self.user_type, self.user_uuid)

        return {
            'address': self.address,
            'user': user,
            'when': self.reserved_at,
            'type': self.reservation_type,
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
                user_type, user_uuid = user
            elif isinstance(user, str):
                # Very old format might have user as a string
                user_uuid = user

        return cls(
            ipam_uuid=ipam_uuid,
            address=data['address'],
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
            'ipam_uuid': self.ipam_uuid,
            'address': self.address,
            'reservation_type': self.reservation_type,
            'user_type': self.user_type,
            'user_uuid': self.user_uuid,
            'reserved_at': self.reserved_at,
            'comment': self.comment
        }
