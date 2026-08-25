import time
from typing import Any, Optional
from uuid import UUID

from shakenfist_utilities import logs  # noreorder

from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
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

    # A terminal state meaning a timing budget the caller set -- the
    # wall-clock deadline, or the progress timeout -- was exhausted.
    # This is deliberately distinct from STATE_ERROR, which means the
    # operation itself failed. It lives here rather than on
    # BaseOperation because deadlines are agent operation scoped for
    # now; see the non-goals in
    # docs/plans/PLAN-agent-operation-deadlines.md.
    STATE_EXPIRED = 'expired'

    # Once an operation reaches one of these it has no further
    # transitions worth making, so expire() and fail() are no-ops from
    # them rather than raising InvalidStateException.
    TERMINAL_STATES = (BaseOperation.STATE_COMPLETE, dbo.STATE_ERROR,
                       STATE_EXPIRED, dbo.STATE_DELETED)

    # NOTE(mikal): every value here is a tuple, including the
    # single-element ones. baseobject._state_update() tests
    # "new_value not in self.state_targets.get(...)", so a bare string
    # value would do substring membership -- admitting 'deleted'
    # correctly by accident, and 'delete' incorrectly.
    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (BaseOperation.STATE_PREFLIGHT,
                            BaseOperation.STATE_QUEUED, dbo.STATE_DELETED,
                            dbo.STATE_ERROR, STATE_EXPIRED),
        BaseOperation.STATE_PREFLIGHT: (BaseOperation.STATE_QUEUED,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR,
                                        STATE_EXPIRED),
        BaseOperation.STATE_QUEUED: (BaseOperation.STATE_EXECUTING,
                                     dbo.STATE_DELETED, dbo.STATE_ERROR,
                                     STATE_EXPIRED),
        BaseOperation.STATE_EXECUTING: (BaseOperation.STATE_COMPLETE,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR,
                                        STATE_EXPIRED),
        BaseOperation.STATE_COMPLETE: (dbo.STATE_DELETED,),
        dbo.STATE_ERROR: (dbo.STATE_DELETED,),
        STATE_EXPIRED: (dbo.STATE_DELETED,),
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values['uuid'], static_values.get('version'))

        self.__namespace = static_values['namespace']
        self.__instance_uuid = static_values['instance_uuid']
        self.__commands = static_values['commands']
        self.__deadline = static_values.get('deadline')
        self.__progress_timeout = static_values.get('progress_timeout')

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
            deadline=metadata.get('deadline'),
            progress_timeout=metadata.get('progress_timeout'),
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
                'deadline': data.deadline,
                'progress_timeout': data.progress_timeout,
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
    def new(cls, operation_uuid, namespace, instance_uuid, commands,
            deadline=None, progress_timeout=None):
        """Create a new AgentOperation.

        deadline is an absolute unix timestamp after which the
        operation must not be dispatched or continue executing, and
        progress_timeout is the number of seconds without forward
        progress which are fatal to it. For both of them None means
        no client intent was recorded, so the server default applies,
        while an explicit 0.0 means the caller asked for none. The
        API server computes the deadline at request receipt; nothing
        reads either value yet.
        """
        AgentOperation._db_create(operation_uuid, {
            'uuid': operation_uuid,
            'namespace': namespace,
            'instance_uuid': instance_uuid,
            'commands': commands,
            'deadline': deadline,
            'progress_timeout': progress_timeout,
            'version': cls.current_version
        })
        o = AgentOperation.from_db(operation_uuid)
        o.state = cls.STATE_INITIAL
        return o

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect. The attributes row is read once and three values taken
        # from it, rather than through the per-value properties, so the
        # view does not cost one database round trip per attribute.
        attrs = self._attributes()
        retval = self._external_view()
        retval.update({
            'namespace': self.namespace,
            'instance_uuid': self.instance_uuid,
            'commands': self.commands,
            'deadline': self.deadline,
            'progress_timeout': self.progress_timeout,
            'results': attrs.results,
            'last_progress': attrs.last_progress,
            'attempts': attrs.attempts
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
    def deadline(self):
        return self.__deadline

    @property
    def progress_timeout(self):
        return self.__progress_timeout

    # Timing resolution. Both stored columns are three valued and the
    # two absences do not mean the same thing, so every enforcement
    # site goes through these helpers rather than reading the columns.
    # Test "is None" rather than truthiness everywhere below: 0.0 is
    # the caller's explicit "none at all" sentinel, not an absence.
    def effective_deadline(self):
        """The absolute timestamp this operation must not outlive, or None.

        None means there is no wall-clock deadline, which is what an
        explicit 0.0 asks for.

        A NULL column means no client intent was recorded -- the row
        was written by an API server which predates deadlines -- so
        the server default applies. Such a row carries no request
        receipt time to anchor that default against, so we anchor on
        the current state's transition time: for a queued operation
        that is when it was queued, and for an executing one when it
        was dispatched. Anchoring on time.time() instead would make
        the deadline recede on every check and never fire. The cost
        is that a legacy operation's budget restarts at each
        transition, which is accepted: it is still far tighter than
        the unbounded queue time plus 900 second executor backstop it
        replaces. See decision 3 in
        docs/plans/PLAN-agent-operation-deadlines-phase-04-enforcement.md.
        """
        if self.deadline == 0.0:
            return None
        if self.deadline is not None:
            return self.deadline
        return self.state.update_time + config.AGENT_OPERATION_DEFAULT_DEADLINE

    def deadline_passed(self):
        """True if this operation has outlived its wall-clock deadline."""
        deadline = self.effective_deadline()
        return deadline is not None and time.time() > deadline

    def effective_progress_timeout(self):
        """Seconds without progress which are fatal, or None if disabled.

        Only meaningful while a command which can report progress is
        in flight; the executor is what knows that.
        """
        if self.progress_timeout == 0.0:
            return None
        if self.progress_timeout is not None:
            return self.progress_timeout
        return float(config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT)

    # Terminal outcomes.
    def expire(self, reason):
        """Move this operation to expired, recording why.

        The reason is stored as the state's message and emitted as an
        audit event. It deliberately does not go to self.error: that
        setter refuses any state whose value does not end in "error"
        (see baseobject.DatabaseBackedObject.error), and relaxing it
        cluster wide to serve one new state on one object type would
        break the invariant that an object with an error message is
        an object in an error state.
        """
        if self.state.value in self.TERMINAL_STATES:
            return

        self._state_update(self.STATE_EXPIRED, message=reason)
        self.add_event(EVENT_TYPE_AUDIT, 'operation expired',
                       extra={'reason': reason})

    def fail(self, message):
        """Move this operation to error, recording why.

        A no-op from a terminal state. Without that guard every
        caller would raise InvalidStateException the moment deadline
        enforcement expires an operation underneath it, because
        expired has no edge to error.

        The message is recorded twice, deliberately. The state message
        is where it actually survives. self.error is set as well
        because every call site this helper replaced set it -- but
        AgentOperation does not override _db_set_attribute(), so that
        write currently reaches nothing but a warning log and a mutate
        event. Setting the state message is what makes the reason
        readable today; keeping the self.error write is what makes
        these call sites correct the day agent operation attribute
        persistence exists.
        """
        if self.state.value in self.TERMINAL_STATES:
            return

        self._state_update(dbo.STATE_ERROR, message=message)
        self.error = message

    def _attributes(self):
        """Read this operation's mutable attributes, creating the row if absent.

        Every attribute reader goes through here so the get-or-create
        dance exists once, and so a caller wanting more than one
        attribute can take them from a single read.
        """
        _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
        attrs = mariadb.get_agent_operation_attributes(_uuid)
        if not attrs:
            default = AgentOperationAttributesData(uuid=_uuid, results={})
            if mariadb.create_agent_operation_attributes(default):
                return default

            # Another thread created the record; re-read it. If that
            # read also comes back empty -- the row was created and
            # then deleted between the two calls -- fall back to the
            # defaults rather than returning None, because every
            # caller here dereferences the result and external_view()
            # does so on a user-facing path.
            attrs = mariadb.get_agent_operation_attributes(_uuid) or default
        return attrs

    @property
    def results(self):
        return self._attributes().results

    @property
    def last_progress(self):
        return self._attributes().last_progress

    @property
    def attempts(self):
        return self._attributes().attempts

    def add_result(self, index, value):
        if 'command' in value:
            del value['command']
        if 'unique' in value:
            del value['unique']

        with self.get_lock_attr('results', op='add result'):
            _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
            attrs = self._attributes()

            results = dict(attrs.results)
            results[str(index)] = value
            updated = AgentOperationAttributesData(uuid=_uuid, results=results)
            mariadb.update_agent_operation_attributes(updated, fields=['results'])

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
