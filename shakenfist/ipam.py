import ipaddress
import random
import time
from collections.abc import Iterator
from ipaddress import IPv4Address
from typing import Any, Optional

from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.schema.ipam_reservation import IPAMReservation
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType


# Please note: IPAMs are a "foundational" baseobject type, which means they
# should not rely on any other baseobjects for their implementation. This is
# done to help minimize circular import problems.

LOG, _ = logs.setup(__name__)

# Legacy etcd path - only used for migration
IPAM_RESERVATIONS_PATH = '/sf/ipam_reservations/%s/'


class IPAM(dbo):
    # There were older versions of ipam, but we don't admit to them because
    # the upgrade is painful.
    object_type = ObjectType.IPAM
    initial_version = 7
    current_version = 8

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None
    }

    @classmethod
    def _upgrade_step_7_to_8(cls, static_values: dict[str, Any]) -> None:
        # State migration to MariaDB is now handled by sf-ctl migrate-state-to-mariadb
        ...

    def __init__(self, static_values: dict[str, Any]) -> None:
        self._in_memory_only: bool = static_values.get('in_memory_only', False)

        super().__init__(static_values['uuid'],
                         static_values.get('version'),
                         self._in_memory_only)

        self.__namespace: str = static_values['namespace']
        self.__network_uuid: str = static_values['network_uuid']
        self.__ipblock: str = static_values['ipblock']

        self.cached_ipblock_object: Optional[ipaddress.IPv4Network] = None
        self.reservations_path: str = IPAM_RESERVATIONS_PATH % self.uuid

        if self._in_memory_only:
            # In-memory store now uses IPAMReservation objects directly
            self.__in_memory_store: dict[str, IPAMReservation] = {}

    def _ensure_ipblock_object(self) -> ipaddress.IPv4Network:
        if not self.cached_ipblock_object:
            self.cached_ipblock_object = ipaddress.ip_network(
                self.__ipblock, strict=False)
        return self.cached_ipblock_object

    @classmethod
    def new(cls, ipam_uuid: str, namespace: Optional[str], network_uuid: str,
            ipblock: str, in_memory_only: bool = False) -> 'IPAM':
        static_values: dict[str, Any] = {
                'uuid': ipam_uuid,
                'namespace': namespace,
                'network_uuid': network_uuid,
                'ipblock': ipblock,
                'version': cls.current_version
            }

        if in_memory_only:
            static_values['in_memory_only'] = True
            o = IPAM(static_values)
            o.log.with_fields(static_values).info('IPAM is in-memory only')
        else:
            IPAM._db_create(ipam_uuid, static_values)
            o = IPAM.from_db(ipam_uuid)

        o.state = cls.STATE_CREATED
        o.reserve(o.network_address, ('network', network_uuid),
                  ReservationType.NETWORK, '')
        o.reserve(o.broadcast_address, ('network', network_uuid),
                  ReservationType.BROADCAST, '')
        o.reserve(o.get_address_at_index(1), ('network', network_uuid),
                  ReservationType.GATEWAY, '')
        return o

    # Static values
    @property
    def namespace(self) -> str:
        return self.__namespace

    @property
    def network_uuid(self) -> str:
        return self.__network_uuid

    @property
    def ipblock(self) -> ipaddress.IPv4Network:
        return self._ensure_ipblock_object()

    @property
    def netmask(self) -> str:
        return str(self._ensure_ipblock_object().netmask)

    @property
    def broadcast_address(self) -> str:
        return str(self._ensure_ipblock_object().broadcast_address)

    @property
    def network_address(self) -> str:
        return str(self._ensure_ipblock_object().network_address)

    @property
    def num_addresses(self) -> int:
        return self._ensure_ipblock_object().num_addresses

    @property
    def in_use(self) -> set[str]:
        if self._in_memory_only:
            return set(self.__in_memory_store.keys())

        return mariadb.get_addresses_in_use(self.uuid)

    @property
    def in_use_counter(self) -> int:
        return len(self.in_use)

    def get_address_at_index(self, idx: int) -> str:
        return str(self.ipblock[idx])

    def is_in_range(self, address: Optional[str]) -> bool:
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        return ipaddress.ip_address(address) in self.ipblock

    def is_free(self, address: Optional[str]) -> bool:
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        return address not in self.in_use

    def reserve(self, address: Optional[str],
                user: Optional[tuple[str, str] | str],
                reservation_type: ReservationType, comment: str) -> bool:
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        user_type = None
        user_uuid = None
        if user:
            if isinstance(user, (list, tuple)) and len(user) == 2:
                user_type, user_uuid = user
            elif isinstance(user, str):
                user_uuid = user

        reservation = IPAMReservation(
            ipam_uuid=self.uuid,
            address=IPv4Address(address),
            reservation_type=reservation_type,
            user_type=user_type,
            user_uuid=user_uuid,
            reserved_at=time.time(),
            comment=comment or None
        )

        self.release_haloed(config.IP_DELETION_HALO_DURATION)

        if self._in_memory_only:
            if address in self.__in_memory_store:
                return False
            self.__in_memory_store[address] = reservation
            return True

        if not mariadb.reserve_address(reservation):
            return False
        self.add_event(
            EVENT_TYPE_AUDIT, 'reserved address',
            extra=reservation.to_legacy_dict())
        return True

    def release(self, address: Optional[str]) -> bool:
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        if self._in_memory_only:
            if address not in self.__in_memory_store:
                return False
            del self.__in_memory_store[address]
            return True

        original_reservation = self.get_reservation(address)
        if not original_reservation:
            return False

        halo_reservation = IPAMReservation(
            ipam_uuid=self.uuid,
            address=IPv4Address(address),
            reservation_type=ReservationType.DELETION_HALO,
            user_type=None,
            user_uuid=None,
            reserved_at=time.time(),
            comment=None
        )

        if not mariadb.release_address(self.uuid, address, halo_reservation):
            return False

        self.add_event(
            EVENT_TYPE_AUDIT, 'released address to deletion-halo',
            extra=original_reservation.to_legacy_dict())
        return True

    @staticmethod
    def _should_free(reservation: IPAMReservation, duration: float | int) -> bool:
        if (reservation.reservation_type == ReservationType.DELETION_HALO and
                time.time() - reservation.reserved_at > duration):
            return True
        return False

    def release_haloed(self, duration: float | int) -> int:
        freed = 0

        if self._in_memory_only:
            for address in list(self.__in_memory_store.keys()):
                reservation = self.__in_memory_store[address]
                if self._should_free(reservation, duration):
                    del self.__in_memory_store[address]
                    freed += 1
            return freed

        # Calculate the cutoff time for deletion-halo expiry
        older_than = time.time() - duration
        return mariadb.release_haloed_addresses(self.uuid, older_than)

    def get_haloed_addresses(self) -> Iterator[str]:
        if self._in_memory_only:
            for address in self.__in_memory_store:
                if self.__in_memory_store[address].reservation_type == \
                        ReservationType.DELETION_HALO:
                    yield address
            return

        for res in mariadb.get_reservations_for_ipam(self.uuid):
            if res.reservation_type == ReservationType.DELETION_HALO:
                yield str(res.address)

    def get_random_address(self) -> str:
        bits = random.getrandbits(
            self.ipblock.max_prefixlen - self.ipblock.prefixlen)
        return str(ipaddress.IPv4Address(self.ipblock.network_address + bits))

    def reserve_random_free_address(self, unique_label_tuple: tuple[str, str],
                                    address_type: ReservationType,
                                    comment: str) -> str:
        # Fast path give up for full networks
        if self.in_use_counter == self.num_addresses:
            raise exceptions.CongestedNetwork('No free addresses on network')

        # Five attempts at using a random address
        attempts = 0
        while attempts < 5:
            attempts += 1
            addr = self.get_random_address()
            free = self.reserve(addr, unique_label_tuple, address_type, comment)
            if free:
                return str(addr)

        # Fall back to a linear scan looking for a gap
        idx = 1
        while idx < self.num_addresses:
            addr = self.get_address_at_index(idx)
            free = self.reserve(addr, unique_label_tuple, address_type, comment)
            if free:
                return str(addr)

            idx += 1

        # If we're congested, decrease the deletion halo period to see if that
        # helps
        freed = self.release_haloed(30)
        if freed:
            self.log.warning(
                'Released %d haloed network addresses due to congestion' % freed)

            # One last linear scan if we freed any
            idx = 1
            while idx < self.num_addresses:
                addr = self.get_address_at_index(idx)
                free = self.reserve(addr, unique_label_tuple, address_type, comment)
                if free:
                    return str(addr)

                idx += 1

        # Give up
        raise exceptions.CongestedNetwork('No free addresses on network')

    def get_reservation(self, address: Optional[str]) -> Optional[IPAMReservation]:
        """Get the reservation for a specific address.

        Args:
            address: The IP address to look up.

        Returns:
            The IPAMReservation if found, None otherwise.

        Raises:
            InvalidIPAMAddress: If address is None or empty.
        """
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        if self._in_memory_only:
            return self.__in_memory_store.get(address)

        return mariadb.get_reservation(self.uuid, address)

    def get_allocation_age(self, address: Optional[str]) -> Optional[float]:
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        r = self.get_reservation(address)
        if not r:
            return None
        return r.reserved_at

    def hard_delete(self) -> None:
        if self._in_memory_only:
            return

        mariadb.delete_reservations_for_ipam(self.uuid)
        super().hard_delete()


class IPAMs(dbo_iter):
    base_object = IPAM

    def __iter__(self) -> Iterator[IPAM]:
        for _, o in self.get_iterator():
            ipam_uuid = o.get('uuid')
            ipam_obj = IPAM.from_db(ipam_uuid)
            if not ipam_obj:
                continue

            out = self.apply_filters(ipam_obj)
            if out:
                yield out
