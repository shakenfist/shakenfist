from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class StatusReply(_message.Message):
    __slots__ = ("success", "error")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    success: bool
    error: str
    def __init__(self, success: bool = ..., error: _Optional[str] = ...) -> None: ...

class GetRequest(_message.Message):
    __slots__ = ("object_type", "subtype", "name")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    subtype: str
    name: str
    def __init__(self, object_type: _Optional[str] = ..., subtype: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class GetReply(_message.Message):
    __slots__ = ("found", "value")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    found: bool
    value: str
    def __init__(self, found: bool = ..., value: _Optional[str] = ...) -> None: ...

class GetPrefixRequest(_message.Message):
    __slots__ = ("object_type", "subtype", "prefix", "limit")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    PREFIX_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    subtype: str
    prefix: str
    limit: int
    def __init__(self, object_type: _Optional[str] = ..., subtype: _Optional[str] = ..., prefix: _Optional[str] = ..., limit: _Optional[int] = ...) -> None: ...

class KeyValue(_message.Message):
    __slots__ = ("key", "value")
    KEY_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    key: str
    value: str
    def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class GetPrefixReply(_message.Message):
    __slots__ = ("results",)
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[KeyValue]
    def __init__(self, results: _Optional[_Iterable[_Union[KeyValue, _Mapping]]] = ...) -> None: ...

class PutRequest(_message.Message):
    __slots__ = ("object_type", "subtype", "name", "data")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    subtype: str
    name: str
    data: str
    def __init__(self, object_type: _Optional[str] = ..., subtype: _Optional[str] = ..., name: _Optional[str] = ..., data: _Optional[str] = ...) -> None: ...

class CreateRequest(_message.Message):
    __slots__ = ("object_type", "subtype", "name", "data")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    subtype: str
    name: str
    data: str
    def __init__(self, object_type: _Optional[str] = ..., subtype: _Optional[str] = ..., name: _Optional[str] = ..., data: _Optional[str] = ...) -> None: ...

class DeleteRequest(_message.Message):
    __slots__ = ("object_type", "subtype", "name")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    subtype: str
    name: str
    def __init__(self, object_type: _Optional[str] = ..., subtype: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class DeletePrefixRequest(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class Mutation(_message.Message):
    __slots__ = ("path", "original_data", "new_data", "original_is_none", "new_is_none")
    PATH_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_DATA_FIELD_NUMBER: _ClassVar[int]
    NEW_DATA_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_IS_NONE_FIELD_NUMBER: _ClassVar[int]
    NEW_IS_NONE_FIELD_NUMBER: _ClassVar[int]
    path: str
    original_data: str
    new_data: str
    original_is_none: bool
    new_is_none: bool
    def __init__(self, path: _Optional[str] = ..., original_data: _Optional[str] = ..., new_data: _Optional[str] = ..., original_is_none: bool = ..., new_is_none: bool = ...) -> None: ...

class ReplaceManyRequest(_message.Message):
    __slots__ = ("mutations", "suppress_failure_audit")
    MUTATIONS_FIELD_NUMBER: _ClassVar[int]
    SUPPRESS_FAILURE_AUDIT_FIELD_NUMBER: _ClassVar[int]
    mutations: _containers.RepeatedCompositeFieldContainer[Mutation]
    suppress_failure_audit: bool
    def __init__(self, mutations: _Optional[_Iterable[_Union[Mutation, _Mapping]]] = ..., suppress_failure_audit: bool = ...) -> None: ...

class MutationFailure(_message.Message):
    __slots__ = ("path", "desired", "actual", "replacement")
    PATH_FIELD_NUMBER: _ClassVar[int]
    DESIRED_FIELD_NUMBER: _ClassVar[int]
    ACTUAL_FIELD_NUMBER: _ClassVar[int]
    REPLACEMENT_FIELD_NUMBER: _ClassVar[int]
    path: str
    desired: str
    actual: str
    replacement: str
    def __init__(self, path: _Optional[str] = ..., desired: _Optional[str] = ..., actual: _Optional[str] = ..., replacement: _Optional[str] = ...) -> None: ...

class ReplaceManyReply(_message.Message):
    __slots__ = ("success", "failures")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    FAILURES_FIELD_NUMBER: _ClassVar[int]
    success: bool
    failures: _containers.RepeatedCompositeFieldContainer[MutationFailure]
    def __init__(self, success: bool = ..., failures: _Optional[_Iterable[_Union[MutationFailure, _Mapping]]] = ...) -> None: ...

class EnqueueRequest(_message.Message):
    __slots__ = ("queue_name", "work_item", "delay")
    QUEUE_NAME_FIELD_NUMBER: _ClassVar[int]
    WORK_ITEM_FIELD_NUMBER: _ClassVar[int]
    DELAY_FIELD_NUMBER: _ClassVar[int]
    queue_name: str
    work_item: str
    delay: float
    def __init__(self, queue_name: _Optional[str] = ..., work_item: _Optional[str] = ..., delay: _Optional[float] = ...) -> None: ...

class DequeueRequest(_message.Message):
    __slots__ = ("queue_name",)
    QUEUE_NAME_FIELD_NUMBER: _ClassVar[int]
    queue_name: str
    def __init__(self, queue_name: _Optional[str] = ...) -> None: ...

class DequeueReply(_message.Message):
    __slots__ = ("found", "job_name", "work_item")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    JOB_NAME_FIELD_NUMBER: _ClassVar[int]
    WORK_ITEM_FIELD_NUMBER: _ClassVar[int]
    found: bool
    job_name: str
    work_item: str
    def __init__(self, found: bool = ..., job_name: _Optional[str] = ..., work_item: _Optional[str] = ...) -> None: ...

class ResolveRequest(_message.Message):
    __slots__ = ("queue_name", "job_name")
    QUEUE_NAME_FIELD_NUMBER: _ClassVar[int]
    JOB_NAME_FIELD_NUMBER: _ClassVar[int]
    queue_name: str
    job_name: str
    def __init__(self, queue_name: _Optional[str] = ..., job_name: _Optional[str] = ...) -> None: ...

class QueueLengthRequest(_message.Message):
    __slots__ = ("queue_name",)
    QUEUE_NAME_FIELD_NUMBER: _ClassVar[int]
    queue_name: str
    def __init__(self, queue_name: _Optional[str] = ...) -> None: ...

class QueueLengthReply(_message.Message):
    __slots__ = ("processing", "queued", "deferred")
    PROCESSING_FIELD_NUMBER: _ClassVar[int]
    QUEUED_FIELD_NUMBER: _ClassVar[int]
    DEFERRED_FIELD_NUMBER: _ClassVar[int]
    processing: int
    queued: int
    deferred: int
    def __init__(self, processing: _Optional[int] = ..., queued: _Optional[int] = ..., deferred: _Optional[int] = ...) -> None: ...

class RestartQueueRequest(_message.Message):
    __slots__ = ("queue_name",)
    QUEUE_NAME_FIELD_NUMBER: _ClassVar[int]
    queue_name: str
    def __init__(self, queue_name: _Optional[str] = ...) -> None: ...

class ClusterLockRequest(_message.Message):
    __slots__ = ("object_type", "subtype", "name", "lock_data")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    LOCK_DATA_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    subtype: str
    name: str
    lock_data: str
    def __init__(self, object_type: _Optional[str] = ..., subtype: _Optional[str] = ..., name: _Optional[str] = ..., lock_data: _Optional[str] = ...) -> None: ...

class ClusterLockReply(_message.Message):
    __slots__ = ("acquired",)
    ACQUIRED_FIELD_NUMBER: _ClassVar[int]
    acquired: bool
    def __init__(self, acquired: bool = ...) -> None: ...

class ClusterReleaseLockRequest(_message.Message):
    __slots__ = ("object_type", "subtype", "name", "lock_data")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    LOCK_DATA_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    subtype: str
    name: str
    lock_data: str
    def __init__(self, object_type: _Optional[str] = ..., subtype: _Optional[str] = ..., name: _Optional[str] = ..., lock_data: _Optional[str] = ...) -> None: ...

class ClusterGetLockHolderRequest(_message.Message):
    __slots__ = ("object_type", "subtype", "name")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUBTYPE_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    subtype: str
    name: str
    def __init__(self, object_type: _Optional[str] = ..., subtype: _Optional[str] = ..., name: _Optional[str] = ...) -> None: ...

class ClusterLockHolderReply(_message.Message):
    __slots__ = ("held", "holder")
    HELD_FIELD_NUMBER: _ClassVar[int]
    HOLDER_FIELD_NUMBER: _ClassVar[int]
    held: bool
    holder: str
    def __init__(self, held: bool = ..., holder: _Optional[str] = ...) -> None: ...

class ClusterClearStaleLocksRequest(_message.Message):
    __slots__ = ("node_name",)
    NODE_NAME_FIELD_NUMBER: _ClassVar[int]
    node_name: str
    def __init__(self, node_name: _Optional[str] = ...) -> None: ...

class ClusterGetExistingLocksRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ClusterLockEntry(_message.Message):
    __slots__ = ("key", "holder")
    KEY_FIELD_NUMBER: _ClassVar[int]
    HOLDER_FIELD_NUMBER: _ClassVar[int]
    key: str
    holder: str
    def __init__(self, key: _Optional[str] = ..., holder: _Optional[str] = ...) -> None: ...

class ClusterGetExistingLocksReply(_message.Message):
    __slots__ = ("locks",)
    LOCKS_FIELD_NUMBER: _ClassVar[int]
    locks: _containers.RepeatedCompositeFieldContainer[ClusterLockEntry]
    def __init__(self, locks: _Optional[_Iterable[_Union[ClusterLockEntry, _Mapping]]] = ...) -> None: ...

class CompactRequest(_message.Message):
    __slots__ = ("revision",)
    REVISION_FIELD_NUMBER: _ClassVar[int]
    revision: int
    def __init__(self, revision: _Optional[int] = ...) -> None: ...

class GetObjectStateRequest(_message.Message):
    __slots__ = ("object_type", "object_uuid")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_UUID_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    object_uuid: str
    def __init__(self, object_type: _Optional[str] = ..., object_uuid: _Optional[str] = ...) -> None: ...

class GetObjectStateReply(_message.Message):
    __slots__ = ("found", "state_value", "update_time", "message")
    FOUND_FIELD_NUMBER: _ClassVar[int]
    STATE_VALUE_FIELD_NUMBER: _ClassVar[int]
    UPDATE_TIME_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    found: bool
    state_value: str
    update_time: float
    message: str
    def __init__(self, found: bool = ..., state_value: _Optional[str] = ..., update_time: _Optional[float] = ..., message: _Optional[str] = ...) -> None: ...

class SetObjectStateRequest(_message.Message):
    __slots__ = ("object_type", "object_uuid", "state_value", "update_time", "message")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_UUID_FIELD_NUMBER: _ClassVar[int]
    STATE_VALUE_FIELD_NUMBER: _ClassVar[int]
    UPDATE_TIME_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    object_uuid: str
    state_value: str
    update_time: float
    message: str
    def __init__(self, object_type: _Optional[str] = ..., object_uuid: _Optional[str] = ..., state_value: _Optional[str] = ..., update_time: _Optional[float] = ..., message: _Optional[str] = ...) -> None: ...

class DeleteObjectStateRequest(_message.Message):
    __slots__ = ("object_type", "object_uuid")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_UUID_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    object_uuid: str
    def __init__(self, object_type: _Optional[str] = ..., object_uuid: _Optional[str] = ...) -> None: ...

class GetObjectsByStateRequest(_message.Message):
    __slots__ = ("object_type", "state_values")
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    STATE_VALUES_FIELD_NUMBER: _ClassVar[int]
    object_type: str
    state_values: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, object_type: _Optional[str] = ..., state_values: _Optional[_Iterable[str]] = ...) -> None: ...

class GetObjectsByStateReply(_message.Message):
    __slots__ = ("object_uuids",)
    OBJECT_UUIDS_FIELD_NUMBER: _ClassVar[int]
    object_uuids: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, object_uuids: _Optional[_Iterable[str]] = ...) -> None: ...
