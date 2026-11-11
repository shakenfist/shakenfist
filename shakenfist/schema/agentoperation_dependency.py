# Pydantic schema for an agent operation's dependency edges.
#
# An agent operation may name other agent operations which must finish before
# it runs. That edge gets its own model here rather than reusing the
# dependency model in schema/operations/baseclusteroperation.py, because the
# two permitted-target sets are deliberately different: this one admits agent
# operations and nothing else, while the cluster set admits cluster operations
# and nothing else. Keeping them apart admits the edge in one direction only
# -- an agent operation may name an agent operation, and a cluster operation
# still cannot name one -- so edges which mix the two kinds stay impossible by
# construction rather than by everyone remembering they are unwanted (issue
# #4824). Putting a second model in the cluster file would also make that
# file's name a lie about what it contains.
#
# This lives in shakenfist/schema/ beside the agent operation's own
# agentoperation_data.py and agentoperation_attributes.py, rather than in
# shakenfist/schema/operations/, which holds cluster operation schema models
# exclusively. That is where a reader goes looking for it.

from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import field_serializer
from pydantic import field_validator
from pydantic import UUID4

from shakenfist.schema.object_types import ObjectType


# The object types an agent operation's dependency edge may name.
VALID_DEPENDENCY_OPERATIONS: frozenset[ObjectType] = frozenset([
    ObjectType.AGENTOPERATION,
])


class agent_dependency(BaseModel):
    # The lower case class name follows dependency in
    # baseclusteroperation.py, which is the convention for an edge model in
    # this schema rather than an oversight.

    # op_type is stored even though only one value is legal, so that a
    # persisted agent edge is the same shape as a persisted cluster edge and
    # code which walks either reads {'op_type': ..., 'op_uuid': ...}. It is
    # not a field the API publishes, because a caller who had to supply a
    # constant could only ever get it wrong.
    op_type: ObjectType = ObjectType.AGENTOPERATION

    # The agent operation this edge names. It must already exist when the
    # edge is submitted, which is what keeps the dependency graph acyclic.
    op_uuid: UUID4

    # This edge is satisfied this many seconds after the named operation
    # completed, for callers whose next step needs the guest to settle after
    # whatever the dependency changed. None means satisfied immediately.
    settle_seconds: Optional[float] = None

    @field_validator('op_type')
    @classmethod
    def validate_op_type(cls, v: ObjectType) -> ObjectType:
        if v not in VALID_DEPENDENCY_OPERATIONS:
            raise ValueError(
                f'{v} is not a valid agent operation dependency. '
                f'Valid operations: '
                f'{sorted(o.value for o in VALID_DEPENDENCY_OPERATIONS)}'
            )
        return v

    @field_validator('settle_seconds')
    @classmethod
    def validate_settle_seconds(cls, v: Optional[float]) -> Optional[float]:
        # A negative settle would make the edge satisfied before the thing it
        # depends on had finished, which is an ordering constraint pointing
        # the wrong way.
        if v is not None and v < 0:
            raise ValueError(
                f'settle_seconds may not be negative, got {v}')
        return v

    @field_serializer('op_type')
    def serialize_op_type(self, op_type: ObjectType, _info: Any) -> str:
        return str(op_type.value)
