import ipaddress
import json
import random


def from_db(d):
    nb = NetBlock(d['ipmanager.v1']['ipblock'])
    for addr in d['ipmanager.v1']['in_use']:
        nb.reserve(addr)
    return nb


class NetBlock(object):
    def __init__(self, ipblock):
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

    def get_address_at_index(self, idx):
        return self.ipblock_obj[idx]

    def is_free(self, address):
        return not address in self.in_use

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

    def save(self):
        in_use = []
        for ip in self.in_use:
            ip_str = str(ip)
            if not ip_str in in_use:
                in_use.append(ip_str)

        return {
            'ipmanager.v1': {
                'ipblock': self.ipblock,
                'in_use': in_use
            }
        }
