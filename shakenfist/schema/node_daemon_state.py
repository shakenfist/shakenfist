# Copyright 2019 Michael Still and contributors
#
# Pydantic schema for per-daemon state storage in MariaDB.
#
# Each (node, daemon) pair gets one row in the ``node_daemon_states`` table.
# This replaces the ``daemon_states`` JSON dict that used to live inside
# ``node_attributes``: the dict required a single coarse per-node lock for
# every state change, which serialised every daemon's startup/shutdown
# transition through one hot path. With a dedicated table keyed on
# ``(node_uuid, daemon)`` each transition is a single atomic upsert and the
# Python-side lock disappears entirely.
#
# The table is created manually in ``mariadb.py`` (see
# ``_get_node_daemon_states_table``) to support the composite primary key.

from typing import Annotated
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.object_state import State
from shakenfist.schema.sqlalchemy import SQLIndex
from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NodeDaemonStateData(BaseModel):
    """Schema for a single ``(node, daemon)`` state row.

    The row mirrors the historical JSON dict entry stored under
    ``node_attributes.daemon_states[daemon]``: a state value, an update
    timestamp, and an optional message. ``State`` is reused for the
    in-memory representation so callers do not see any difference.
    """

    model_config = ConfigDict(
        json_schema_extra={
            'sql_indexes': [
                # Lookup by node is the dominant query pattern -- every
                # ``Node.get_degraded_daemons`` call lists all daemons for
                # one node.
                ['node_uuid'],
                # Reverse lookup: which nodes have a given daemon in a
                # particular state?
                ['daemon', 'value'],
            ]
        }
    )

    node_uuid: Annotated[UUID4, SQLNativeUUID()]
    daemon: Annotated[str, Field(max_length=32), SQLIndex()]

    value: Annotated[Optional[str], Field(max_length=32)] = None
    update_time: float = 0.0
    message: Optional[str] = None

    def to_state(self) -> State:
        """Return a ``State`` view, matching the prior JSON-dict shape."""
        return State(
            value=self.value,
            update_time=self.update_time,
            message=self.message)

    def obj_dict(self) -> dict[str, Any]:
        """Return the historical dict shape (for transitional code paths)."""
        return self.to_state().obj_dict()
