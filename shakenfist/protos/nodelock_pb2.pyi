from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class LockRequest(_message.Message):
    __slots__ = ("requester", "key")
    REQUESTER_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    requester: str
    key: str
    def __init__(self, requester: _Optional[str] = ..., key: _Optional[str] = ...) -> None: ...

class UnlockRequest(_message.Message):
    __slots__ = ("requester", "key")
    REQUESTER_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    requester: str
    key: str
    def __init__(self, requester: _Optional[str] = ..., key: _Optional[str] = ...) -> None: ...

class NodeLockRequest(_message.Message):
    __slots__ = ("lock_request", "unlock_request")
    LOCK_REQUEST_FIELD_NUMBER: _ClassVar[int]
    UNLOCK_REQUEST_FIELD_NUMBER: _ClassVar[int]
    lock_request: LockRequest
    unlock_request: UnlockRequest
    def __init__(self, lock_request: _Optional[_Union[LockRequest, _Mapping]] = ..., unlock_request: _Optional[_Union[UnlockRequest, _Mapping]] = ...) -> None: ...

class LockReply(_message.Message):
    __slots__ = ("outcome",)
    class Outcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        OK: _ClassVar[LockReply.Outcome]
        ALREADY_HELD: _ClassVar[LockReply.Outcome]
        DENIED: _ClassVar[LockReply.Outcome]
    OK: LockReply.Outcome
    ALREADY_HELD: LockReply.Outcome
    DENIED: LockReply.Outcome
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: LockReply.Outcome
    def __init__(self, outcome: _Optional[_Union[LockReply.Outcome, str]] = ...) -> None: ...

class UnlockReply(_message.Message):
    __slots__ = ("outcome",)
    class Outcome(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        OK: _ClassVar[UnlockReply.Outcome]
        NOT_HELD: _ClassVar[UnlockReply.Outcome]
    OK: UnlockReply.Outcome
    NOT_HELD: UnlockReply.Outcome
    OUTCOME_FIELD_NUMBER: _ClassVar[int]
    outcome: UnlockReply.Outcome
    def __init__(self, outcome: _Optional[_Union[UnlockReply.Outcome, str]] = ...) -> None: ...

class NodeLockReply(_message.Message):
    __slots__ = ("lock_reply", "unlock_reply")
    LOCK_REPLY_FIELD_NUMBER: _ClassVar[int]
    UNLOCK_REPLY_FIELD_NUMBER: _ClassVar[int]
    lock_reply: LockReply
    unlock_reply: UnlockReply
    def __init__(self, lock_reply: _Optional[_Union[LockReply, _Mapping]] = ..., unlock_reply: _Optional[_Union[UnlockReply, _Mapping]] = ...) -> None: ...
