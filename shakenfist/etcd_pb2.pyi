from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ResponseHeader(_message.Message):
    __slots__ = ("cluster_id", "member_id", "revision", "raft_term")
    CLUSTER_ID_FIELD_NUMBER: _ClassVar[int]
    MEMBER_ID_FIELD_NUMBER: _ClassVar[int]
    REVISION_FIELD_NUMBER: _ClassVar[int]
    RAFT_TERM_FIELD_NUMBER: _ClassVar[int]
    cluster_id: int
    member_id: int
    revision: int
    raft_term: int
    def __init__(self, cluster_id: _Optional[int] = ..., member_id: _Optional[int] = ..., revision: _Optional[int] = ..., raft_term: _Optional[int] = ...) -> None: ...

class CompactionRequest(_message.Message):
    __slots__ = ("revision", "physical")
    REVISION_FIELD_NUMBER: _ClassVar[int]
    PHYSICAL_FIELD_NUMBER: _ClassVar[int]
    revision: int
    physical: bool
    def __init__(self, revision: _Optional[int] = ..., physical: bool = ...) -> None: ...

class CompactionResponse(_message.Message):
    __slots__ = ("header",)
    HEADER_FIELD_NUMBER: _ClassVar[int]
    header: ResponseHeader
    def __init__(self, header: _Optional[_Union[ResponseHeader, _Mapping]] = ...) -> None: ...

class DefragmentRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class DefragmentResponse(_message.Message):
    __slots__ = ("header",)
    HEADER_FIELD_NUMBER: _ClassVar[int]
    header: ResponseHeader
    def __init__(self, header: _Optional[_Union[ResponseHeader, _Mapping]] = ...) -> None: ...
