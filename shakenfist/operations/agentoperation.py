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
from shakenfist.eventlog import add_event_multi
from shakenfist.operations.baseoperation import BaseOperation
from shakenfist.schema.agentoperation_attributes import AgentOperationAttributesData
from shakenfist.schema.agentoperation_data import AgentOperationData
from shakenfist.schema.object_reference import references_to_grouped_dict
from shakenfist.schema.object_types import ObjectType


LOG, _ = logs.setup(__name__)


# Audit event texts for a dependency cascade. A cascade is not always a
# failed dependency: a wait or a settle delay which outruns the
# operator's budget ends an operation whose dependency has not failed
# and may yet succeed, and an operator keying alerts off the event text
# should not be told those are the same thing. The stored
# dependency_failure carries the dependency's live state either way, so
# a client can always tell them apart.
CASCADE_EVENT_FAILED = 'operation aborted by failed dependency'
CASCADE_EVENT_GAVE_UP = 'operation aborted waiting for a dependency'


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

    # The two timing budgets an operation can exhaust, as expire()
    # records them in the expiry_reason attribute. They are enumerated
    # rather than free prose because a client is expected to branch on
    # the value: a deadline expiry is answered by asking for a longer
    # deadline, a progress expiry means the guest agent stalled and a
    # bigger deadline does not help (issue #4075).
    EXPIRY_REASON_DEADLINE = 'deadline'
    EXPIRY_REASON_PROGRESS = 'progress'
    EXPIRY_REASONS = (EXPIRY_REASON_DEADLINE, EXPIRY_REASON_PROGRESS)

    # Once an operation reaches one of these it has no further
    # transitions worth making, so expire() and fail() are no-ops from
    # them rather than raising InvalidStateException.
    #
    # Membership here is also what lets
    # Instance.agent_operation_next() retire a finished queue entry. A
    # terminal state missing from this tuple takes that method's
    # unknown-state branch instead, which leaves the entry at the head
    # of the instance's queue forever and stops the instance running
    # any further agent operations, with nothing in the system able to
    # recover it. A new terminal state therefore joins this tuple in
    # the same change which gives state_targets an edge to it, never
    # one change later.
    TERMINAL_STATES = (BaseOperation.STATE_COMPLETE, dbo.STATE_ERROR,
                       STATE_EXPIRED, BaseOperation.STATE_ABORT,
                       dbo.STATE_DELETED)

    # NOTE(mikal): every value here is a tuple, including the
    # single-element ones. baseobject._state_update() tests
    # "new_value not in self.state_targets.get(...)", so a bare string
    # value would do substring membership -- admitting 'deleted'
    # correctly by accident, and 'delete' incorrectly.
    #
    # The initial -> expired edge is permitted for completeness rather
    # than because a site walks it: an operation is only in initial for
    # the moments between new() and the API enqueuing it. Declaring it
    # costs nothing and means a future enforcement point (the node-local
    # reaper is the obvious candidate) which does reach one does not
    # raise InvalidStateException.
    #
    # STATE_ABORT means something this operation declared a depends_on
    # dependency on failed, so the operation will never be run. The
    # initial -> abort and preflight -> abort edges are declared on the
    # same completeness basis as initial -> expired: the only one of
    # the three which is reachable is queued -> abort, because the
    # evaluator which will move an operation to abort runs against the
    # head of an instance's queue, and a head it can act on is queued
    # by then. Declaring the other two costs nothing and means a site
    # which does reach one does not raise InvalidStateException.
    #
    # There is deliberately no executing -> abort edge. Stopping work
    # the guest agent has already started is agent side cancellation,
    # which Shaken Fist does not implement and which is not being
    # added here, so an edge declared for it would be an invitation to
    # a future caller to abort a running operation without the agent
    # side protocol that would make the state true.
    #
    # The abort -> deleted edge is not optional. delete() removes the
    # operation's rows and only then assigns the deleted state, so
    # without it a user deleting an aborted operation would raise
    # InvalidStateException.
    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (BaseOperation.STATE_PREFLIGHT,
                            BaseOperation.STATE_QUEUED, dbo.STATE_DELETED,
                            dbo.STATE_ERROR, STATE_EXPIRED,
                            BaseOperation.STATE_ABORT),
        BaseOperation.STATE_PREFLIGHT: (BaseOperation.STATE_QUEUED,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR,
                                        STATE_EXPIRED,
                                        BaseOperation.STATE_ABORT),
        BaseOperation.STATE_QUEUED: (BaseOperation.STATE_EXECUTING,
                                     dbo.STATE_DELETED, dbo.STATE_ERROR,
                                     STATE_EXPIRED,
                                     BaseOperation.STATE_ABORT),
        BaseOperation.STATE_EXECUTING: (BaseOperation.STATE_COMPLETE,
                                        BaseOperation.STATE_QUEUED,
                                        dbo.STATE_DELETED, dbo.STATE_ERROR,
                                        STATE_EXPIRED),
        BaseOperation.STATE_COMPLETE: (dbo.STATE_DELETED,),
        dbo.STATE_ERROR: (dbo.STATE_DELETED,),
        STATE_EXPIRED: (dbo.STATE_DELETED,),
        BaseOperation.STATE_ABORT: (dbo.STATE_DELETED,),
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values['uuid'], static_values.get('version'))

        self.__namespace = static_values['namespace']
        self.__instance_uuid = static_values['instance_uuid']
        self.__commands = static_values['commands']
        # An operation which declares no edges has empty lists, never
        # None: the columns are not nullable, the model defaults to
        # empty and _db_get() always supplies both keys. The .get() is
        # for a static values dict built by hand, which some tests do.
        self.__depends_on = static_values.get('depends_on') or []
        self.__runs_after = static_values.get('runs_after') or []
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
            depends_on=metadata.get('depends_on', []),
            runs_after=metadata.get('runs_after', []),
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
                # Copied rather than referenced: data is the shared
                # model the object cache holds, and its frozen=True
                # prevents attribute assignment, not mutation of a list
                # field's contents. Handing the same list to every
                # AgentOperation built from a cache hit would make one
                # object's mutation everybody's.
                'commands': list(data.commands),
                # Copied for the reason commands just above is.
                'depends_on': list(data.depends_on),
                'runs_after': list(data.runs_after),
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
            deadline=None, progress_timeout=None, depends_on=None,
            runs_after=None):
        """Create a new AgentOperation.

        deadline is an absolute unix timestamp after which the
        operation must not be dispatched or continue executing, and
        progress_timeout is the number of seconds without forward
        progress which are fatal to it. For both of them None means
        no client intent was recorded, so the server default applies,
        while an explicit 0.0 means the caller asked for none. The
        API server computes the deadline at request receipt. Both are
        resolved by effective_deadline() and
        effective_progress_timeout(), and enforced at dequeue
        (Instance.agent_operation_next), during preflight
        (NodeAgentopOp._preflight) and in the sidechannel executor
        (SideChannelExecutorJob.expire_if_out_of_budget).

        depends_on and runs_after are lists of dependency edges, each
        naming another agent operation this one waits for. The two
        differ in fate sharing rather than in ordering: a depends_on
        edge whose operation fails takes this operation down with it,
        while a runs_after edge only says "not before". An edge may
        only name an operation which already exists, so an operation
        can point at nothing but its own past and the graph of edges is
        acyclic by construction. None and an empty list mean the same
        thing. Nothing evaluates either list yet -- an operation
        created with edges is dispatched exactly as one created without
        them.
        """
        AgentOperation._db_create(operation_uuid, {
            'uuid': operation_uuid,
            'namespace': namespace,
            'instance_uuid': instance_uuid,
            'commands': commands,
            'depends_on': depends_on or [],
            'runs_after': runs_after or [],
            'deadline': deadline,
            'progress_timeout': progress_timeout,
            'version': cls.current_version
        })
        o = AgentOperation.from_db(operation_uuid)
        o.state = cls.STATE_INITIAL
        return o

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect. The attributes row is read once and every attribute the
        # view carries is taken from it, rather than through the per-value
        # properties, so the view does not cost one database round trip per
        # attribute.
        attrs = self._attributes()
        retval = self._external_view()
        retval.update({
            'namespace': self.namespace,
            'instance_uuid': self.instance_uuid,
            'commands': self.commands,
            'depends_on': self.depends_on,
            'runs_after': self.runs_after,
            'deadline': self.deadline,
            'progress_timeout': self.progress_timeout,
            'results': attrs.results,
            'last_progress': attrs.last_progress,
            'attempts': attrs.attempts,
            'expiry_reason': attrs.expiry_reason,
            'dependency_failure': attrs.dependency_failure,
            'error_message': self.error
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
        # Returned by reference, like every other static value property
        # in the tree. Static values are immutable after construction,
        # so the contract for a caller which needs to consume this list
        # is to take its own copy -- see SideChannelExecutorJob's, and
        # "A frozen model is not a deep frozen model" in
        # docs/developer_guide/coding_rules.md. _db_get() copies at the
        # cache boundary so a caller who breaks that rule corrupts only
        # its own object rather than every reader in the process, but
        # the boundary is containment, not permission.
        return self.__commands

    @property
    def depends_on(self):
        # Returned by reference, as commands is and for the same
        # reasons, so a caller which needs to consume this list takes
        # its own copy.
        return self.__depends_on

    @property
    def runs_after(self):
        return self.__runs_after

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
    def effective_deadline(self, state=None):
        """The absolute timestamp this operation must not outlive, or None.

        None means there is no wall-clock deadline. An explicit 0.0
        asks for that, but only gets it while a live progress timeout
        still bounds the operation -- the design's licensed use for
        the sentinel is a very large transfer whose stalls the
        progress timeout detects. With both budgets disabled at once
        (every agent/execute created with deadline_seconds=0, since
        that endpoint always stores a progress timeout of 0.0) nothing
        would ever end the operation, and it would hold its instance's
        single executor slot for as long as the guest command ran
        (issue #4074, the same wedge shape as #3516). Such an
        operation is instead bounded by AGENT_OPERATION_MAX_DEADLINE,
        anchored on its current state transition like a NULL row.

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
        replaces.

        Retry makes that cost larger, and it is worth being explicit
        about how much. A retry is two more transitions
        (executing -> queued -> executing), so a NULL-deadline
        operation is handed a fresh AGENT_OPERATION_DEFAULT_DEADLINE
        on every attempt, and its real ceiling is
        AGENT_OPERATION_MAX_ATTEMPTS times that default rather than
        the default -- 30 minutes rather than 10, at the shipped
        values. It is still bounded, and it applies only to rows
        written by an API server old enough to predate deadlines, so
        it is a rolling-upgrade property rather than a permanent one.

        A caller which has already read this operation's State passes
        it as state, and the anchor is taken from that rather than
        read again. That matters on the dispatch poll: reading it
        here is an uncached GetState round trip, it only happens for
        the NULL-deadline rows a rolling upgrade is full of, and the
        one caller which polls per ready instance every five seconds
        has the value in hand already.
        """
        if self.deadline == 0.0:
            if self.effective_progress_timeout() is not None:
                return None
            if state is None:
                state = self.state
            return state.update_time + config.AGENT_OPERATION_MAX_DEADLINE
        if self.deadline is not None:
            return self.deadline
        if state is None:
            state = self.state
        return state.update_time + config.AGENT_OPERATION_DEFAULT_DEADLINE

    def deadline_passed(self, state=None):
        """True if this operation has outlived its wall-clock deadline.

        state is an already-read State to anchor a NULL deadline
        against; see effective_deadline().
        """
        deadline = self.effective_deadline(state=state)
        return deadline is not None and time.time() > deadline

    def deadline_needs_state_anchor(self):
        """True when effective_deadline() must read a State to resolve.

        That read is an uncached GetState round trip, so a hot path
        which checks repeatedly reads the State once and passes it in
        instead (issue #4014, and see
        SideChannelExecutorJob.expire_if_out_of_budget()). True for a
        NULL deadline, whose server default anchors on a transition
        time, and for the 0.0 sentinel with no live progress timeout,
        whose AGENT_OPERATION_MAX_DEADLINE backstop anchors the same
        way. Both columns tested here are static values, so this costs
        no database read itself.
        """
        if self.deadline is None:
            return True
        return (self.deadline == 0.0
                and self.effective_progress_timeout() is None)

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
    def expire(self, reason, budget):
        """Move this operation to expired, recording why.

        budget is which of the two timing budgets ran out, one of
        EXPIRY_REASONS. It is persisted to the expiry_reason attribute
        so the external view carries a value a client can branch on
        without parsing the prose in reason (issue #4075).

        The reason is stored as the state's message and emitted as an
        audit event. It deliberately does not go to self.error: that
        setter refuses any state whose value does not end in "error"
        (see baseobject.DatabaseBackedObject.error), and relaxing it
        cluster wide to serve one new state on one object type would
        break the invariant that an object with an error message is
        an object in an error state.
        """
        if budget not in self.EXPIRY_REASONS:
            raise ValueError(f'unknown expiry budget: {budget}')

        if self.state.value in self.TERMINAL_STATES:
            return

        # Written before the state transition, so a reader who sees
        # expired always sees the reason too. The field mask is not
        # optional, for the reason record_progress() spells out in its
        # docstring.
        attrs = self._attributes()
        attrs.expiry_reason = budget
        mariadb.update_agent_operation_attributes(
            attrs, fields=['expiry_reason'])

        self._state_update(self.STATE_EXPIRED, message=reason)

        # The event is recorded against the instance as well as the
        # operation, because expired is in FINAL_OBJECT_STATES and so
        # the operation itself is swept for hard deletion after
        # CLEANER_DELAY. An event held only against the operation
        # would go with it; the instance is where an operator looks
        # for the history of what was run against a machine anyway.
        # Object tuples are passed rather than a hydrated Instance so
        # this costs no database read, and so that agentoperation does
        # not import instance (which imports it).
        add_event_multi(
            EVENT_TYPE_AUDIT,
            [(self.object_type, self.uuid),
             (ObjectType.INSTANCE, self.instance_uuid)],
            'operation expired', extra={'reason': reason, 'budget': budget})

    def fail(self, message):
        """Move this operation to error, recording why.

        A no-op from a terminal state. Without that guard every
        caller would raise InvalidStateException the moment deadline
        enforcement expires an operation underneath it, because
        expired has no edge to error.

        The reason is recorded as the state message, and only there.
        Every call site this helper replaced also assigned self.error
        as well; that second write is dropped deliberately. Since the
        fix for issue #3899 the error setter stores the message on the
        object's state row, which is the row _state_update() has just
        written, so self.error already reads back what was passed here
        and assigning it again would buy nothing but a redundant mutate
        event on every failure.
        """
        if self.state.value in self.TERMINAL_STATES:
            return

        self._state_update(dbo.STATE_ERROR, message=message)

    def cascade_abort_for_dependency(self, dep, dep_state,
                                     event=CASCADE_EVENT_FAILED):
        """Move this operation to abort because a dependency it needs failed.

        dep is the stored depends_on edge, so it carries op_type and
        op_uuid, and dep_state is the state that dependency was
        observed in. dep_state may be a value no AgentOperation ever
        holds -- 'missing' for a dependency which no longer exists at
        all -- which is deliberate: a client has to be able to tell a
        dependency which failed from one which went away, and both
        cost the dependent the same fate.

        This is for depends_on only. A runs_after edge is an ordering
        hint rather than a fate-sharing one, so an unsatisfiable
        runs_after warns and lets its operation run, and never arrives
        here.

        Returns True when the cascade was applied and False when the
        operation had already finished, so a caller can tell a cascade
        it performed from one it was too late for.

        An operation which is already terminal is left alone, which is
        the guard expire() and fail() carry and for a sharper reason
        than theirs. Deadline enforcement can expire an operation out
        of band at any moment, so this call really does race a
        finished operation. Writing the reason anyway would leave an
        operation reading expired on the wire while carrying a
        dependency_failure claiming a dependency aborted it, and that
        field exists precisely so a client can branch on it without
        parsing prose (issue #4075) -- so a client doing exactly what
        the field was published for would be told something untrue
        about a finished operation. The state machine's refusal
        remains the backstop for the window between this check and
        the write below, but it must not be the primary mechanism:
        reaching it means the reason has already been persisted.

        The no-op is noisy rather than silent. A cascade which did not
        land is worth an operator seeing, so it is audited with the
        state the operation was found in.

        event is the audit event text, because not every cascade is a
        failed dependency. A wait or a settle delay which outruns the
        operator's budget aborts an operation whose dependency has not
        failed and may yet succeed, and an event claiming otherwise is
        what an operator's alerting would key on. The stored
        dependency_failure carries the dependency's live state either
        way, so a client can always tell the two apart.
        """
        if self.state.value in self.TERMINAL_STATES:
            add_event_multi(
                EVENT_TYPE_AUDIT,
                [(self.object_type, self.uuid),
                 (ObjectType.INSTANCE, self.instance_uuid)],
                'operation finished before its dependency cascade applied',
                extra={'op_type': dep['op_type'], 'op_uuid': dep['op_uuid'],
                       'dependency_state': dep_state,
                       'state': self.state.value})
            return False

        # The reason is written before the state transition, so a
        # reader who sees abort can always see why. The order is not
        # merely tidiness: delete() removes an operation's attribute
        # rows and only then assigns its state, so there is no later
        # moment at which the reason could be filled in, and a state
        # written first would be observable with its explanation
        # missing. The field mask is not optional, for the reason
        # record_progress() spells out in its docstring.
        attrs = self._attributes()
        attrs.dependency_failure = {
            'op_type': dep['op_type'],
            'op_uuid': dep['op_uuid'],
            'state': dep_state
        }
        mariadb.update_agent_operation_attributes(
            attrs, fields=['dependency_failure'])

        self.state = BaseOperation.STATE_ABORT

        # Against the instance as well as the operation, for the
        # reason expire() gives: abort is in FINAL_OBJECT_STATES too,
        # so the operation is swept for hard deletion and an event
        # held only against it would go with it.
        add_event_multi(
            EVENT_TYPE_AUDIT,
            [(self.object_type, self.uuid),
             (ObjectType.INSTANCE, self.instance_uuid)],
            event,
            extra={'op_type': dep['op_type'], 'op_uuid': dep['op_uuid'],
                   'state': dep_state})
        return True

    def note_dependency_wait_started(self):
        """Record that this operation is blocked on a dependency, and when.

        Returns the instant the dependency wait budget is measured
        from, whether this call wrote it or an earlier one did, so a
        caller which has just discovered a block and one which
        rediscovers it on a later pass both get the same answer.

        The write happens only while the anchor is unset, and that
        idempotence is a property of this method rather than something
        each caller has to remember. A blocked operation is
        re-evaluated on every dispatch pass, so an unconditional write
        would push the anchor forward a few seconds at a time and
        leave a budget which can never be exhausted: what is being
        guarded against is not a lost write but a budget which quietly
        stops being one.
        """
        attrs = self._attributes()
        if attrs.dependency_wait_started is not None:
            return attrs.dependency_wait_started

        anchor = time.time()
        attrs.dependency_wait_started = anchor
        mariadb.update_agent_operation_attributes(
            attrs, fields=['dependency_wait_started'])
        return anchor

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

    @property
    def expiry_reason(self):
        return self._attributes().expiry_reason

    @property
    def dependency_failure(self):
        return self._attributes().dependency_failure

    def record_attempt(self):
        """Persist that another dispatch attempt of this operation has started.

        The persistence detail lives here rather than in the caller for
        the same reason record_progress()'s does: this is the object
        which knows how its attributes row is written.

        The field mask is not optional. add_result() reads, merges and
        writes the results column on this same row, and an unmasked
        write here would push a stale snapshot of it over a concurrent
        update.
        """
        attrs = self._attributes()
        attrs.attempts += 1
        mariadb.update_agent_operation_attributes(
            attrs, fields=['attempts'])

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

    def clear_results(self):
        """Discard this operation's results.

        Used when a retry abandons an attempt: the next attempt
        rewrites every index it reaches, so the results this clears
        are only ever ones the retry is about to replace or was never
        going to reach.

        It takes the same results lock that add_result() takes, since
        the two write the same column.

        The field mask is not optional, for the reason record_progress()
        spells out in its docstring.

        The event matters more here than add_result()'s does. This is
        the only place results are destroyed rather than overwritten,
        so without it an operation's event stream shows results
        appearing and then simply not being there, with nothing tying
        the disappearance to the retry which caused it.
        """
        with self.get_lock_attr('results', op='clear results'):
            _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
            attrs = self._attributes()
            cleared = len(attrs.results)
            updated = AgentOperationAttributesData(uuid=_uuid, results={})
            mariadb.update_agent_operation_attributes(updated, fields=['results'])

        self.add_event(EVENT_TYPE_MUTATE, 'clear results',
                       extra={'cleared': cleared})

    def record_progress(self, timestamp):
        """Persist the time the agent last made forward progress.

        The persistence detail lives here rather than in the caller for
        the same reason add_result()'s does: this is the object which
        knows how its attributes row is written.

        The field mask is not optional. add_result() reads, merges and
        writes the results column on this same row, and an unmasked
        write here would push a stale snapshot of it over a concurrent
        update.
        """
        attrs = self._attributes()
        attrs.last_progress = timestamp
        mariadb.update_agent_operation_attributes(
            attrs, fields=['last_progress'])

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
