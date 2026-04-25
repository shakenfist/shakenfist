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

    An empty list on ``states`` behaves the same as ``None`` at the MariaDB
    layer, but the distinction is kept at the API so a caller can express
    "no matching states" deliberately in future if needed.

    Attributes:
        states: List of state values to match. ``None`` = no state filter;
            ``[]`` = no matching states (explicit empty list).
        namespace: Namespace to filter by. ``None`` = no namespace filter.
        name: Object name to filter by. ``None`` = no name filter.
        network_uuid: Foreign-key filter for the ``network_interfaces``
            table. Honoured only by ``find_network_interfaces`` and
            silently ignored by other ``find_*`` helpers.
        instance_uuid: Foreign-key filter for the ``network_interfaces``
            table. Honoured only by ``find_network_interfaces`` and
            silently ignored by other ``find_*`` helpers.
    """

    states: Optional[list[str]] = None
    namespace: Optional[str] = None
    name: Optional[str] = None
    network_uuid: Optional[str] = None
    instance_uuid: Optional[str] = None
