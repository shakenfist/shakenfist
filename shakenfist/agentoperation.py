from shakenfist_utilities import logs

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist import blob


LOG, _ = logs.setup(__name__)


class AgentOperation(dbo):
    object_type = 'agentoperation'
    initial_version = 1
    current_version = 1

    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_QUEUED = 'queued'
    STATE_PREFLIGHT = 'preflight'
    STATE_EXECUTING = 'executing'
    STATE_COMPLETE = 'complete'

    ACTIVE_STATES = {dbo.STATE_CREATED, STATE_QUEUED, STATE_EXECUTING, STATE_COMPLETE}

    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (STATE_PREFLIGHT, STATE_QUEUED, dbo.STATE_DELETED,
                            dbo.STATE_ERROR),
        STATE_PREFLIGHT: (STATE_QUEUED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_QUEUED: (STATE_EXECUTING, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_EXECUTING: (STATE_COMPLETE, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_COMPLETE: (dbo.STATE_DELETED),
        dbo.STATE_ERROR: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values['uuid'],
                                             static_values.get('version'))

        self.__namespace = static_values['namespace']
        self.__instance_uuid = static_values['instance_uuid']
        self.__commands = static_values['commands']

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
