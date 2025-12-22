import ipaddress
import random
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT


# Please note: IPAMs are a "foundational" baseobject type, which means they
# should not rely on any other baseobjects for their implementation. This is
# done to help minimize circular import problems.

LOG, _ = logs.setup(__name__)

IPAM_RESERVATIONS_PATH = '/sf/ipam_reservations/%s/'
RESERVATION_TYPE_NETWORK = 'network'
RESERVATION_TYPE_BROADCAST = 'broadcast'
RESERVATION_TYPE_GATEWAY = 'gateway'
RESERVATION_TYPE_FLOATING = 'floating'
RESERVATION_TYPE_ROUTED = 'routed'
RESERVATION_TYPE_INSTANCE = 'instance'
RESERVATION_TYPE_DELETION_HALO = 'deletion-halo'
RESERVATION_TYPE_UNKNOWN = 'unknown'


class IPAM(dbo):
    # There were older versions of ipam, but we don't admit to them because
    # the upgrade is painful.
    object_type = 'ipam'
    initial_version = 7
    current_version = 8

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None
    }

    @classmethod
    def _upgrade_step_7_to_8(cls, static_values):
        # State migration to MariaDB is now handled by sf-ctl migrate-state-to-mariadb
        ...

    def __init__(self, static_values):
        self._in_memory_only = static_values.get('in_memory_only', False)

        super().__init__(static_values['uuid'],
                         static_values.get('version'),
                         self._in_memory_only)

        self.__namespace = static_values['namespace']
        self.__network_uuid = static_values['network_uuid']
        self.__ipblock = static_values['ipblock']

        self.cached_ipblock_object = None
        self.reservations_path = IPAM_RESERVATIONS_PATH % self.uuid

        if self._in_memory_only:
            self.__in_memory_store = {}

    def _ensure_ipblock_object(self):
        if not self.cached_ipblock_object:
            self.cached_ipblock_object = ipaddress.ip_network(self.__ipblock, strict=False)
        return self.cached_ipblock_object

    @classmethod
    def new(cls, ipam_uuid, namespace, network_uuid, ipblock, in_memory_only=False):
        static_values = {
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
        o.reserve(o.network_address, ('network', network_uuid), RESERVATION_TYPE_NETWORK, '')
        o.reserve(o.broadcast_address, ('network', network_uuid), RESERVATION_TYPE_BROADCAST, '')
        o.reserve(o.get_address_at_index(1), ('network', network_uuid), RESERVATION_TYPE_GATEWAY, '')
        return o

    # Static values
    @property
    def namespace(self):
        return self.__namespace

    @property
    def network_uuid(self):
        return self.__network_uuid

    @property
    def ipblock(self):
        return self._ensure_ipblock_object()

    @property
    def netmask(self):
        return str(self._ensure_ipblock_object().netmask)

    @property
    def broadcast_address(self):
        return str(self._ensure_ipblock_object().broadcast_address)

    @property
    def network_address(self):
        return str(self._ensure_ipblock_object().network_address)

    @property
    def num_addresses(self):
        return self._ensure_ipblock_object().num_addresses

    @property
    def in_use(self):
        if self._in_memory_only:
            return self.__in_memory_store.keys()

        reservations = []
        for _, data in etcd.get_prefix_raw(self.reservations_path):
            reservations.append(data['address'])
        return reservations

    @property
    def in_use_counter(self):
        return len(self.in_use)

    def get_address_at_index(self, idx):
        return str(self.ipblock[idx])

    def is_in_range(self, address):
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        return ipaddress.ip_address(address) in self.ipblock

    def is_free(self, address):
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        return address not in self.in_use

    def reserve(self, address, user, reservation_type, comment):
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        reservation = {
            'address': address,
            'user': user,
            'when': time.time(),
            'type': reservation_type,
            'comment': comment
        }

        self.release_haloed(config.IP_DELETION_HALO_DURATION)

        if self._in_memory_only:
            if address in self.__in_memory_store:
                return False
            self.__in_memory_store[address] = reservation
            return True

        if not etcd.create_raw(self.reservations_path + address, reservation):
            return False
        self.add_event(EVENT_TYPE_AUDIT, 'reserved address', extra=reservation)
        return True

    def release(self, address):
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

        halo_reservation = {
            'address': address,
            'user': None,
            'when': time.time(),
            'type': RESERVATION_TYPE_DELETION_HALO,
            'comment': ''
        }

        reservation_path = self.reservations_path + address
        if not etcd.replace_raw(
                reservation_path, original_reservation, halo_reservation):
            return False

        self.add_event(
            EVENT_TYPE_AUDIT, 'released address to deletion-halo',
            extra=original_reservation)
        return True

    @staticmethod
    def _should_free(data, duration):
        if (data['type'] == RESERVATION_TYPE_DELETION_HALO and
                time.time() - data['when'] > duration):
            return True
        return False

    def release_haloed(self, duration):
        freed = 0

        if self._in_memory_only:
            for address in list(self.__in_memory_store.keys()):
                data = self.__in_memory_store[address]
                if self._should_free(data, duration):
                    del self.__in_memory_store[address]
                    freed += 1
            return freed

        # Handle the possible allocation race here where something's halo is
        # removed and its immediately allocated, but at the same time we
        # remove its halo by using a transactional_delete_raw.
        for key, data in etcd.get_prefix_raw(self.reservations_path):
            if self._should_free(data, duration):
                if etcd.transactional_delete_raw(key, data):
                    freed += 1
        return freed

    def get_haloed_addresses(self):
        if self._in_memory_only:
            for address in self.__in_memory_store:
                if self.__in_memory_store[address]['type'] == \
                        RESERVATION_TYPE_DELETION_HALO:
                    yield address
            return

        for _, data in etcd.get_prefix_raw(self.reservations_path):
            if data['type'] == RESERVATION_TYPE_DELETION_HALO:
                yield data['address']

    def get_random_address(self):
        bits = random.getrandbits(
            self.ipblock.max_prefixlen - self.ipblock.prefixlen)
        return str(ipaddress.IPv4Address(self.ipblock.network_address + bits))

    def reserve_random_free_address(self, unique_label_tuple, address_type, comment):
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

    def get_reservation(self, address):
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        if self._in_memory_only:
            return self.__in_memory_store.get(address)

        return etcd.get_raw(self.reservations_path + address)

    def get_allocation_age(self, address):
        if not address:
            raise exceptions.InvalidIPAMAddress(
                f'{address} is not a valid address')

        r = self.get_reservation(address)
        if not r:
            return None
        return r.get('when', time.time())

    def hard_delete(self):
        if self._in_memory_only:
            return

        etcd.delete_prefix(self.reservations_path)
        super().hard_delete()


class IPAMs(dbo_iter):
    base_object = IPAM

    def __iter__(self):
        for _, o in self.get_iterator():
            ipam_uuid = o.get('uuid')
            o = IPAM.from_db(ipam_uuid)
            if not o:
                continue

            out = self.apply_filters(o)
            if out:
                yield out
