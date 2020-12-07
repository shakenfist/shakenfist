import etcd3
import json
import os
import random
import time

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import logutil
from shakenfist import util
from shakenfist import virt


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
                log_ctx = LOG.withInstance(instance_uuid)

                instance = virt.Instance.from_db(instance_uuid)
                if not instance:
                    # Instance is SF but not in database. Kill to reduce load.
                    log_ctx.warning('Destroying unknown instance')
                    util.execute(None,
                                 'virsh destroy "sf:%s"' % instance_uuid)
                    continue

                instance.place_instance(config.NODE_NAME)
                seen.append(domain.name())

                dbstate = db.get_instance_attribute(
                    instance.uuid, 'state')
                if dbstate.get('state') == 'deleted':
                    # NOTE(mikal): a delete might be in-flight in the queue.
                    # We only worry about instances which should have gone
                    # away five minutes ago.
                    if time.time() - dbstate['state_updated'] < 300:
                        continue

                    instance.enforced_deletes_increment()
                    attempts = db.get_instance_attribute(
                        instance.uuid, 'enforced_deletes')['count']

                    if attempts > 5:
                        # Sometimes we just can't delete the VM. Try the big
                        # hammer instead.
                        log_ctx.warning(
                            'Attempting alternate delete method for instance')
                        util.execute(None,
                                     'virsh destroy "sf:%s"' % instance_uuid)

                        instance.add_event('enforced delete', 'complete')
                    else:
                        instance.delete()

                    log_ctx.withField('attempt', attempts).warning(
                        'Deleting stray instance')

                    continue

                state = util.extract_power_state(libvirt, domain)
                instance.update_power_state(state)
                if state == 'crashed':
                    instance.update_instance_state('error')

            # Inactive VMs just have a name, and are powered off
            # in our state system.
            for domain_name in conn.listDefinedDomains():
                if not domain_name.startswith('sf:'):
                    continue

                if domain_name not in seen:
                    instance_uuid = domain_name.split(':')[1]
                    log_ctx = LOG.withInstance(instance_uuid)
                    instance = virt.Instance.from_db(instance_uuid)

                    if not instance:
                        # Instance is SF but not in database. Kill because
                        # unknown.
                        log_ctx.warning('Removing unknown inactive instance')
                        domain = conn.lookupByName(domain_name)
                        domain.undefine()
                        continue

                    dbstate = db.get_instance_attribute(
                        instance.uuid, 'state')
                    if dbstate.get('state') == 'deleted':
                        # NOTE(mikal): a delete might be in-flight in the queue.
                        # We only worry about instances which should have gone
                        # away five minutes ago.
                        if time.time() - instance['state_updated'] < 300:
                            continue

                        domain = conn.lookupByName(domain_name)
                        domain.undefine()
                        log_ctx.info('Detected stray instance')
                        instance.add_event('deleted stray', 'complete')
                        continue

                    instance.place_instance(config.NODE_NAME)

                    dbpowerstate = db.get_instance_attribute(
                        instance.uuid, 'power_state')
                    if not os.path.exists(virt.instance_path(instance.uuid)):
                        # If we're inactive and our files aren't on disk,
                        # we have a problem.
                        log_ctx.info('Detected error state for instance')
                        instance.update_instance_state('error')

                    elif dbpowerstate['power_state'] != 'off':
                        log_ctx.info('Detected power off for instance')
                        instance.update_power_state('off')
                        instance.add_event('detected poweroff', 'complete')

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

        while True:
            # Update power state of all instances on this hypervisor
            LOG.info('Updating power states')
            self._update_power_states()

            # Cleanup soft deleted instances and networks
            delay = config.get('CLEANER_DELAY')

            for i in db.get_stale_instances(delay):
                LOG.withInstance(i['uuid']).info('Hard deleting instance')
                db.hard_delete_instance(i['uuid'])

            for n in db.get_stale_networks(delay):
                LOG.withNetwork(n['uuid']).info('Hard deleting network')
                db.hard_delete_network(n['uuid'])

            for ni in db.get_stale_network_interfaces(delay):
                LOG.withNetworkInterface(
                    ni['uuid']).info('Hard deleting network interface')
                db.hard_delete_network_interface(ni['uuid'])

            # Perform etcd maintenance
            if time.time() - last_compaction > 1800:
                LOG.info('Compacting etcd')
                self._compact_etcd()
                last_compaction = time.time()

            time.sleep(60)
