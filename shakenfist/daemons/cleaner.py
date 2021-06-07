import etcd3
import json
import os
import random
import time

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import logutil
from shakenfist import instance
from shakenfist import net
from shakenfist import networkinterface
from shakenfist.node import (
    Node, Nodes,
    active_states_filter as node_active_states_filter,
    inactive_states_filter as node_inactive_states_filter)
from shakenfist import util


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.Daemon):
    def _update_power_states(self):
        libvirt = util.get_libvirt()
        conn = libvirt.open('qemu:///system')
        try:
            seen = []

            # Active VMs have an ID. Active means running in libvirt
            # land.
            for domain_id in conn.listDomainsID():
                domain = conn.lookupByID(domain_id)
                if not domain.name().startswith('sf:'):
                    continue

                instance_uuid = domain.name().split(':')[1]
                log_ctx = LOG.with_instance(instance_uuid)

                inst = instance.Instance.from_db(instance_uuid)
                if not inst:
                    # Instance is SF but not in database. Kill to reduce load.
                    log_ctx.warning('Destroying unknown instance')
                    util.execute(None,
                                 'virsh destroy "sf:%s"' % instance_uuid)
                    continue

                inst.place_instance(config.NODE_NAME)
                seen.append(domain.name())

                db_state = inst.state
                if db_state.value == dbo.STATE_DELETED:
                    # NOTE(mikal): a delete might be in-flight in the queue.
                    # We only worry about instances which should have gone
                    # away five minutes ago.
                    if time.time() - db_state.update_time < 300:
                        continue

                    inst.enforced_deletes_increment()
                    attempts = inst._db_get_attribute(
                        'enforced_deletes')['count']

                    if attempts > 5:
                        # Sometimes we just can't delete the VM. Try the big
                        # hammer instead.
                        log_ctx.warning(
                            'Attempting alternate delete method for instance')
                        util.execute(None,
                                     'virsh destroy "sf:%s"' % instance_uuid)

                        inst.add_event('enforced delete', 'complete')
                    else:
                        inst.delete()

                    log_ctx.with_field('attempt', attempts).warning(
                        'Deleting stray instance')

                    continue

                state = util.extract_power_state(libvirt, domain)
                inst.update_power_state(state)
                if state == 'crashed':
                    inst.state = inst.state.value + '-error'

            # Inactive VMs just have a name, and are powered off
            # in our state system.
            for domain_name in conn.listDefinedDomains():
                if not domain_name.startswith('sf:'):
                    continue

                if domain_name not in seen:
                    instance_uuid = domain_name.split(':')[1]
                    log_ctx = LOG.with_instance(instance_uuid)
                    inst = instance.Instance.from_db(instance_uuid)

                    if not inst:
                        # Instance is SF but not in database. Kill because
                        # unknown.
                        log_ctx.warning('Removing unknown inactive instance')
                        domain = conn.lookupByName(domain_name)
                        domain.undefine()
                        continue

                    db_state = inst.state
                    if db_state.value == dbo.STATE_DELETED:
                        # NOTE(mikal): a delete might be in-flight in the queue.
                        # We only worry about instances which should have gone
                        # away five minutes ago.
                        if time.time() - db_state.update_time < 300:
                            continue

                        domain = conn.lookupByName(domain_name)
                        domain.undefine()
                        log_ctx.info('Detected stray instance')
                        inst.add_event('deleted stray', 'complete')
                        continue

                    inst.place_instance(config.NODE_NAME)

                    db_power = inst.power_state
                    if not os.path.exists(inst.instance_path):
                        # If we're inactive and our files aren't on disk,
                        # we have a problem.
                        log_ctx.info('Detected error state for instance')
                        inst.state = inst.state.value + '-error'

                    elif not db_power or db_power['power_state'] != 'off':
                        log_ctx.info('Detected power off for instance')
                        inst.update_power_state('off')
                        inst.add_event('detected poweroff', 'complete')

        except libvirt.libvirtError as e:
            LOG.error('Failed to lookup all domains: %s' % e)

    def _compact_etcd(self):
        try:
            # We need to determine what revision to compact to, so we keep a
            # key which stores when we last compacted and we use it's latest
            # revision number as the revision to compact to. Note that we use
            # a different library for compaction as our primary library does
            # not support it.
            c = etcd3.client()
            c.put('/sf/compact',
                  json.dumps({'compacted_at': time.time()}))
            _, kv = c.get('/sf/compact')
            c.compact(kv.mod_revision, physical=True)
            c.defragment()
            LOG.info('Compacted etcd')

        except Exception as e:
            util.ignore_exception('etcd compaction', e)

    def run(self):
        LOG.info('Starting')

        # Delay first compaction until system startup load has reduced
        last_compaction = time.time() - random.randint(1, 20*60)

        last_loop_run = time.time()
        while True:
            # Update power state of all instances on this hypervisor
            LOG.info('Updating power states')
            self._update_power_states()

            # Cleanup soft deleted instances and networks
            for i in instance.inactive_instances():
                LOG.with_object(i).info('Hard deleting instance')
                i.hard_delete()

            for n in net.inactive_networks():
                LOG.with_network(n).info('Hard deleting network')
                n.hard_delete()

            for ni in networkinterface.inactive_network_interfaces():
                LOG.with_networkinterface(
                    ni).info('Hard deleting network interface')
                ni.hard_delete()

            for n in Nodes([node_inactive_states_filter]):
                age = time.time() - n.last_seen

                # Find nodes which have returned from being missing
                if age < config.NODE_CHECKIN_MAXIMUM:
                    n.state = Node.STATE_CREATED
                    LOG.with_object(n).info('Node returned from being missing')

                # Find nodes which have been offline for a long time, unless
                # this machine has been asleep for a long time (think developer
                # laptop).
                if (time.time() - last_loop_run < config.NODE_CHECKIN_MAXIMUM
                        and age > config.NODE_CHECKIN_MAXIMUM * 10):
                    n.state = Node.STATE_ERROR
                    for i in instance.healthy_instances_on_node(n):
                        LOG.with_object(i).with_object(n).info(
                            'Node in error state, erroring instance')
                        # Note, this queue job is just in case the node comes
                        # back.
                        i.enqueue_delete_due_error('Node in error state')

            # Find nodes which haven't checked in recently
            for n in Nodes([node_active_states_filter]):
                age = time.time() - n.last_seen
                if age > config.NODE_CHECKIN_MAXIMUM:
                    n.state = Node.STATE_MISSING

            # Perform etcd maintenance
            if time.time() - last_compaction > 1800:
                LOG.info('Compacting etcd')
                self._compact_etcd()
                last_compaction = time.time()

            last_loop_run = time.time()
            time.sleep(60)
