import ipaddress
import random
import time

from shakenfist_utilities import logs

from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import ipmanager
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.baseobject import get_minimum_object_version as gmov
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.util import callstack as util_callstack


# Please note: IPAMs are a "foundational" baseobject type, which means they
# should not rely on any other baseobjects for their implementation. This is
# done to help minimize circular import problems.

# Also note that version 3 is just a proxy to the old ipmanager, which should be
# removed with fire in v0.9.

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
    object_type = 'ipam'
    initial_version = 3
    current_version = 5

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None
    }

    def __init__(self, static_values):
        # NOTE(mikal): this is unusual, but we can't do an online upgrade from
        # v3 to v4 until everyone knows about v4 due to how we're transitioning
        # from ipmanager to ipam.
        try:
            cluster_minimum = gmov(self.object_type)
        except KeyError:
            cluster_minimum = 3

        if cluster_minimum > 3:
            self.upgrade(static_values)

        super().__init__(static_values['uuid'],
                         static_values.get('version'),
                         static_values.get('in_memory_only', False))

        self.__namespace = static_values['namespace']
        self.__network_uuid = static_values['network_uuid']
        self.__ipblock = static_values['ipblock']

        self.cached_ipblock_object = None
        self.cached_ipmanager_object = None
        self.reservations_path = IPAM_RESERVATIONS_PATH % self.uuid

        if self.version == 3:
            self.cached_ipmanager_object = ipmanager.IPManager.from_db(self.uuid)

    @classmethod
    def _upgrade_step_3_to_4(cls, static_values):
        ipm = ipmanager.IPManager.from_db(static_values['uuid'])
        for address in ipm.in_use:
            etcd.put_raw((IPAM_RESERVATIONS_PATH % static_values['uuid']) + address,
                         {
                             'address': address,
                             'user': ipm.in_use[address]['user'],
                             'when': ipm.in_use[address]['when'],
                             'type': RESERVATION_TYPE_UNKNOWN,
                             'comment': ''
                         })

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values):
        try:
            ipm = ipmanager.IPManager.from_db(static_values['uuid'])
            if ipm:
                LOG.with_fields({'ipam': static_values['uuid']}).info(
                    'Removed obsolete ipmanager post IPAM upgrade')
                ipm.delete()
        except exceptions.IPManagerMissing:
            pass

    def _ensure_ipblock_object(self):
        if not self.cached_ipblock_object:
            self.cached_ipblock_object = ipaddress.ip_network(self.__ipblock, strict=False)
        return self.cached_ipblock_object

    @classmethod
    def from_db(cls, ipam_uuid, suppress_failure_audit=False):
        # This is required to handle the ipmanager to IPAM upgrade case and can
        # be removed in v0.9.
        if not ipam_uuid:
            return None

        static_values = cls._db_get(ipam_uuid)
        if static_values:
            return cls(static_values)

        try:
            ipm = ipmanager.IPManager.from_db(ipam_uuid)
            if not ipm:
                LOG.with_fields({'ipam': ipam_uuid}).debug(
                    'Failed to find both an IPAM and an ipmanager')
                if not suppress_failure_audit:
                    eventlog.add_event(
                            EVENT_TYPE_AUDIT, cls.object_type, ipam_uuid,
                            'attempt to lookup non-existent object',
                            extra={'caller': util_callstack.get_caller(offset=-3)},
                            log_as_error=True)
                return None
        except exceptions.IPManagerMissing:
            return None

        LOG.with_fields({'ipam': ipam_uuid}).warning('Falling back to ipmanager for IPAM')
        return cls({
            'uuid': ipam_uuid,
            'version': 3,
            'namespace': None,
            'network_uuid': ipam_uuid,
            'ipblock': ipm.ipblock
        })

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
        if self.version == 3:
            return None
        return self.__namespace

    @property
    def network_uuid(self):
        if self.version == 3:
            return self.uuid
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
        if self.version == 3:
            return self.cached_ipmanager_object.in_use.keys()

        reservations = []
        for _, data in etcd.get_prefix(self.reservations_path,
                                       sort_order='ascend',
                                       sort_target='key'):
            reservations.append(data['address'])
        return reservations

    @property
    def in_use_counter(self):
        return len(self.in_use)

    def get_address_at_index(self, idx):
        return str(self.ipblock[idx])

    def is_in_range(self, address):
        return ipaddress.ip_address(address) in self.ipblock

    def is_free(self, address):
        return address not in self.in_use

    def reserve(self, address, user, reservation_type, comment):
        self.release_haloed(config.IP_DELETION_HALO_DURATION)
        reservation = {
            'address': address,
            'user': user,
            'when': time.time(),
            'type': reservation_type,
            'comment': comment
        }

        with self.get_lock('reservations', op='Reserve address'):
            if self.version == 3:
                success = self.cached_ipmanager_object.reserve(address, user)
                if success:
                    self.cached_ipmanager_object.persist()
                self.log.with_fields(reservation).info('Reserved address via ipmanager')
                return success

            if not self.is_free(address):
                return False

            etcd.put_raw(self.reservations_path + address, reservation)
            self.add_event(EVENT_TYPE_AUDIT, 'reserved address', extra=reservation)
            return True

    def release(self, address):
        reservation = {
            'address': address,
            'user': None,
            'when': time.time(),
            'type': RESERVATION_TYPE_DELETION_HALO,
            'comment': ''
        }

        with self.get_lock('reservations', op='Release address'):
            if self.version == 3:
                success = self.cached_ipmanager_object.release(address)
                if success:
                    self.cached_ipmanager_object.persist()
                self.log.with_fields({'address': address}).info('Released address via ipmanager')
                return success

            if self.is_free(address):
                return False

            etcd.put_raw(self.reservations_path + address, reservation)
            self._add_item_in_attribute_list(
                'deletion-halo', [address, reservation['when']])
            self.add_event(
                EVENT_TYPE_AUDIT, 'released address to deletion-halo', extra=reservation)
            return True

    def release_haloed(self, duration):
        freed = 0
        with self.get_lock('reservations', op='Release haloed addresses'):
            haloed = self._db_get_attribute('deletion-halo', {'deletion-halo': []})
            for address, when in haloed['deletion-halo']:
                if time.time() - when > duration:
                    etcd.delete_raw(self.reservations_path + address)
                    self._remove_item_in_attribute_list(
                        'deletion-halo', [address, when])
                    self.add_event(
                        EVENT_TYPE_AUDIT, 'released address to free pool',
                        extra={'address': address})
                    freed += 1
        return freed

    def get_haloed_addresses(self):
        haloed = self._db_get_attribute('deletion-halo', {'deletion-halo': []})
        for address, _ in haloed['deletion-halo']:
            yield address

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
        if address not in self.in_use:
            return None

        if self.version == 3:
            return self.cached_ipmanager_object.in_use.get(address)

        return etcd.get_raw(self.reservations_path + address)

    def get_allocation_age(self, address):
        if self.version == 3:
            return self.cached_ipmanager_object.in_use.get(address, {}).get('when')

        r = self.get_reservation(address)
        if not r:
            return None
        return r.get('when', time.time())

    def hard_delete(self):
        if self.version == 3:
            self.cached_ipmanager_object.delete()

        etcd.delete('ipmanager', None, self.uuid)
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
