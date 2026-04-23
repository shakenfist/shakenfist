from typing import Any, Optional
from uuid import UUID

from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.operations.baseoperation import BaseOperation
from shakenfist.schema.agentoperation_attributes import AgentOperationAttributesData
from shakenfist.schema.agentoperation_data import AgentOperationData
from shakenfist.schema.object_reference import references_to_grouped_dict
from shakenfist.schema.object_types import ObjectType


LOG, _ = logs.setup(__name__)


class AgentOperation(BaseOperation):
    object_type = ObjectType.AGENTOPERATION
    initial_version = 2
    current_version = 3

    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (BaseOperation.STATE_PREFLIGHT,
                            BaseOperation.STATE_QUEUED, dbo.STATE_DELETED,
                            dbo.STATE_ERROR),
        BaseOperation.STATE_PREFLIGHT: (BaseOperation.STATE_QUEUED,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR),
        BaseOperation.STATE_QUEUED: (BaseOperation.STATE_EXECUTING,
                                     dbo.STATE_DELETED, dbo.STATE_ERROR),
        BaseOperation.STATE_EXECUTING: (BaseOperation.STATE_COMPLETE,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR),
        BaseOperation.STATE_COMPLETE: (dbo.STATE_DELETED),
        dbo.STATE_ERROR: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values['uuid'], static_values.get('version'))

        self.__namespace = static_values['namespace']
        self.__instance_uuid = static_values['instance_uuid']
        self.__commands = static_values['commands']

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values: dict[str, Any]) -> None:
        # Static values migration to MariaDB is handled by the
        # database daemon data migrations.
        ...

    @classmethod
    def _db_create(cls, object_uuid: str, metadata: dict[str, Any]) -> None:
        """Create an AgentOperation record in both etcd and MariaDB."""
        # Write to etcd (base class behavior)
        super()._db_create(object_uuid, metadata)

        # Also write static values to MariaDB
        _uuid = object_uuid if isinstance(object_uuid, UUID) else UUID(object_uuid)
        _instance_uuid = metadata['instance_uuid']
        if not isinstance(_instance_uuid, UUID):
            _instance_uuid = UUID(_instance_uuid)

        data = AgentOperationData(
            uuid=_uuid,
            namespace=metadata.get('namespace', ''),
            instance_uuid=_instance_uuid,
            commands=metadata.get('commands', []),
            version=metadata['version']
        )
        mariadb.create_agent_operation(data)

        # Create initial attributes record
        attrs = AgentOperationAttributesData(
            uuid=_uuid,
            results={},
        )
        mariadb.create_agent_operation_attributes(attrs)

    @classmethod
    def _db_get(cls, object_uuid: str) -> Optional[dict]:
        """Get AgentOperation static values, trying MariaDB first."""
        if not isinstance(object_uuid, UUID):
            object_uuid = UUID(object_uuid)
        data = mariadb.get_agent_operation(object_uuid)
        if data:
            result = {
                'uuid': str(data.uuid),
                'namespace': data.namespace,
                'instance_uuid': str(data.instance_uuid),
                'commands': data.commands,
                'version': data.version
            }
            if result.get('version', 0) != cls.current_version:
                if not cls.upgrade_supported:
                    from shakenfist import exceptions
                    raise exceptions.BadObjectVersion(
                        f'Unsupported object version - {cls.object_type}: {result}')
            return result

        # Object not found in MariaDB
        return None

    @classmethod
    def new(cls, operation_uuid, namespace, instance_uuid, commands):
        AgentOperation._db_create(operation_uuid, {
            'uuid': operation_uuid,
            'namespace': namespace,
            'instance_uuid': instance_uuid,
            'commands': commands,
            'version': cls.current_version
        })
        o = AgentOperation.from_db(operation_uuid)
        o.state = cls.STATE_INITIAL
        return o

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        retval = self._external_view()
        retval.update({
            'namespace': self.namespace,
            'instance_uuid': self.instance_uuid,
            'commands': self.commands,
            'results': self.results
        })

        # Add object references (what references this agent operation and what
        # this agent operation references)
        refs_to = mariadb.get_references_to(ObjectType.AGENTOPERATION, self.uuid)
        refs_from = mariadb.get_references_from(
            ObjectType.AGENTOPERATION, self.uuid)
        retval['references_to'] = references_to_grouped_dict(refs_to)
        retval['references_from'] = references_to_grouped_dict(refs_from)

        return retval

    # Static values
    @property
    def namespace(self):
        return self.__namespace

    @property
    def instance_uuid(self):
        return self.__instance_uuid

    @property
    def commands(self):
        return self.__commands

    @property
    def results(self):
        _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
        attrs = mariadb.get_agent_operation_attributes(_uuid)
        if not attrs:
            attrs = AgentOperationAttributesData(uuid=_uuid, results={})
            if not mariadb.create_agent_operation_attributes(attrs):
                # Another thread created the record; re-read it
                attrs = mariadb.get_agent_operation_attributes(_uuid)
        return attrs.results

    def add_result(self, index, value):
        if 'command' in value:
            del value['command']
        if 'unique' in value:
            del value['unique']

        with self.get_lock_attr('results', op='add result'):
            _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
            attrs = mariadb.get_agent_operation_attributes(_uuid)
            if not attrs:
                attrs = AgentOperationAttributesData(uuid=_uuid, results={})
                if not mariadb.create_agent_operation_attributes(attrs):
                    attrs = mariadb.get_agent_operation_attributes(_uuid)

            results = dict(attrs.results)
            results[str(index)] = value
            updated = AgentOperationAttributesData(uuid=_uuid, results=results)
            mariadb.update_agent_operation_attributes(updated)

        self.add_event(EVENT_TYPE_MUTATE, 'add result',
                       extra={'index': str(index)})

    def hard_delete(self):
        _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
        mariadb.delete_agent_operation_attributes(_uuid)
        mariadb.delete_agent_operation(_uuid)
        super().hard_delete()

    def delete(self):
        # Remove MariaDB records
        _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
        mariadb.delete_agent_operation_attributes(_uuid)
        mariadb.delete_agent_operation(_uuid)

        # Remove all blob references from this agent operation
        mariadb.remove_all_references_from(ObjectType.AGENTOPERATION, self.uuid)
        self.state = self.STATE_DELETED


class AgentOperations(dbo_iter):
    base_object = AgentOperation

    def __iter__(self):
        for _, static_values in self.get_iterator():
            operation_uuid = static_values.get('uuid')
            o = AgentOperation.from_db(
                operation_uuid, suppress_failure_audit=self.suppress_failure_audit)
            if not o:
                continue

            out = self.apply_filters(o)
            if out:
                yield out


def instance_filter(instance, o):
    return str(o.instance_uuid) == str(instance.uuid)
