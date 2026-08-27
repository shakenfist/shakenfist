import os
import signal
import time
from typing import Any
from typing import Optional
from uuid import UUID

from shakenfist import eventlog
from shakenfist import instance
from shakenfist import mariadb
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.exceptions import NatOnlyNetworksShouldNotHaveDnsMasq
from shakenfist.managed_executables import managedexecutable
from shakenfist.schema.dnsmasq import DnsMasqData
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import callstack as util_callstack
from shakenfist.util import concurrency as util_concurrency


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

    def hard_delete(self) -> None:
        mariadb.delete_dnsmasq(self.uuid)
        super().hard_delete()

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
        util_concurrency.execute(
            'dhcp_release %(interface)s %(ipv4)s %(macaddr)s' % subst,
            netns=self.network.uuid)
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
