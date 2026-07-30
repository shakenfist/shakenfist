from enum import Enum
import json
from typing import Any
import uuid

from shakenfist import baseobject

# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


class JSONEncoderCustomTypes(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, baseobject.State):
            return obj.obj_dict()
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.name
        return json.JSONEncoder.default(self, obj)


class JSONEncoderStringifyFallback(JSONEncoderCustomTypes):
    def default(self, obj: Any) -> Any:
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def json_dump(data: Any) -> str:
    return json.dumps(
        data, indent=4, sort_keys=True, cls=JSONEncoderCustomTypes)


def json_sanitise(data: Any) -> Any:
    """Round trip data through JSON, stringifying unserialisable leaves.

    The result is a deep copy of data composed only of JSON-native types,
    safe to hand to any json.dumps call with no custom encoder.
    """
    return json.loads(json.dumps(data, cls=JSONEncoderStringifyFallback))
