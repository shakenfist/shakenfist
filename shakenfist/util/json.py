from enum import Enum
import json
import uuid

from shakenfist import baseobject
from shakenfist.tasks import QueueTask
# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


class JSONEncoderCustomTypes(json.JSONEncoder):
    def default(self, obj):
        if QueueTask.__subclasscheck__(type(obj)):
            return obj.obj_dict()
        if type(obj) is baseobject.State:
            return obj.obj_dict()
        if type(obj) is uuid.UUID:
            return str(obj)
        if isinstance(obj, Enum):
            return obj.name
        return json.JSONEncoder.default(self, obj)


def json_dump(data):
    return json.dumps(
        data, indent=4, sort_keys=True, cls=JSONEncoderCustomTypes)
