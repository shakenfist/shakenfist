import faulthandler
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist import network
from shakenfist.daemons.network import floating_ip_reaper
from shakenfist.daemons.network import maintain
from shakenfist.daemons.network import mtus
from shakenfist.daemons.network import stray_nics
from shakenfist.daemons.network import workitem
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.util import general as util_general
from shakenfist.util import network as util_network
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


class Monitor(daemon.WorkerPoolDaemon):
    def run(self):
        LOG.info('Starting')
        running = True
        shutdown_commenced = None
        last_defer_message = 0

        self.workers = {}
        job_classes = {
            'fip-reaper': floating_ip_reaper.Job,
            'maintain': maintain.Job,
            'mtus': mtus.Job,
            'net-worker': workitem.Job,
            'stray-nics': stray_nics.Job
        }

        while True:
            try:
                self.reap_workers()

                if not self.exit.is_set():
                    for job_name in job_classes:
                        needs_start = False
                        if (job_name == 'net-worker' and
                                not config.NODE_IS_NETWORK_NODE):
                            continue

                        if not self.cluster_stable():
                            if time.time() - last_defer_message > 10:
                                LOG.info(
                                    'Cluster not yet stable, deferring maintenance')
                                last_defer_message = time.time()
                            continue

                        if job_name not in self.workers:
                            needs_start = True
                        elif not self.workers[job_name]['thread'].is_alive():
                            needs_start = True
                            self.workers[job_name]['thread'].join(0.2)

                        if needs_start:
                            self.start_job(job_classes[job_name], [], job_name)

                elif len(self.workers) > 0:
                    if running:
                        shutdown_commenced = time.time()
                        for thread_name in self.workers:
                            thread_ident = self.workers[thread_name]['thread'].ident
                            self.workers[thread_name]['object'].exit.set()
                            LOG.info(f'Sent exit event to {thread_name} thread '
                                     f'with ident {thread_ident}')

                        running = False

                    if time.time() - shutdown_commenced > 10:
                        LOG.warning(
                            'We have taken more than ten seconds to shut down')
                        LOG.warning('Dumping thread traces')
                        for thread_name in self.workers:
                            thread_ident = self.workers[thread_name]['thread'].ident
                            LOG.warning(f'{thread_name} thread still running '
                                        f'with ident {thread_ident}')
                        faulthandler.dump_traceback()

                else:
                    break

                self.exit.wait(1)

            except Exception as e:
                util_general.ignore_exception('network worker', e)

        LOG.info('Terminated')


def main():
    # If I am the network node, I need some setup
    if config.NODE_IS_NETWORK_NODE:
        LOG.info('Network node pre-start is running')

        # Bootstrap the floating network in the Networks table
        network.floating_network()
        subst = {
            'egress_bridge': util_network.get_safe_interface_name(
                'egr-br-%s' % config.NODE_EGRESS_NIC),
            'egress_nic': config.NODE_EGRESS_NIC
        }

        if not util_network.check_for_interface(subst['egress_bridge']):
            # NOTE(mikal): Adding the physical interface to the physical bridge
            # is considered outside the scope of the orchestration software as
            # it will cause the node to lose network connectivity. So instead
            # all we do is create a bridge if it doesn't exist and the wire
            # everything up to it. We can do egress NAT in that state, even if
            # floating IPs don't work.
            #
            # No locking as read only
            fn = network.floating_network()
            subst['master_float'] = fn.ipam.get_address_at_index(1)
            subst['netmask'] = fn.netmask

            # We need to copy the MTU of the interface we are bridging to
            # or weird networking things happen.
            mtu = util_network.get_interface_mtu(config.NODE_EGRESS_NIC)

            util_network.create_interface(
                subst['egress_bridge'], 'bridge', '', mtu=mtu)

            util_concurrency.execute(
                None, 'ip link set %(egress_bridge)s up' % subst)
            util_network.add_address_to_interface(
                None, subst['master_float'], subst['netmask'], subst['egress_bridge'])

            util_concurrency.execute(
                None,
                'iptables -w 10 -A FORWARD -o %(egress_nic)s '
                '-i %(egress_bridge)s -j ACCEPT' % subst)
            util_concurrency.execute(
                None,
                'iptables -w 10 -A FORWARD -i %(egress_nic)s '
                '-o %(egress_bridge)s -j ACCEPT' % subst)
            util_concurrency.execute(
                None,
                'iptables -w 10 -t nat -A POSTROUTING '
                '-o %(egress_nic)s -j MASQUERADE' % subst)

    m = Monitor('net')
    m.run()
