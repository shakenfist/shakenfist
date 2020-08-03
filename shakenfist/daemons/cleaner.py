import etcd3
import json
import logging
from logging import handlers as logging_handlers
import os
import setproctitle
import time

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist import db
from shakenfist import util
from shakenfist import virt


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf cleaner')

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
                db.place_instance(
                    instance_uuid, config.parsed.get('NODE_NAME'))
                seen.append(domain.name())

                if instance.get('state') == 'deleted':
                    db.instance_enforced_deletes_increment(instance_uuid)
                    attempts = instance.get('enforced_deletes', 0)
                    LOG.warning('Deleting stray instance %s (attempt %d)'
                                % (instance_uuid, attempts))

                    if attempts > 5:
                        # Sometimes we just can't delete the VM. Try the big hammer instead.
                        LOG.warning('Attempting alternate delete method for instance %s'
                                    % instance_uuid)
                        processutils.execute(
                            'virsh destroy "sf:%s"' % instance_uuid, shell=True)

                        db.add_event('instance', instance_uuid,
                                     'enforced delete', 'complete', None, None)
                    else:
                        i = virt.from_db(instance_uuid)
                        i.delete()

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
                        domain = conn.lookupByName(domain_name)
                        domain.undefine()
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
                        db.update_instance_state(instance_uuid, 'error')
                    elif instance.get('powerstate') != 'off':
                        db.update_instance_power_state(
                            instance_uuid, 'off')
                        db.add_event('instance', instance_uuid,
                                     'detected poweroff', 'complete', None, None)

        except libvirt.libvirtError as e:
            LOG.error('Failed to lookup all domains: %s' % e)

    def _compact_etcd(self):
        try:
            # We need to determine what revision to compact to, so we keep a
            # key which stores when we last compacted and we use it's latest
            # revision number as the revision to compact to.
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
        last_compaction = 0

        while True:
            # Update power state of all instances on this hypervisor
            self._update_power_states()

            # Cleanup soft deleted instances and networks
            delay = config.parsed.get('CLEANER_DELAY')

            for i in db.get_stale_instances(delay):
                LOG.info('Hard deleting instance %s' % i['uuid'])
                db.hard_delete_instance(i['uuid'])

            for n in db.get_stale_networks(delay):
                LOG.info('Hard deleting network %s' % n['uuid'])
                db.hard_delete_network(n['uuid'])

            for ni in db.get_stale_network_interfaces(delay):
                LOG.info('Hard deleting network interface %s' % ni['uuid'])
                db.hard_delete_network_interface(ni['uuid'])

            # Perform etcd maintenance
            if time.time() - last_compaction > 1800:
                self._compact_etcd()
                last_compaction = time.time()

            time.sleep(60)
