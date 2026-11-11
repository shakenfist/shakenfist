# Pydantic schema for AgentOperation object storage in MariaDB.
#
# This schema defines the structure for storing AgentOperation static
# values. AgentOperations represent in-guest agent tasks (execute,
# get-file, put-blob, chmod) queued against an Instance.
#
# This model serves as both:
# 1. The source of truth for the agent_operations table schema (used
#    by pydantic_to_sqlalchemy_table to generate the table)
# 2. A typed data transfer object for AgentOperation static values

from typing import Annotated
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import field_validator
from pydantic import UUID4
from pydantic import ValidationInfo

from shakenfist.schema.agentoperation_dependency import agent_dependency
from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class AgentOperationData(BaseModel):
    """Schema for AgentOperation static values in MariaDB.

    This model represents the static (immutable) values for an
    AgentOperation object. It replaces the dict-based static_values
    pattern with a type-safe Pydantic model.

    Table: agent_operations
    Primary key: uuid

    Attributes:
        uuid: The AgentOperation's unique identifier.
        namespace: The namespace this operation belongs to.
        instance_uuid: The UUID of the Instance this operation
            targets.
        commands: List of command dicts (stored as JSON).
        depends_on: Edges whose named operations must succeed before
            this one runs, and whose failure is this one's failure.
            Each entry is an agent_dependency serialised to a dict.
        runs_after: Edges which order this operation after the named
            operations without sharing their fate. Each entry is an
            agent_dependency serialised to a dict.
        deadline: Absolute unix timestamp after which the operation
            must not be dispatched and must not continue executing.
            NULL means no client intent was recorded, so the server
            default applies; an explicit 0.0 means the client asked
            for no wall-clock deadline at all.
        progress_timeout: Seconds without forward progress which are
            fatal to the operation. NULL means no client intent was
            recorded, so the server default for progress-capable
            commands applies; an explicit 0.0 disables the progress
            timeout.
        version: Object version number for schema migrations.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The AgentOperation's UUID - primary key, stored as native
    # MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The namespace this operation belongs to
    namespace: str

    # The Instance UUID this operation targets (indexed for lookups)
    instance_uuid: Annotated[UUID4, SQLNativeUUID(), SQLIndex()]

    # List of command dicts (variable-length, stored as JSON)
    commands: list[dict]

    # The operations this one depends on, and the operations it merely
    # follows. Both are lists of agent_dependency serialised to dicts,
    # stored as JSON the same way commands is, and an empty list is the
    # ordinary case.
    #
    # The two differ in fate sharing, not in ordering: a depends_on edge
    # whose operation fails takes this operation down with it, while a
    # runs_after edge only says "not before", so this operation still runs
    # when the edge's operation fails. An edge's settle_seconds, where
    # present, means the edge is satisfied that many seconds after its
    # operation completed rather than immediately.
    #
    # Nothing evaluates either list yet. An operation created with edges
    # today is dispatched exactly as one created without them.
    depends_on: list[dict] = []
    runs_after: list[dict] = []

    # The absolute unix timestamp after which this operation must not
    # be dispatched and must not continue executing. Computed by the
    # API server at request receipt as time.time() + deadline_seconds.
    #
    # NULL means no client intent was recorded -- either the row
    # predates deadlines entirely, or it was written by an API node
    # which had not yet been upgraded -- and the server default
    # applies. An explicit 0.0 means the client asked for no
    # wall-clock deadline at all. 0.0 is an unambiguous sentinel
    # because a real deadline is an absolute timestamp of order 1.7e9.
    deadline: Optional[float] = None

    # Seconds without forward progress which are fatal to this
    # operation. NULL means the same thing it means for deadline: no
    # client intent was recorded, so the server default for
    # progress-capable commands applies. An explicit 0.0 disables the
    # progress timeout.
    progress_timeout: Optional[float] = None

    # Object version number for schema migrations
    version: int

    @field_validator('depends_on', 'runs_after')
    @classmethod
    def validate_dependencies(
            cls, v: list[dict], info: ValidationInfo) -> list[dict]:
        """Refuse an edge list which does not describe agent_dependency."""
        # The columns are JSON, so the shape of what goes in them is
        # otherwise unchecked. Validating here rather than in the API layer
        # means a malformed or mixed-type edge is refused when the row is
        # read back as well as when it is written, so a bad edge which
        # arrived some other way -- a hand-edited row, an older or
        # future-dated writer -- surfaces as a refusal rather than as an
        # evaluator quietly doing nothing with it.
        # Narrowed to the two exceptions this can actually raise, matching
        # the API helper which does the same construction: pydantic's
        # ValidationError is a ValueError, and a non-mapping entry is the
        # TypeError from the keyword expansion. Chained, so a traceback keeps
        # the report which says which field was wrong.
        for index, entry in enumerate(v):
            try:
                agent_dependency(**entry)
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f'{info.field_name}[{index}] is not a valid agent '
                    f'operation dependency: {e}') from e
        return v
