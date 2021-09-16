import ipaddress
import random
import time

from shakenfist import db
from shakenfist import exceptions
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


# NOTE(mikal): IPManager should _always_ return addresses as strings,
# not as ipaddress.IPv4Address.

class IPManager(object):
    def __init__(self, uuid=None, ipblock=None, in_use=None):
        self.uuid = uuid
        self.ipblock = ipblock
        self.ipblock_obj = ipaddress.ip_network(ipblock, strict=False)
        self.log = LOG.with_field('ipmanager', self.uuid)

        self.netmask = self.ipblock_obj.netmask
        self.broadcast_address = str(self.ipblock_obj.broadcast_address)
        self.network_address = str(self.ipblock_obj.network_address)
        self.num_addresses = self.ipblock_obj.num_addresses

        if in_use:
            self.in_use_counter = len(in_use)
            self.in_use = in_use
        else:
            self.in_use_counter = 0
            self.in_use = {
                self.network_address: {
                    'user': self.unique_label(),
                    'when': time.time()
                },
                self.broadcast_address: {
                    'user': self.unique_label(),
                    'when': time.time()
                }
            }

    def unique_label(self):
        return ('ipmanager', self.uuid)

    @staticmethod
    def new(uuid, ipblock):
        with db.get_lock('ipmanager', None, uuid, ttl=120,
                         op='Network object initialization'):
            ipm = IPManager(uuid, ipblock)
            # Reserve first IP address
            ipm.reserve(ipm.get_address_at_index(1), ipm.unique_label())
            ipm.persist()
        return ipm

    @staticmethod
    def from_db(uuid):
        db_data = db.get_ipmanager(uuid)

        if 'ipmanager.v3' in db_data:
            ipm = IPManager(**db_data['ipmanager.v3'])
        else:
            db_data['ipmanager.v3'] = {}
            db_data['ipmanager.v3']['ipblock'] = db_data['ipmanager.v2']['ipblock']
            db_data['ipmanager.v3']['in_use'] = {}

            for addr in db_data['ipmanager.v2']['in_use']:
                db_data['ipmanager.v3']['in_use'][addr] = {
                    'user': db_data['ipmanager.v2']['in_use'][addr],
                    'when': time.time()
                }
            ipm = IPManager(**db_data['ipmanager.v3'])

        return ipm

    def persist(self):
        d = {
            'ipmanager.v3': {
                'ipblock': self.ipblock,
                'in_use': self.in_use,
                'uuid': self.uuid
            }
        }
        db.persist_ipmanager(self.uuid, d)

    def delete(self):
        db.delete_ipmanager(self.uuid)

    def get_address_at_index(self, idx):
        return str(self.ipblock_obj[idx])

    def is_in_range(self, address):
        return ipaddress.ip_address(address) in self.ipblock_obj

    def is_free(self, address):
        return address not in self.in_use

    def reserve(self, address, unique_label_tuple):
        if not self.is_free(address):
            return False

        self.log.with_field(*unique_label_tuple).with_field(
            'address', address).info('Reserving address')
        self.in_use[address] = {
            'user': unique_label_tuple,
            'when': time.time()
        }
        self.in_use_counter += 1
        return True

    def release(self, address):
        if self.is_free(address):
            return

        self.log.with_field('address', address).info('Releasing address')
        del self.in_use[address]
        self.in_use_counter -= 1

    def get_random_address(self):
        bits = random.getrandbits(
            self.ipblock_obj.max_prefixlen - self.ipblock_obj.prefixlen)
        return str(ipaddress.IPv4Address(self.ipblock_obj.network_address + bits))

    def get_random_free_address(self, unique_label_tuple):
        if self.in_use_counter / self.num_addresses < 0.5:
            while True:
                addr = self.get_random_address()
                free = self.reserve(addr, unique_label_tuple)
                if free:
                    return str(addr)

        else:
            idx = 1
            while idx < self.num_addresses:
                addr = self.get_address_at_index(idx)
                free = self.reserve(addr, unique_label_tuple)
                if free:
                    return str(addr)

                idx += 1

        raise exceptions.CongestedNetwork('No free addresses on network')
