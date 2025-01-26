from shakenfist_utilities import logs  # noreorder

from shakenfist import blob
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.etcd_schema.operations.agent_op \
    import current_version as ao_current_version
from shakenfist.etcd_schema.operations.agent_op \
    import initial_version as ao_initial_version
from shakenfist.etcd_schema.operations.agent_op import model as ao_model
from shakenfist.etcd_schema.operations.agent_op \
    import object_type as ao_object_type
from shakenfist.operations.baseoperation import BaseOperation


LOG, _ = logs.setup(__name__)


class AgentOperation(BaseOperation):
    object_type = ao_object_type
    initial_version = ao_initial_version
    current_version = ao_current_version

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
    def new(cls, operation_uuid, namespace, instance_uuid, commands):
        ao = ao_model(
            uuid=operation_uuid,
            namespace=namespace,
            instance_uuid=instance_uuid,
            commands=commands,
            version=ao_current_version)

        AgentOperation._db_create(
            operation_uuid, ao.model_dump(mode='json', by_alias=True))
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
        db_data = self._db_get_attribute('results')
        if not db_data:
            return {}
        return db_data.get('results', {})

    def add_result(self, index, value):
        if 'command' in value:
            del value['command']
        if 'unique' in value:
            del value['unique']

        with self.get_lock_attr('results', op='add result'):
            results = self.results
            results[str(index)] = value
            self._db_set_attribute('results', {'results': results})

    def delete(self):
        for result in self._db_get_attribute('results'):
            for key in result:
                if key.endswith('_blob'):
                    b = blob.Blob.from_db(result[key], suppress_failure_audit=True)
                    if b:
                        b.ref_count_dec(self)

        self.state = self.STATE_DELETED


class AgentOperations(dbo_iter):
    base_object = AgentOperation

    def __iter__(self):
        for _, o in self.get_iterator():
            operation_uuid = o.get('uuid')
            o = AgentOperation.from_db(
                operation_uuid, suppress_failure_audit=self.suppress_failure_audit)
            if not o:
                continue

            out = self.apply_filters(o)
            if out:
                yield out


def instance_filter(instance, o):
    return o.instance_uuid == instance.uuid
