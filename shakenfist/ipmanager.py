import ipaddress
import random

from shakenfist import db


class IPManager(object):
    def __init__(self, uuid, ipblock, in_use=None):
        self.uuid = uuid
        self.ipblock = ipblock
        self.ipblock_obj = ipaddress.ip_network(ipblock, strict=False)

        self.netmask = self.ipblock_obj.netmask
        self.broadcast_address = self.ipblock_obj.broadcast_address
        self.network_address = self.ipblock_obj.network_address

        self.num_addresses = self.ipblock_obj.num_addresses
        self.in_use = {
            self.network_address: True,
            self.broadcast_address: True
        }
        self.in_use_counter = 0

        if in_use:
            for addr in in_use:
                self.reserve(addr)

    @staticmethod
    def new(uuid, ipblock):
        with db.get_lock('ipmanager', None, uuid, ttl=120,
                         op='Network object initialization'):
            ipm = IPManager(uuid, ipblock)
            # Reserve first IP address
            ipm.reserve(ipm.get_address_at_index(1))
            ipm.persist()
        return ipm

    @staticmethod
    def from_db(uuid):
        db_data = db.get_ipmanager(uuid)
        ipm = IPManager(uuid=uuid, **db_data['ipmanager.v1'])
        return ipm

    def persist(self):
        in_use = []
        for ip in self.in_use:
            ip_str = str(ip)
            if ip_str not in in_use:
                in_use.append(ip_str)

        d = {
                'ipmanager.v1': {
                    'ipblock': self.ipblock,
                    'in_use': in_use
                }
            }
        db.persist_ipmanager(self.uuid, d)

    def delete(self):
        db.delete_ipmanager(self.uuid)

    def get_address_at_index(self, idx):
        return self.ipblock_obj[idx]

    def is_in_range(self, address):
        return ipaddress.ip_address(address) in self.ipblock_obj

    def is_free(self, address):
        return address not in self.in_use

    def reserve(self, address):
        if not self.is_free(address):
            return False

        self.in_use[address] = True
        self.in_use_counter += 1
        return True

    def release(self, address):
        if self.is_free(address):
            return

        del self.in_use[address]
        self.in_use_counter -= 1

    def get_random_address(self):
        bits = random.getrandbits(
            self.ipblock_obj.max_prefixlen - self.ipblock_obj.prefixlen)
        return str(ipaddress.IPv4Address(self.ipblock_obj.network_address + bits))

    def get_random_free_address(self):
        if self.in_use_counter / self.num_addresses < 0.5:
            while True:
                addr = self.get_random_address()
                free = self.reserve(addr)
                if free:
                    return str(addr)

        else:
            idx = 1
            while idx < self.num_addresses:
                addr = self.get_address_at_index(idx)
                free = self.reserve(addr)
                if free:
                    return str(addr)

                idx += 1
