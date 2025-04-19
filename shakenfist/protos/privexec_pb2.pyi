import common_pb2 as _common_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PrivExecRequest(_message.Message):
    __slots__ = ("execute_request",)
    EXECUTE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    execute_request: _common_pb2.ExecuteRequest
    def __init__(self, execute_request: _Optional[_Union[_common_pb2.ExecuteRequest, _Mapping]] = ...) -> None: ...

class PrivExecReply(_message.Message):
    __slots__ = ("execute_reply",)
    EXECUTE_REPLY_FIELD_NUMBER: _ClassVar[int]
    execute_reply: _common_pb2.ExecuteReply
    def __init__(self, execute_reply: _Optional[_Union[_common_pb2.ExecuteReply, _Mapping]] = ...) -> None: ...
