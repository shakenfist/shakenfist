from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class EventRequest(_message.Message):
    __slots__ = ("object_type", "object_uuid", "event_type", "timestamp", "fqdn", "duration", "message", "extra")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_UUID_FIELD_NUMBER: _ClassVar[int]
    EVENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    FQDN_FIELD_NUMBER: _ClassVar[int]
    DURATION_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    EXTRA_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    object_uuid: str
    event_type: str
    timestamp: float
    fqdn: str
    duration: float
    message: str
    extra: str
    def __init__(self, object_type: _Optional[str] = ..., object_uuid: _Optional[str] = ..., event_type: _Optional[str] = ..., timestamp: _Optional[float] = ..., fqdn: _Optional[str] = ..., duration: _Optional[float] = ..., message: _Optional[str] = ..., extra: _Optional[str] = ...) -> None: ...

class EventReply(_message.Message):
    __slots__ = ("ack",)
    ACK_FIELD_NUMBER: _ClassVar[int]
    ack: bool
    def __init__(self, ack: bool = ...) -> None: ...
