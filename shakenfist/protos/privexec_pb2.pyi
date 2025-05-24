import common_pb2 as _common_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class HashAlgorithm(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SHA1: _ClassVar[HashAlgorithm]
    SHA256: _ClassVar[HashAlgorithm]
    SHA512: _ClassVar[HashAlgorithm]
    XXH128: _ClassVar[HashAlgorithm]
SHA1: HashAlgorithm
SHA256: HashAlgorithm
SHA512: HashAlgorithm
XXH128: HashAlgorithm

class HashFileRequest(_message.Message):
    __slots__ = ("path", "algorithm")
    PATH_FIELD_NUMBER: _ClassVar[int]
    ALGORITHM_FIELD_NUMBER: _ClassVar[int]
    path: str
    algorithm: HashAlgorithm
    def __init__(self, path: _Optional[str] = ..., algorithm: _Optional[_Union[HashAlgorithm, str]] = ...) -> None: ...

class HashFileReply(_message.Message):
    __slots__ = ("path", "algorithm", "hash", "error", "error_text")
    class Errors(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        OK: _ClassVar[HashFileReply.Errors]
        FILE_NOT_FOUND: _ClassVar[HashFileReply.Errors]
        UNKNOWN_ALGORITHM: _ClassVar[HashFileReply.Errors]
        ALGORITHM_NOT_FOUND: _ClassVar[HashFileReply.Errors]
        ALGORITHM_FAILED: _ClassVar[HashFileReply.Errors]
    OK: HashFileReply.Errors
    FILE_NOT_FOUND: HashFileReply.Errors
    UNKNOWN_ALGORITHM: HashFileReply.Errors
    ALGORITHM_NOT_FOUND: HashFileReply.Errors
    ALGORITHM_FAILED: HashFileReply.Errors
    PATH_FIELD_NUMBER: _ClassVar[int]
    ALGORITHM_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ERROR_TEXT_FIELD_NUMBER: _ClassVar[int]
    path: str
    algorithm: HashAlgorithm
    hash: str
    error: HashFileReply.Errors
    error_text: str
    def __init__(self, path: _Optional[str] = ..., algorithm: _Optional[_Union[HashAlgorithm, str]] = ..., hash: _Optional[str] = ..., error: _Optional[_Union[HashFileReply.Errors, str]] = ..., error_text: _Optional[str] = ...) -> None: ...

class EnableNATRequest(_message.Message):
    __slots__ = ("network_uuid", "network_address", "network_mask", "vxid")
    NETWORK_UUID_FIELD_NUMBER: _ClassVar[int]
    NETWORK_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    NETWORK_MASK_FIELD_NUMBER: _ClassVar[int]
    VXID_FIELD_NUMBER: _ClassVar[int]
    network_uuid: str
    network_address: str
    network_mask: str
    vxid: int
    def __init__(self, network_uuid: _Optional[str] = ..., network_address: _Optional[str] = ..., network_mask: _Optional[str] = ..., vxid: _Optional[int] = ...) -> None: ...

class EnableNATReply(_message.Message):
    __slots__ = ("network_uuid", "network_address", "network_mask", "vxid", "error", "error_text")
    class Errors(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        OK: _ClassVar[EnableNATReply.Errors]
        IPTABLES_FAILED: _ClassVar[EnableNATReply.Errors]
        RULES_ALREADY_PRESENT: _ClassVar[EnableNATReply.Errors]
    OK: EnableNATReply.Errors
    IPTABLES_FAILED: EnableNATReply.Errors
    RULES_ALREADY_PRESENT: EnableNATReply.Errors
    NETWORK_UUID_FIELD_NUMBER: _ClassVar[int]
    NETWORK_ADDRESS_FIELD_NUMBER: _ClassVar[int]
    NETWORK_MASK_FIELD_NUMBER: _ClassVar[int]
    VXID_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ERROR_TEXT_FIELD_NUMBER: _ClassVar[int]
    network_uuid: str
    network_address: str
    network_mask: str
    vxid: int
    error: EnableNATReply.Errors
    error_text: str
    def __init__(self, network_uuid: _Optional[str] = ..., network_address: _Optional[str] = ..., network_mask: _Optional[str] = ..., vxid: _Optional[int] = ..., error: _Optional[_Union[EnableNATReply.Errors, str]] = ..., error_text: _Optional[str] = ...) -> None: ...

class PrivExecRequest(_message.Message):
    __slots__ = ("execute_request", "hash_file_request", "enable_nat_request")
    EXECUTE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    HASH_FILE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    ENABLE_NAT_REQUEST_FIELD_NUMBER: _ClassVar[int]
    execute_request: _common_pb2.ExecuteRequest
    hash_file_request: HashFileRequest
    enable_nat_request: EnableNATRequest
    def __init__(self, execute_request: _Optional[_Union[_common_pb2.ExecuteRequest, _Mapping]] = ..., hash_file_request: _Optional[_Union[HashFileRequest, _Mapping]] = ..., enable_nat_request: _Optional[_Union[EnableNATRequest, _Mapping]] = ...) -> None: ...

class PrivExecReply(_message.Message):
    __slots__ = ("execute_reply", "hash_file_reply", "enable_nat_reply")
    EXECUTE_REPLY_FIELD_NUMBER: _ClassVar[int]
    HASH_FILE_REPLY_FIELD_NUMBER: _ClassVar[int]
    ENABLE_NAT_REPLY_FIELD_NUMBER: _ClassVar[int]
    execute_reply: _common_pb2.ExecuteReply
    hash_file_reply: HashFileReply
    enable_nat_reply: EnableNATReply
    def __init__(self, execute_reply: _Optional[_Union[_common_pb2.ExecuteReply, _Mapping]] = ..., hash_file_reply: _Optional[_Union[HashFileReply, _Mapping]] = ..., enable_nat_reply: _Optional[_Union[EnableNATReply, _Mapping]] = ...) -> None: ...
