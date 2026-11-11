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

import math
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
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

    # An unknown key is refused rather than dropped. pydantic's default is
    # to ignore one, which would let junk ride along in a stored edge and
    # read back out again -- and the point of validating here rather than
    # only at the API layer is that an edge cannot be stored or read back
    # unnoticed whatever wrote it. The published parameter already refuses
    # an unknown key, but only while request validation is enforcing.
    model_config = ConfigDict(extra='forbid')

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

    @field_validator('settle_seconds', mode='before')
    @classmethod
    def refuse_boolean_settle_seconds(cls, v: Any) -> Any:
        # Before coercion, because that is the only point a bool is still
        # distinguishable: isinstance(True, int) is true in Python, so a
        # bool reaches float() happily and arrives as 1.0. JSON true is not
        # a number of seconds, and accepting it as one second would be worse
        # than refusing it. The deadline and progress parameters refuse a
        # bool in external_api/base.py for the same reason.
        if isinstance(v, bool):
            raise ValueError(
                'settle_seconds must be a number of seconds, not a boolean')
        return v

    @field_validator('settle_seconds')
    @classmethod
    def validate_settle_seconds(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v

        # Neither infinity nor NaN is a number of seconds, and both arrive
        # easily: json.loads() accepts the bare Infinity and NaN literals, so
        # flask hands them straight through. NaN matters most, because it
        # fails every comparison including the negative test below and so
        # would otherwise be stored -- to become a wrong answer rather than a
        # refusal once something does settle arithmetic with it. This is the
        # same refusal, for the same reason, that the deadline and progress
        # parameters make in external_api/base.py.
        if not math.isfinite(v):
            raise ValueError(
                f'settle_seconds must be a finite number of seconds, got {v}')

        # A negative settle would make the edge satisfied before the thing it
        # depends on had finished, which is an ordering constraint pointing
        # the wrong way.
        if v < 0:
            raise ValueError(
                f'settle_seconds may not be negative, got {v}')

        # The operator ceiling is deliberately NOT enforced here. It is a
        # deployment policy value, and nothing else in shakenfist/schema/
        # imports config -- making forty schema modules depend on it to bound
        # one field would be a worse trade than checking the bound in the one
        # place every stored edge passes through, which is the API helper.
        return v

    @field_serializer('op_type')
    def serialize_op_type(self, op_type: ObjectType, _info: Any) -> str:
        return str(op_type.value)
