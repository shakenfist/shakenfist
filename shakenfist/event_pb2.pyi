from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EventRequest(_message.Message):
    __slots__ = ("object_type", "object_uuid", "event_type", "obsolete_timestamp", "fqdn", "duration", "message", "extra", "timestamp")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_UUID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBSOLETE_TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    FQDN_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EXTRA_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    object_uuid: str
    event_type: str
    obsolete_timestamp: float
    fqdn: str
    duration: float
    message: str
    extra: str
    timestamp: float
    def __init__(self, object_type: _Optional[str] = ..., object_uuid: _Optional[str] = ..., event_type: _Optional[str] = ..., obsolete_timestamp: _Optional[float] = ..., fqdn: _Optional[str] = ..., duration: _Optional[float] = ..., message: _Optional[str] = ..., extra: _Optional[str] = ..., timestamp: _Optional[float] = ...) -> None: ...

class EventObject(_message.Message):
    __slots__ = ("object_type", "object_uuid")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_UUID_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    object_uuid: str
    def __init__(self, object_type: _Optional[str] = ..., object_uuid: _Optional[str] = ...) -> None: ...

class EventMultiRequest(_message.Message):
    __slots__ = ("objects", "event_type", "fqdn", "duration", "message", "extra", "timestamp")
    OBJECTS_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    FQDN_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EXTRA_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    objects: _containers.RepeatedCompositeFieldContainer[EventObject]
    event_type: str
    fqdn: str
    duration: float
    message: str
    extra: str
    timestamp: float
    def __init__(self, objects: _Optional[_Iterable[_Union[EventObject, _Mapping]]] = ..., event_type: _Optional[str] = ..., fqdn: _Optional[str] = ..., duration: _Optional[float] = ..., message: _Optional[str] = ..., extra: _Optional[str] = ..., timestamp: _Optional[float] = ...) -> None: ...

class EventReply(_message.Message):
    __slots__ = ("ack",)
    ACK_FIELD_NUMBER: _ClassVar[int]
    ack: bool
    def __init__(self, ack: bool = ...) -> None: ...
