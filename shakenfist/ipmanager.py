import ipaddress
from shakenfist_utilities import logs
import time

from shakenfist import db


LOG, _ = logs.setup(__name__)


# NOTE(mikal): IPManager should _always_ return addresses as strings,
# not as ipaddress.IPv4Address.

class IPManager:
    def __init__(self, uuid=None, ipblock=None, in_use=None):
        self.uuid = uuid
        self.ipblock = ipblock
        self.ipblock_obj = ipaddress.ip_network(ipblock, strict=False)
        self.log = LOG.with_fields({'ipmanager': self.uuid})

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

    def is_free(self, address):
        return address not in self.in_use

    def reserve(self, address, unique_label_tuple):
        if not self.is_free(address):
            return False

        self.in_use[address] = {
            'user': unique_label_tuple,
            'when': time.time()
        }
        self.in_use_counter += 1
        return True

    def release(self, address):
        if self.is_free(address):
            return False

        del self.in_use[address]
        self.in_use_counter -= 1
        return True
