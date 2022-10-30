from collections import defaultdict
from functools import partial
import time

from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist.constants import GiB
from shakenfist import etcd
from shakenfist import logutil
from shakenfist.util import general as util_general


LOG, _ = logutil.setup(__name__)


class Node(dbo):
    object_type = 'node'
    current_version = 2

    # docs/development/state_machine.md has a description of these states.
    STATE_MISSING = 'missing'
    STATE_STOPPING = 'stopping'
    STATE_STOPPED = 'stopped'

    ACTIVE_STATES = set([dbo.STATE_CREATED])

    state_targets = {
        None: (dbo.STATE_CREATED, dbo.STATE_ERROR, STATE_MISSING),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR, STATE_MISSING,
                            STATE_STOPPING),
        STATE_STOPPING: (STATE_STOPPED, dbo.STATE_DELETED, dbo.STATE_ERROR,
                         dbo.STATE_CREATED),
        STATE_STOPPED: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),

        # A node can return from the dead...
        dbo.STATE_ERROR: (dbo.STATE_CREATED, dbo.STATE_DELETED),
        STATE_MISSING: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR)
    }

    def __init__(self, static_values):
        # We treat a node name as a UUID here for historical reasons
        super(Node, self).__init__(static_values['fqdn'],
                                   static_values.get('version'))

        self.__ip = static_values['ip']
        self.__fqdn = static_values['fqdn']

    @classmethod
    def new(cls, name, ip):
        n = Node.from_db(name)
        if n:
            return n

        Node._db_create(name, {
            'fqdn': name,
            'ip': ip,
            'version': cls.current_version
        })
        n = Node.from_db(name)
        n.state = cls.STATE_CREATED
        return n

    @classmethod
    def observe_this_node(cls):
        # We use Node.new here because it acts like a "upsert". It will create
        # the node object if it doesn't already exist, and otherwise use the
        # existing one.
        n = cls.new(config.NODE_NAME, config.NODE_MESH_IP)
        n._db_set_attribute('observed',
                            {
                                'at': time.time(),
                                'release': util_general.get_version()
                            })

        roles = {
            'is_etcd_master': config.NODE_IS_ETCD_MASTER,
            'is_hypervisor': config.NODE_IS_HYPERVISOR,
            'is_network_node': config.NODE_IS_NETWORK_NODE,
            'is_eventlog_node': config.NODE_IS_EVENTLOG_NODE
        }
        if n._db_get_attribute('roles') != roles:
            n._db_set_attribute('roles', roles)

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        retval = {
            'uuid': self.uuid,
            'fqdn': self.fqdn,
            'ip': self.ip,
            'state': self.state.value,
            'lastseen': self.last_seen,
            'version': self.installed_version
        }
        retval.update(self._db_get_attribute('roles'))
        return retval

    # Static values
    @property
    def ip(self):
        return self.__ip

    @property
    def fqdn(self):
        return self.__fqdn

    # Values routed to attributes, writes are via helper methods.
    @property
    def last_seen(self):
        return self._db_get_attribute('observed').get('at', 0)

    @property
    def installed_version(self):
        return self._db_get_attribute('observed').get('release')

    @property
    def blobs(self):
        return self._db_get_attribute('blobs').get('blobs', [])

    @blobs.setter
    def blobs(self, value):
        self._db_set_attribute('blobs', {'blobs': value})

    def add_blob(self, blob):
        self._add_item_in_attribute_list('blobs', blob)

    def remove_blob(self, blob):
        self._remove_item_in_attribute_list('blobs', blob)

    def delete(self):
        self.state = self.STATE_DELETED

    def hard_delete(self):
        # We do not hard delete nodes, they're special.
        pass


class Nodes(dbo_iter):
    def __iter__(self):
        for _, n in etcd.get_all('node', None):
            uniq = n.get('uuid')
            if not uniq:
                uniq = n.get('fqdn')
            if not uniq:
                continue

            n = Node.from_db(uniq)
            if not n:
                continue

            out = self.apply_filters(n)
            if out:
                yield out


active_states_filter = partial(baseobject.state_filter, Node.ACTIVE_STATES)
inactive_states_filter = partial(
    baseobject.state_filter, [dbo.STATE_DELETED, dbo.STATE_ERROR, Node.STATE_MISSING])


def _sort_by_key(d):
    for k in sorted(d, reverse=True):
        for v in d[k]:
            yield v


def nodes_by_free_disk_descending(minimum=0, maximum=-1, intention=None):
    by_disk = defaultdict(list)
    if not intention:
        intention = ''
    else:
        intention = '_%s' % intention

    for n in Nodes([active_states_filter]):
        metrics = etcd.get('metrics', n.fqdn, None)
        if metrics:
            metrics = metrics.get('metrics', {})
        else:
            metrics = {}

        disk_free_gb = int(
            int(metrics.get('disk_free%s' % intention, '0')) / GiB)

        if disk_free_gb < minimum:
            continue
        if maximum != -1 and disk_free_gb > maximum:
            continue

        by_disk[disk_free_gb].append(n.fqdn)

    return list(_sort_by_key(by_disk))
