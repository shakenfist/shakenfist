# Pydantic schema for object filtering criteria in queries.
#
# This schema defines the filter parameters for querying objects by state,
# namespace, and name.

from typing import Optional

from pydantic import BaseModel


class ObjectFilterCriteria(BaseModel):
    """Filter criteria for object queries.

    Represents optional filtering constraints for finding artifacts, instances,
    or networks. ``None`` on any field means "do not filter on this field".

    An empty list on ``states`` means the same as ``None``: do not filter
    on state. The gRPC wire format cannot carry the difference (an empty
    repeated field is indistinguishable from an unset one), so
    ``criteria_to_proto()`` and ``criteria_from_proto()`` in mariadb.py
    collapse ``[]`` to ``None``. A future "match no states" would need its
    own field or a sentinel value, not an empty list.

    Attributes:
        states: List of state values to match. ``None`` or ``[]`` = no
            state filter.
        namespace: Namespace to filter by. ``None`` = no namespace filter.
        name: Object name to filter by. ``None`` = no name filter.
        network_uuid: Foreign-key filter for the ``network_interfaces``
            table. Honoured only by ``find_network_interfaces``; a
            ``find_*`` helper whose table has no such column refuses
            the query rather than ignoring the field (issue 4947).
        instance_uuid: Foreign-key filter for the ``network_interfaces``
            table, with the same rule as ``network_uuid``.

    ``states`` is the one field every filterable type can serve, since
    it is read from ``object_states`` rather than from the per-type
    table. Each of the others is a column on some tables and not on
    others, and asking for one a table cannot serve is refused: the
    alternative is answering a narrower query with more rows than it
    asked for, which is how a scoping mistake becomes a disclosure.
    """

    states: Optional[list[str]] = None
    namespace: Optional[str] = None
    name: Optional[str] = None
    network_uuid: Optional[str] = None
    instance_uuid: Optional[str] = None
