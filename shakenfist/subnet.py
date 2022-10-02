import ipaddress
import random
from shakenfist_utilities import logs
import time

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import exceptions


LOG, _ = logs.setup(__name__)


# Subnets are unusual in that they do not get exposed via the API
# currently. They are an internal tracking mechanism used by networks.
# There is a strong 1:1 relationship between a network and a subnet,
# and that should be enforced where possible. This might change in the
# future, but that's how it is for now at least.


class Subnet(dbo):
    object_type = 'subnet'
    current_version = 1

    ACTIVE_STATES = set([dbo.STATE_CREATED])

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED)
    }

    def __init__(self, static_values):
        super(Subnet, self).__init__(static_values['uuid'],
                                     static_values.get('version'))
        self.__network_uuid = static_values['network_uuid']
        self.__iprange = static_values['iprange']

        self.ipblock_obj = ipaddress.ip_network(self.__iprange, strict=False)
        self.netmask = self.ipblock_obj.netmask
        self.broadcast_address = str(self.ipblock_obj.broadcast_address)
        self.network_address = str(self.ipblock_obj.network_address)
        self.num_addresses = self.ipblock_obj.num_addresses

    @classmethod
    def new(cls, subnet_uuid, network_uuid, iprange):
        Subnet._db_create(subnet_uuid, {
            'uuid': subnet_uuid,
            'network_uuid': network_uuid,
            'iprange': iprange,
            'version': cls.current_version
        })

        s = Subnet.from_db(subnet_uuid)
        s.state = cls.STATE_CREATED

        # Reserve network and broadcast addresses
        s.reserve(s.network_address, s.unique_label())
        s.reserve(s.broadcast_address, s.unique_label())

        # Reserve first IP address for router
        # TODO(mikal): move this to network, it doesn't make sense for some
        # networks -- floating networks for example.
        s.reserve(s.get_address_at_index(1), s.unique_label())
        return s

    # Static values
    @property
    def network_uuid(self):
        return self.__network_uuid

    @property
    def iprange(self):
        return self.__iprange

    # Values routed to attributes
    @property
    def allocations(self):
        with self.get_lock_attr('allocations', 'Get usage'):
            allocs = self._db_get_attribute('allocations',
                                            {
                                                'counter': 0,
                                                'addresses': {}
                                            })
            return allocs

    def reserve(self, address, unique_label_tuple, when=None):
        with self.get_lock_attr('allocations', 'Reserve address'):
            allocs = self._db_get_attribute('allocations',
                                            {
                                                'counter': 0,
                                                'addresses': {}
                                            })

            if address in allocs['addresses']:
                return False

            if not when:
                when = time.time()

            allocs['addresses'][address] = {
                'user': unique_label_tuple,
                'when': when
            }
            allocs['counter'] += 1
            self._db_set_attribute('allocations', allocs)
            self.add_event('Allocated address %s to %s'
                           % (address, unique_label_tuple))
            return True

    def release(self, address):
        if not address:
            return False

        with self.get_lock_attr('allocations', 'Release address'):
            allocs = self._db_get_attribute('allocations',
                                            {
                                                'counter': 0,
                                                'addresses': {}
                                            })

            if address not in allocs['addresses']:
                self.log.error('Attempt to release unallocated address %s'
                               % address)
                return False

            self.add_event('Released address %s previously allocated to %s'
                           % (address, allocs['addresses'][address]['user']))
            del allocs['addresses'][address]
            allocs['counter'] -= 1
            self._db_set_attribute('allocations', allocs)
            return True

    # Helper methods
    def get_address_at_index(self, idx):
        return str(self.ipblock_obj[idx])

    def is_in_range(self, address):
        return ipaddress.ip_address(address) in self.ipblock_obj

    def is_free(self, address):
        return address not in self.allocations['addresses']

    def get_random_address(self):
        bits = random.getrandbits(
            self.ipblock_obj.max_prefixlen - self.ipblock_obj.prefixlen)
        return str(ipaddress.IPv4Address(self.ipblock_obj.network_address + bits))

    def get_random_free_address(self, unique_label_tuple):
        # Fast path give up for full networks
        if self.allocations['counter'] == self.num_addresses:
            raise exceptions.CongestedNetwork('No free addresses on network')

        # Five attempts at using a random address
        attempts = 0
        while attempts < 5:
            attempts += 1
            addr = self.get_random_address()
            free = self.reserve(addr, unique_label_tuple)
            if free:
                return str(addr)

        # Fall back to a linear scan looking for a gap
        idx = 1
        while idx < self.num_addresses:
            addr = self.get_address_at_index(idx)
            free = self.reserve(addr, unique_label_tuple)
            if free:
                return str(addr)

            idx += 1

        # Give up
        raise exceptions.CongestedNetwork('No free addresses on network')

    def delete(self):
        self.state = self.STATE_DELETED
