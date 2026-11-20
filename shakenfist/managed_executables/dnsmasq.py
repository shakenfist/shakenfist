import os
import shlex
import signal
import sys
import time
from typing import Any
from typing import Optional
from uuid import UUID

from shakenfist import eventlog
from shakenfist import instance
from shakenfist import mariadb
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.exceptions import DHCPProbeFailed
from shakenfist.exceptions import DHCPProbeLoopbackDown
from shakenfist.exceptions import NatOnlyNetworksShouldNotHaveDnsMasq
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.managed_executables import managedexecutable
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import callstack as util_callstack
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import dhcp_probe
from shakenfist.util import network as util_network


class DnsMasq(managedexecutable.ManagedExecutable):
    # Note that this slightly confusing object type is required for historical
    # reasons so that objects and config files don't need to be renamed on
    # upgrade.
    object_type = ObjectType.DHCP
    initial_version = 1
    current_version = 4

    def __init__(self, data: DnsMasqData) -> None:
        # Apply lazy upgrades to the immutable Pydantic model if needed
        data = self.upgrade_pydantic_data(data, DnsMasqData)

        # Initialize base class with the Pydantic model
        super().__init__(data)

        # Store DnsMasq-specific fields
        self.__provide_dhcp: bool = data.provide_dhcp
        self.__provide_dns: bool = data.provide_dns

        # Runtime state (not persisted)
        self.__interface: Optional[str] = None
        self.__network: Any = None
        self._templates_initialized: bool = False

    @classmethod
    def _upgrade_step_1_to_2(cls, static_values: dict[str, Any]) -> None:
        static_values['provide_dhcp'] = True
        static_values['provide_dns'] = False

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values: dict[str, Any]) -> None:
        ...

    @classmethod
    def _upgrade_step_3_to_4(cls, static_values: dict[str, Any]) -> None:
        # Converts owner_type from a string to ObjectType enum if needed.
        owner_type = static_values.get('owner_type')
        if isinstance(owner_type, str):
            static_values['owner_type'] = ObjectType(owner_type)  # type: ignore[call-arg]

    @classmethod
    def _persist_pydantic_upgrade(  # type: ignore[override]
            cls, data: DnsMasqData) -> None:
        """Persist an upgraded DnsMasqData to MariaDB."""
        mariadb.update_dnsmasq(data)

    @classmethod
    def _db_create(cls, object_uuid: str, metadata: dict[str, Any]) -> None:
        """Create a DnsMasq record in MariaDB instead of etcd."""
        # Convert owner_type to ObjectType if it's a string
        owner_type = metadata['owner_type']
        if isinstance(owner_type, str):
            owner_type = ObjectType(owner_type)  # type: ignore[call-arg]

        data = DnsMasqData(
            uuid=object_uuid,  # type: ignore[arg-type]
            namespace=metadata['namespace'],
            owner_type=owner_type,
            owner_uuid=metadata['owner_uuid'],
            version=metadata['version'],
            provide_dhcp=metadata['provide_dhcp'],
            provide_dns=metadata['provide_dns']
        )
        if not mariadb.create_dnsmasq(data):
            raise RuntimeError(f'Failed to create dnsmasq {object_uuid} in MariaDB')
        super()._db_create(object_uuid, metadata)

    @classmethod
    def _db_get(cls, object_uuid: UUID) -> Optional[DnsMasqData]:
        """Get DnsMasq static values from MariaDB instead of etcd."""
        data = mariadb.get_dnsmasq(object_uuid)
        if not data:
            return None

        if data.version != cls.current_version:
            if not cls.upgrade_supported:
                from shakenfist import exceptions
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {data}')
        return data

    @classmethod
    def from_db(cls, object_uuid: UUID,
                suppress_failure_audit: bool = False) -> Optional['DnsMasq']:
        """Load a DnsMasq from the database.

        Override the base class from_db because _db_get returns a Pydantic
        DnsMasqData model, not a dictionary. The base class from_db uses
        dict methods (get, in) that don't work with Pydantic models.
        """
        if not object_uuid:
            return None

        data = cls._db_get(object_uuid)
        if not data:
            if not suppress_failure_audit:
                eventlog.add_event(
                    EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                    'attempt to lookup non-existent object',
                    extra={'caller': util_callstack.get_caller(offset=-3)})
            return None

        return cls(data)

    # Static values (runtime, not persisted)
    @property
    def interface(self) -> Optional[str]:
        return self.__interface

    @interface.setter
    def interface(self, value: Optional[str]) -> None:
        self.__interface = value

    @property
    def network(self) -> Any:
        return self.__network

    @network.setter
    def network(self, value: Any) -> None:
        self.__network = value

    # Static values (from database)
    @property
    def provide_dhcp(self) -> bool:
        return self.__provide_dhcp

    @property
    def provide_dns(self) -> bool:
        return self.__provide_dns

    # Helpers
    @classmethod
    def new(cls, owner_network: Any, provide_dhcp: bool = True,
            provide_nat: bool = True, provide_dns: bool = False) -> 'DnsMasq':
        if not provide_dhcp and not provide_dns:
            raise NatOnlyNetworksShouldNotHaveDnsMasq()

        u = owner_network.uuid
        n = cls.from_db(u, suppress_failure_audit=True)
        if n:
            n.interface = owner_network._vx_veth_inner
            n.network = owner_network
            return n

        uniq = owner_network.unique_label()
        cls._db_create(str(u), {
            'uuid': str(u),
            'namespace': owner_network.namespace,
            'owner_type': uniq[0],
            'owner_uuid': str(uniq[1]),
            'provide_dhcp': provide_dhcp,
            'provide_nat': provide_nat,
            'provide_dns': provide_dns,
            'version': cls.current_version
        })
        n = cls.from_db(u)
        if n is None:
            raise RuntimeError(f'Failed to create DnsMasq for {u}')
        n.state = cls.STATE_CREATED  # type: ignore[misc]
        n.interface = owner_network._vx_veth_inner
        n.network = owner_network
        return n

    def subst_dict(self) -> dict[str, Any]:
        instances, _ = self._enumerate_leases()

        # NOTE(mikal): provide_nat comes from the network subst dictionary, not
        # the dnsmasq one.
        d = super().subst_dict()
        d.update({
            'zone': config.ZONE,
            'dns_server': config.DNS_SERVER,
            'mtu': config.MAX_HYPERVISOR_MTU - 50,
            'interface': self.interface,
            'instances': instances,
            'provide_dhcp': self.provide_dhcp,
            'provide_dns': self.provide_dns
        })
        d.update(self.network.subst_dict())
        return d

    def is_serving_dhcp(self) -> bool:
        """Is this dnsmasq still answering DHCP, not merely alive?

        ``is_running()`` reads the pid file and asks whether that
        process exists, so a dnsmasq which is alive but wedged passes
        it and the network looks healthy while its guests cannot get a
        lease (issue 730).

        Sends a DHCPINFORM from inside the network's namespace, which
        is where the server's address exists. A DHCPINFORM is answered
        for a client the server has never heard of and allocates
        nothing, which is what makes it safe to run on every
        maintenance pass -- see shakenfist/util/dhcp_probe.py for the
        measurements behind both claims.

        Answers True when this dnsmasq does not serve DHCP at all:
        there is no readiness to check, and reporting a DNS-only
        network as unhealthy would have it rebuilt on every pass.

        False only when the probe ran and nothing answered. A probe
        which could not be run -- privexec unreachable, the module not
        importable, anything but the probe's own "no answer" status --
        raises DHCPProbeFailed instead, because it says nothing about
        dnsmasq and must not get a healthy network rebuilt. A down
        loopback in the namespace raises DHCPProbeLoopbackDown, the
        subclass for the one such reason the caller can repair.
        """
        if not self.provide_dhcp:
            return True

        # Run by this process's own interpreter. privexec runs commands
        # with a system PATH, on which python3 has no shakenfist to
        # import, and that import failure was read as "no answer" for
        # every network on every pass. -P keeps the inherited working
        # directory off sys.path: this runs as root, and -m would
        # otherwise import a socket.py planted there ahead of the real
        # one.
        #
        # The router address and namespace are read from the network
        # directly. subst_dict() would enumerate every lease on the
        # network -- an interface query and an instance read each --
        # on every pass, to use none of it.
        router = self.network.router
        command = shlex.join([
            sys.executable, '-P', '-m', 'shakenfist.util.dhcp_probe',
            router, router, str(config.DHCP_PROBE_TIMEOUT)])
        try:
            util_concurrency.execute(
                command, netns=str(self.network.uuid),
                suppress_command_logging=True)
        except ProcessExecutionError as e:
            if e.exit_code == dhcp_probe.EXIT_NO_ANSWER:
                return False
            stderr = (e.stderr or '').strip()[-200:]
            if e.exit_code == dhcp_probe.EXIT_LOOPBACK_DOWN:
                raise DHCPProbeLoopbackDown(
                    f'the DHCP probe exited {e.exit_code}: {stderr}') from e
            raise DHCPProbeFailed(
                f'the DHCP probe exited {e.exit_code}: {stderr}') from e
        except util_concurrency.PRIVEXEC_TRANSPORT_ERRORS as e:
            # Only a privexec which did not answer. Anything else -- a
            # renamed network attribute, a protocol bug -- is a defect in
            # this code, and read as "could not probe" it would report
            # every network as serving DHCP forever, which is the silent
            # failure this check exists to remove.
            raise DHCPProbeFailed(
                f'the DHCP probe could not be run: '
                f'{e.__class__.__name__}: {e}') from e
        return True

    def _read_templates(self) -> None:
        if not self._templates_initialized:
            self._read_template('config', 'dhcp.tmpl')
            if self.provide_dhcp:
                self._read_template('hosts', 'dhcphosts.tmpl')
            if self.provide_dns:
                self._read_template('dnshosts', 'dnshosts.tmpl')
            self._templates_initialized = True

    def _enumerate_leases(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        instances: list[dict[str, Any]] = []
        allowed_leases: dict[str, str] = {}

        for ni in self.network.networkinterfaces:
            inst = instance.Instance.from_db(ni.instance_uuid)
            if not inst:
                continue

            instances.append(
                {
                    'uuid': ni.instance_uuid,
                    'macaddr': ni.macaddr,
                    'ipv4': ni.ipv4,
                    'name': inst.name.replace(',', '')
                })
            allowed_leases[ni.macaddr] = ni.ipv4

        return instances, allowed_leases

    def _remove_invalid_leases(
            self, allowed_leases: dict[str, str]) -> bool:
        lf = os.path.join(self.config_directory, 'leases')
        if not os.path.exists(lf):
            return False

        needs_restart = False
        with open(lf) as lin, open(lf + '.new', 'w') as lout:
            for line in lin.readlines():
                # 1672899136 02:00:00:55:04:a2 172.10.0.8 client *
                # ^--expiry  ^--mac            ^--ip      ^-- hostname
                elems = line.split(' ')
                expiry = int(elems[0])

                # The lease is expired, so we don't care
                if time.time() > expiry:
                    lout.write(line)
                    continue

                # The lease is valid, so keep it
                if elems[1] in allowed_leases:
                    lout.write(line)
                    continue

                # Otherwise, this lease is invalid and we'll need to do a
                # hard restart
                needs_restart = True
                self.add_event(EVENT_TYPE_AUDIT, 'detected invalid DHCP lease',
                               extra={
                                   'expiry': expiry,
                                   'remaining_life': round(time.time() - expiry, 2),
                                   'macaddr': elems[1],
                                   'ipv4': elems[2],
                                   'hostname': elems[3]
                               })

        return needs_restart

    def remove_lease(self, ipv4: str, macaddr: str) -> None:
        subst = self.subst_dict()
        subst.update({
            'ipv4': ipv4,
            'macaddr': macaddr
        })

        # dhcp_release runs inside the network's namespace, and a lease removal
        # can arrive when that namespace is not there: enqueued before a host
        # reboot and dequeued after it, before sf-net has rebuilt it. That is
        # the post-reboot half of the race
        # BridgedVXLanNetwork._apply_unroute_address() already tolerates, and
        # the same reasoning applies -- the end state this method exists to
        # reach is "the lease is not held", and a namespace with no dnsmasq in
        # it already satisfies that. Untolerated, the ProcessExecutionError
        # reaches net_macaddr_ip_op.dispatch_task(), which writes a traceback
        # to syslog and records a cluster operation error for a benign startup
        # race, and makes the lease removal an instance delete waits on fail
        # with NetworkOperationFailed (#4921). The guard sits where the
        # exception is raised rather than on the os.path.exists() pre-check
        # restart() uses, because the namespace can also go away between such
        # a check and the exec. ip netns exec reports a missing namespace with
        # an exit code of its own, so a dhcp_release which failed for any
        # other reason still raises.
        try:
            util_concurrency.execute(
                'dhcp_release %(interface)s %(ipv4)s %(macaddr)s' % subst,
                netns=self.network.uuid)
        except ProcessExecutionError as e:
            if e.exit_code != util_network.NETNS_MISSING_EXIT_CODE:
                raise
            self.add_event(
                EVENT_TYPE_AUDIT,
                'namespace absent while releasing a DHCP lease, '
                'nothing to remove',
                extra={
                    'macaddr': macaddr,
                    'ipv4': ipv4
                })
            return

        self.add_event(EVENT_TYPE_AUDIT, 'released a DHCP lease',
                       extra={
                           'macaddr': macaddr,
                           'ipv4': ipv4
                       })

    def restart(self) -> None:
        if not os.path.exists('/var/run/netns/%s' % str(self.network.uuid)):
            return

        _, allowed_leases = self._enumerate_leases()
        needs_start = False

        self._read_templates()
        self._make_config()

        if self._remove_invalid_leases(allowed_leases):
            # We found invalid leases and need to do a hard restart of dnsmasq
            self._send_signal(signal.SIGKILL)
            leases_file = os.path.join(self.config_directory, 'leases')
            os.unlink(leases_file)
            os.rename(leases_file + '.new', leases_file)
            needs_start = True

        elif not self._send_signal(signal.SIGHUP):
            # We failed to find a PID to SIGHUP and therefore must start
            # dnsmasq
            needs_start = True

        if needs_start:
            util_concurrency.execute(
                'dnsmasq --conf-file=%s/config' % self.config_directory,
                netns=self.network.uuid)
            self.add_event(EVENT_TYPE_AUDIT, 'started')
