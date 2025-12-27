# Pydantic schemas for REST API external views.
#
# These schemas define the structure of API responses and handle field
# transformations (e.g., State object -> state value string). The models
# are used incrementally during migration from dict-based external_view()
# methods to fully typed Pydantic responses.
#
# Migration strategy:
# 1. Add fields to these models as they're migrated
# 2. _external_view() builds the model, dumps to dict, then merges remaining
#    fields that haven't been migrated yet
# 3. Once all fields are in the model, _external_view() just returns
#    model.model_dump()

import uuid as uuid_module
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel, field_serializer

from shakenfist.schema.object_state import State


class BaseExternalView(BaseModel):
    """Base external view with fields common to all object types.

    This model handles the core fields that appear in every object's
    external API representation. The state field is transformed from
    a State object to just its value string.

    Fields handled here:
    - uuid: Object identifier (accepts both str and uuid.UUID, serializes to str)
    - state: Transformed from State object to value string
    - version: Schema version
    - metadata: User-defined key-value pairs
    """

    uuid: Union[str, uuid_module.UUID]
    state: State
    version: int
    metadata: Dict[str, Any]

    @field_serializer('uuid')
    def serialize_uuid(self, value: Union[str, uuid_module.UUID]) -> str:
        """Serialize UUID to string for API output."""
        return str(value)

    @field_serializer('state')
    def serialize_state(self, state: State) -> Optional[str]:
        """Transform State object to just its value string for API output."""
        return state.value


class BlobExternalView(BaseExternalView):
    """External view for Blob objects.

    Extends BaseExternalView with blob-specific fields. As more fields
    are migrated from the dict-based external_view(), add them here.

    Currently handled by Pydantic:
    - All fields from BaseExternalView (uuid, state, version, metadata)

    Still handled by dict merge in Blob.external_view():
    - size, modified, fetched_at, depends_on, transcodes, reference_count,
      sha512, last_used, checksums, locations, info fields (mime-type, etc.)
    """
    pass
