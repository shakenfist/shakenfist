# Pydantic schema for object state storage in MariaDB.
#
# This schema defines the structure for storing object states in a single
# shared table. All object types (Blob, Instance, Network, etc.) use this
# same table structure, with object_type discriminating between them.
#
# The state value is stored as a string rather than an enum because different
# object types have different valid states. State validation is handled by
# each object type's state_targets dict in Python code.
#
# The State class is a Pydantic replacement for baseobject.State. It provides
# the same interface (value, update_time, message properties) but uses Pydantic
# for validation. During transition, both classes can be used interchangeably.

from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.sqlalchemy import SQLIndex


class State(BaseModel):
    """Pydantic model for object state.

    This replaces the baseobject.State class with a Pydantic model. The
    interface is backwards compatible - value, update_time, and message
    are all accessible as properties.

    The model can be constructed from:
    - Keyword arguments: State(value='created', update_time=123.45)
    - A dict: State(**{'value': 'created', 'update_time': 123.45})

    Attributes:
        value: The state value (e.g., 'created', 'deleted').
        update_time: Unix timestamp when the state was last updated.
        message: Optional message explaining the state change.
    """

    model_config = ConfigDict(frozen=True)  # Immutable like the original

    value: Optional[str] = None
    update_time: float = 0.0
    message: Optional[str] = None

    def __repr__(self) -> str:
        return 'State(' + str(self.obj_dict()) + ')'

    def __str__(self) -> str:
        return self.__repr__()

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, State):
            return False
        return self.__hash__() == other.__hash__()

    def __hash__(self) -> int:
        return hash(str(self.obj_dict()))

    def obj_dict(self) -> dict[str, Any]:
        """Return state as a dict, for backwards compatibility."""
        retval: dict[str, Any] = {
            'value': self.value,
            'update_time': self.update_time
        }
        if self.message:
            retval['message'] = self.message
        return retval


class ObjectState(BaseModel):
    """Schema for object state storage in MariaDB.

    This model represents a single row in the object_states table. The
    combination of (object_type, object_uuid) forms the primary key because
    different object types can share the same UUID. For example, a Network
    and its associated IPAM both use the network's UUID.

    Note: The actual table is created manually in mariadb.py to support
    the composite primary key. This model is used for reference only.

    Attributes:
        object_uuid: The UUID of the object this state belongs to.
        object_type: The type of object (uses ObjectType enum).
        state_value: The current state value (e.g., 'created', 'deleted').
        update_time: Unix timestamp when the state was last updated.
        message: Optional message explaining the state change.
    """

    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                # Compound index for querying objects by type and state
                ['object_type', 'state_value'],
            ]
        }
    )

    # Part of composite primary key (object_type, object_uuid)
    object_uuid: Annotated[str, Field(max_length=36)]

    # Part of composite primary key (object_type, object_uuid)
    # Uses ObjectType enum for type safety and efficient storage
    object_type: Annotated[ObjectType, SQLIndex()]

    # The actual state value - validated per-type in Python code
    state_value: Annotated[str, SQLIndex(), Field(max_length=32)]

    # When the state was last updated (Unix epoch seconds)
    update_time: float

    # Optional message explaining the state change
    message: Optional[str] = None

    def to_state(self) -> State:
        """Convert to a State object for use in object properties."""
        return State(
            value=self.state_value,
            update_time=self.update_time,
            message=self.message
        )
