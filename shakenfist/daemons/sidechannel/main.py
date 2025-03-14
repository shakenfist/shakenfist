import base64
import os
import socket
import threading
import time
import uuid

from shakenfist_utilities import random as sf_random        # noreorder
from shakenfist_utilities import logs                       # noreorder

from shakenfist import blob
from shakenfist import constants
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist.exceptions import NoSuchChannel
from shakenfist import instance
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.protos import agent_pb2
from shakenfist.protos import common_pb2
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


# This is the minimum version of the in-guest agent that we support. This
# generally gets bumped when the protocol changes.
MINIMUM_AGENT_VERSION = '0.3.16'


class ConnectionFailed(Exception):
    ...


class PutException(Exception):
    ...


class SideChannelJob(util_concurrency.Job):
    def __init__(self, inst):
        super().__init__()
        self.instance = inst
        self.abort_path = f'/run/sf/sidechannel-{inst.uuid}.abort'
        self.log = LOG.with_fields({'instance': self.instance.uuid})

    def _record_system_boot_time(self, sbt):
        if sbt != self.system_boot_time:
            if self.system_boot_time != 0:
                self.instance.add_event(EVENT_TYPE_AUDIT, 'reboot detected')
            self.system_boot_time = sbt
            self.instance.agent_system_boot_time = sbt

    def execute(self):
        etcd.reset_client()
        util_concurrency.set_thread_name(self.instance.uuid)
        self.log.debug('Attempt channel connection')

        self.instance_ready = constants.AGENT_NEVER_TALKED
        self.instance.agent_state = constants.AGENT_NEVER_TALKED
        self.system_boot_time = 0
        self.last_data = time.time()


        # We use the existence of a console.log file in the instance directory
        # to indicate the instance has been created. This will be true even if
        # the instance doesn't actually every write to the serial console.
        console_path = os.path.join(self.instance.instance_path, 'console.log')
        while not os.path.exists(console_path):
            time.sleep(1)
        self.log.debug('Detected console log')

        # Attempt to connect to the agent
        try:
            with self.instance.socket_on_vsock_channel('sf-agent2') as vsock:
                vsock.sock.settimeout(1)
                self.instance.add_event(EVENT_TYPE_STATUS, 'connected to agent')

                request = agent_pb2.AgentRequestCommand(
                    command_id=sf_random.random_id(),
                    hypervisor_welcome=agent_pb2.HypervisorWelcome(
                        version=util_general.get_version()
                    )
                )
                vsock.sock.sendall(request.SerializeToString())
                last_traffic = time.time()

                buffered = bytearray()
                while daemon.check_abort_path(self.abort_path):
                    if time.time() - last_traffic > 2:
                        request = agent_pb2.AgentRequestCommand(
                            command_id=sf_random.random_id(),
                            ping_request=agent_pb2.PingRequest(
                            )
                        )
                        vsock.sock.sendall(request.SerializeToString())
                        last_traffic = time.time()
                        self.log.debug('Ping request')

                    try:
                        input = vsock.sock.recv(102400)
                        if not input:
                            return

                        last_traffic = time.time()
                        buffered += input

                        reply = agent_pb2.AgentReplyCommand()
                        consumed = reply.ParseFromString(buffered)
                        if consumed == 0:
                            continue
                        buffered = buffered[consumed:]

                        if reply.HasField('agent_welcome'):
                            response = reply.agent_welcome
                            self.instance.add_event(
                                EVENT_TYPE_STATUS, 'agent metrics',
                                extra={
                                    'version': response.version,
                                    'boot_time': response.boot_time
                                })
                            self._record_system_boot_time(response.boot_time)

                        elif reply.HasField('ping_reply'):
                            self.log.debug('Ping reply')

                    except socket.timeout:
                        ...

        except NoSuchChannel:
            self.log.debug('No such channel')

        except OSError as e:
            self.log.debug(f'OSError: {e}')


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super().__init__(name)
        self.monitors = {}

    def reap_single_instance_monitors(self):
        all_monitors = list(self.monitors.keys())
        for instance_uuid in all_monitors:
            t = self.monitors[instance_uuid]['thread']
            if not t.is_alive():
                t.join(1)
                LOG.info(
                    f'Reaped dead side channel monitor with ident {t.ident}')
                eventlog.add_event(
                    EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                    'side channel monitor ended')
                del self.monitors[instance_uuid]

    def _request_all_threads_exit(self):
        LOG.info('Requesting all threads exit')
        all_monitors = self.monitors.keys()
        for instance_uuid in all_monitors:
            self._request_thread_exit(instance_uuid)

    def _request_thread_exit(self, instance_uuid):
        t = self.monitors[instance_uuid]
        t['object'].exit.set()
        eventlog.add_event(
            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
            'side channel monitor instructed to exit')
        self.monitors[instance_uuid]['thread'].join(0.5)

        if not t['thread'].is_alive():
            del self.monitors[instance_uuid]
            eventlog.add_event(
                EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                'side channel monitor finished')

    def _run_inner(self):
        instance_sidechannel_cache = {}

        while daemon.check_abort_path(self.abort_path):
            try:
                while not daemon.health_check_nodelock():
                    LOG.info('Waiting for nodelock daemon to be healthy')
                    time.sleep(1)
                    continue

                self.reap_single_instance_monitors()

                if not os.path.exists(self.abort_path):
                    # Audit desired self.monitors
                    extra_instances = list(self.monitors.keys())
                    missing_instances = []

                    # The goal here is to find all instances running on this node so
                    # that we can monitor them. We used to query etcd for this, but
                    # we needed to do so frequently and it created a lot of etcd load.
                    # We also can't use the existence of instance folders (which once
                    # seemed like a good idea at the time), because some instances might
                    # also be powered off. Instead, we ask libvirt what domains are
                    # running.
                    with util_libvirt.LibvirtConnection() as lc:
                        for domain in lc.get_sf_domains():
                            state = lc.extract_power_state(domain)
                            if state in ['off', 'crashed', 'paused']:
                                # If the domain isn't running, it shouldn't have a
                                # sidechannel monitor.
                                continue

                            instance_uuid = domain.name().split(':')[1]
                            if instance_uuid in extra_instances:
                                extra_instances.remove(instance_uuid)
                            if instance_uuid not in self.monitors:
                                missing_instances.append(instance_uuid)

                    # Start missing monitors. We only support sf-agent2 for now.
                    for instance_uuid in missing_instances:
                        inst = instance.Instance.from_db(instance_uuid)
                        if not inst:
                            continue

                        if instance_uuid not in instance_sidechannel_cache:
                            instance_sidechannel_cache[instance_uuid] = inst.side_channels

                        if 'sf-agent2' not in instance_sidechannel_cache[instance_uuid]:
                            continue
                        if inst.state.value == instance.Instance.STATE_DELETED:
                            continue
                        if not inst.vsock_cid('sf-agent2'):
                            continue

                        sc_obj = SideChannelJob(inst)
                        sc_thread = threading.Thread(
                            target=sc_obj.run, daemon=True, name=instance_uuid)
                        sc_thread.start()

                        self.monitors[instance_uuid] = {
                            'object': sc_obj,
                            'thread': sc_thread,
                            'instance_uuid': instance_uuid
                        }
                        eventlog.add_event(
                            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                            'side channel monitor started')

                    # Cleanup extra monitors
                    for instance_uuid in extra_instances:
                        self._request_thread_exit(instance_uuid)

                    self.idle(1)

            except Exception as e:
                util_general.ignore_exception('side channel monitor', e)

        LOG.info('Stopping')

        while self.monitors:
            LOG.info('There are {len(self.monitors)} threads remaining')
            self._request_all_threads_exit()
            if self(self.monitors):
                time.sleep(5)

        LOG.info('There are {len(self.monitors)} threads remaining')
        LOG.info('Stopped')


def main():
    daemon.write_pid_file('sidechannel')
    m = Monitor('sidechannel')

    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    m.run()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    LOG.info('Terminating ourselves')
    raise SystemExit(0)
