import etcd3
import json
import os
import time

from shakenfist import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import logutil
from shakenfist import net
from shakenfist import util
from shakenfist import virt


class Monitor(daemon.Daemon):
    def _update_power_states(self):
        libvirt = util.get_libvirt()
        conn = libvirt.open(None)
        try:
            seen = []

            # Active VMs have an ID. Active means running in libvirt
            # land.
            for domain_id in conn.listDomainsID():
                domain = conn.lookupByID(domain_id)
                if not domain.name().startswith('sf:'):
                    continue

                instance_uuid = domain.name().split(':')[1]
                instance = db.get_instance(instance_uuid)
                if not instance:
                    # Instance is SF but not in database. Kill to reduce load.
                    logutil.warning([virt.ThinInstance(instance_uuid)],
                                    'Destroying unknown instance')
                    util.execute(None,
                                 'virsh destroy "sf:%s"' % instance_uuid)
                    continue

                db.place_instance(
                    instance_uuid, config.parsed.get('NODE_NAME'))
                seen.append(domain.name())

                if instance.get('state') == 'deleted':
                    # NOTE(mikal): a delete might be in-flight in the queue.
                    # We only worry about instances which should have gone
                    # away five minutes ago.
                    if time.time() - instance['state_updated'] < 300:
                        continue

                    db.instance_enforced_deletes_increment(instance_uuid)
                    attempts = instance.get('enforced_deletes', 0)

                    if attempts > 5:
                        # Sometimes we just can't delete the VM. Try the big hammer instead.
                        logutil.warning([virt.ThinInstance(instance_uuid)],
                                        'Attempting alternate delete method for instance')
                        util.execute(None,
                                     'virsh destroy "sf:%s"' % instance_uuid)

                        db.add_event('instance', instance_uuid,
                                     'enforced delete', 'complete', None, None)
                    else:
                        i = virt.from_db(instance_uuid)
                        i.delete()
                        i.update_instance_state('deleted')

                    logutil.warning(
                        [virt.ThinInstance(instance_uuid)],
                        'Deleting stray instance (attempt %d)' % attempts)

                    continue

                state = util.extract_power_state(libvirt, domain)
                db.update_instance_power_state(instance_uuid, state)
                if state == 'crashed':
                    db.update_instance_state(instance_uuid, 'error')

            # Inactive VMs just have a name, and are powered off
            # in our state system.
            for domain_name in conn.listDefinedDomains():
                if not domain_name.startswith('sf:'):
                    continue

                if domain_name not in seen:
                    instance_uuid = domain_name.split(':')[1]
                    instance = db.get_instance(instance_uuid)

                    if instance.get('state') == 'deleted':
                        # NOTE(mikal): a delete might be in-flight in the queue.
                        # We only worry about instances which should have gone
                        # away five minutes ago.
                        if time.time() - instance['state_updated'] < 300:
                            continue

                        domain = conn.lookupByName(domain_name)
                        domain.undefine()
                        logutil.info(
                            [virt.ThinInstance(instance_uuid)], 'Detected stray instance')
                        db.add_event('instance', instance_uuid,
                                     'deleted stray', 'complete', None, None)
                        continue

                    db.place_instance(
                        instance_uuid, config.parsed.get('NODE_NAME'))
                    instance_path = os.path.join(
                        config.parsed.get('STORAGE_PATH'), 'instances',
                        instance_uuid)

                    if not os.path.exists(instance_path):
                        # If we're inactive and our files aren't on disk,
                        # we have a problem.
                        logutil.info([virt.ThinInstance(instance_uuid)],
                                     'Detected error state for instance')
                        db.update_instance_state(instance_uuid, 'error')
                    elif instance.get('power_state') != 'off':
                        logutil.info([virt.ThinInstance(instance_uuid)],
                                     'Detected power off for instance')
                        db.update_instance_power_state(
                            instance_uuid, 'off')
                        db.add_event('instance', instance_uuid,
                                     'detected poweroff', 'complete', None, None)

        except libvirt.libvirtError as e:
            logutil.error(None, 'Failed to lookup all domains: %s' % e)

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
            logutil.info(None, 'Compacted etcd')

        except Exception as e:
            util.ignore_exception('etcd compaction', e)

    def run(self):
        logutil.info(None, 'Starting')
        last_compaction = 0

        while True:
            # Update power state of all instances on this hypervisor
            logutil.info(None, 'Updating power states')
            self._update_power_states()

            # Cleanup soft deleted instances and networks
            delay = config.parsed.get('CLEANER_DELAY')

            for i in db.get_stale_instances(delay):
                logutil.info([virt.ThinInstance(i['uuid'])],
                             'Hard deleting instance')
                db.hard_delete_instance(i['uuid'])

            for n in db.get_stale_networks(delay):
                logutil.info([net.ThinNetwork(n['uuid'])],
                             'Hard deleting network')
                db.hard_delete_network(n['uuid'])

            for ni in db.get_stale_network_interfaces(delay):
                logutil.info([net.ThinNetworkInterface(ni['uuid'])],
                             'Hard deleting network interface')
                db.hard_delete_network_interface(ni['uuid'])

            # Perform etcd maintenance
            if time.time() - last_compaction > 1800:
                logutil.info(None, 'Compacting etcd')
                self._compact_etcd()
                last_compaction = time.time()

            time.sleep(60)
