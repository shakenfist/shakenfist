import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CommandError(_message.Message):
    __slots__ = ("error",)
    ERROR_FIELD_NUMBER: _ClassVar[int]
    error: str
    def __init__(self, error: _Optional[str] = ...) -> None: ...

class UnknownCommand(_message.Message):
    __slots__ = ("command",)
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    command: str
    def __init__(self, command: _Optional[str] = ...) -> None: ...

class HypervisorWelcome(_message.Message):
    __slots__ = ("version",)
    VERSION_FIELD_NUMBER: _ClassVar[int]
    version: str
    def __init__(self, version: _Optional[str] = ...) -> None: ...

class AgentWelcome(_message.Message):
    __slots__ = ("version", "boot_time")
    VERSION_FIELD_NUMBER: _ClassVar[int]
    BOOT_TIME_FIELD_NUMBER: _ClassVar[int]
    version: str
    boot_time: float
    def __init__(self, version: _Optional[str] = ..., boot_time: _Optional[float] = ...) -> None: ...

class HypervisorDeparture(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class AgentDeparture(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class PingRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class PingReply(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class IsSystemRunningRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class IsSystemRunningReply(_message.Message):
    __slots__ = ("result", "message", "boot_time")
    RESULT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    BOOT_TIME_FIELD_NUMBER: _ClassVar[int]
    result: bool
    message: str
    boot_time: float
    def __init__(self, result: bool = ..., message: _Optional[str] = ..., boot_time: _Optional[float] = ...) -> None: ...

class Fact(_message.Message):
    __slots__ = ("name", "value")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    name: str
    value: str
    def __init__(self, name: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class MountPoint(_message.Message):
    __slots__ = ("device", "mount_point", "vfs_type")
    DEVICE_FIELD_NUMBER: _ClassVar[int]
    MOUNT_POINT_FIELD_NUMBER: _ClassVar[int]
    VFS_TYPE_FIELD_NUMBER: _ClassVar[int]
    device: str
    mount_point: str
    vfs_type: str
    def __init__(self, device: _Optional[str] = ..., mount_point: _Optional[str] = ..., vfs_type: _Optional[str] = ...) -> None: ...

class GatherFactsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class GatherFactsReply(_message.Message):
    __slots__ = ("distro_facts", "mount_points", "ssh_host_keys")
    DISTRO_FACTS_FIELD_NUMBER: _ClassVar[int]
    MOUNT_POINTS_FIELD_NUMBER: _ClassVar[int]
    SSH_HOST_KEYS_FIELD_NUMBER: _ClassVar[int]
    distro_facts: _containers.RepeatedCompositeFieldContainer[Fact]
    mount_points: _containers.RepeatedCompositeFieldContainer[MountPoint]
    ssh_host_keys: _containers.RepeatedCompositeFieldContainer[Fact]
    def __init__(self, distro_facts: _Optional[_Iterable[_Union[Fact, _Mapping]]] = ..., mount_points: _Optional[_Iterable[_Union[MountPoint, _Mapping]]] = ..., ssh_host_keys: _Optional[_Iterable[_Union[Fact, _Mapping]]] = ...) -> None: ...

class FileStat(_message.Message):
    __slots__ = ("st_mode", "st_size", "st_uid", "st_gid", "st_atime", "st_mtime", "st_ctime")
    ST_MODE_FIELD_NUMBER: _ClassVar[int]
    ST_SIZE_FIELD_NUMBER: _ClassVar[int]
    ST_UID_FIELD_NUMBER: _ClassVar[int]
    ST_GID_FIELD_NUMBER: _ClassVar[int]
    ST_ATIME_FIELD_NUMBER: _ClassVar[int]
    ST_MTIME_FIELD_NUMBER: _ClassVar[int]
    ST_CTIME_FIELD_NUMBER: _ClassVar[int]
    st_mode: int
    st_size: int
    st_uid: int
    st_gid: int
    st_atime: float
    st_mtime: float
    st_ctime: float
    def __init__(self, st_mode: _Optional[int] = ..., st_size: _Optional[int] = ..., st_uid: _Optional[int] = ..., st_gid: _Optional[int] = ..., st_atime: _Optional[float] = ..., st_mtime: _Optional[float] = ..., st_ctime: _Optional[float] = ...) -> None: ...

class FileChunk(_message.Message):
    __slots__ = ("path", "offset", "length", "encoding", "payload")
    class Encoding(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        BASE64: _ClassVar[FileChunk.Encoding]
    BASE64: FileChunk.Encoding
    PATH_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    LENGTH_FIELD_NUMBER: _ClassVar[int]
    ENCODING_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    path: str
    offset: int
    length: int
    encoding: FileChunk.Encoding
    payload: str
    def __init__(self, path: _Optional[str] = ..., offset: _Optional[int] = ..., length: _Optional[int] = ..., encoding: _Optional[_Union[FileChunk.Encoding, str]] = ..., payload: _Optional[str] = ...) -> None: ...

class PutFileRequest(_message.Message):
    __slots__ = ("path", "stat", "first_chunk")
    PATH_FIELD_NUMBER: _ClassVar[int]
    STAT_FIELD_NUMBER: _ClassVar[int]
    FIRST_CHUNK_FIELD_NUMBER: _ClassVar[int]
    path: str
    stat: FileStat
    first_chunk: FileChunk
    def __init__(self, path: _Optional[str] = ..., stat: _Optional[_Union[FileStat, _Mapping]] = ..., first_chunk: _Optional[_Union[FileChunk, _Mapping]] = ...) -> None: ...

class FileChunkReply(_message.Message):
    __slots__ = ("path", "offset")
    PATH_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    path: str
    offset: int
    def __init__(self, path: _Optional[str] = ..., offset: _Optional[int] = ...) -> None: ...

class ChmodRequest(_message.Message):
    __slots__ = ("path", "mode")
    PATH_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    path: str
    mode: str
    def __init__(self, path: _Optional[str] = ..., mode: _Optional[str] = ...) -> None: ...

class ChmodReply(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class ChownRequest(_message.Message):
    __slots__ = ("path", "user", "group")
    PATH_FIELD_NUMBER: _ClassVar[int]
    USER_FIELD_NUMBER: _ClassVar[int]
    GROUP_FIELD_NUMBER: _ClassVar[int]
    path: str
    user: str
    group: str
    def __init__(self, path: _Optional[str] = ..., user: _Optional[str] = ..., group: _Optional[str] = ...) -> None: ...

class ChownReply(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class GetFileRequest(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class WatchFileRequest(_message.Message):
    __slots__ = ("path",)
    PATH_FIELD_NUMBER: _ClassVar[int]
    path: str
    def __init__(self, path: _Optional[str] = ...) -> None: ...

class AgentRequestCommand(_message.Message):
    __slots__ = ("command_id", "hypervisor_welcome", "hypervisor_departure", "command_error", "unknown_command", "ping_request", "execute_request", "is_system_running_request", "gather_facts_request", "put_file_request", "file_chunk", "chmod_request", "chown_request", "get_file_request", "watch_file_request")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    HYPERVISOR_WELCOME_FIELD_NUMBER: _ClassVar[int]
    HYPERVISOR_DEPARTURE_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ERROR_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_COMMAND_FIELD_NUMBER: _ClassVar[int]
    PING_REQUEST_FIELD_NUMBER: _ClassVar[int]
    EXECUTE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    IS_SYSTEM_RUNNING_REQUEST_FIELD_NUMBER: _ClassVar[int]
    GATHER_FACTS_REQUEST_FIELD_NUMBER: _ClassVar[int]
    PUT_FILE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    FILE_CHUNK_FIELD_NUMBER: _ClassVar[int]
    CHMOD_REQUEST_FIELD_NUMBER: _ClassVar[int]
    CHOWN_REQUEST_FIELD_NUMBER: _ClassVar[int]
    GET_FILE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    WATCH_FILE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    hypervisor_welcome: HypervisorWelcome
    hypervisor_departure: HypervisorDeparture
    command_error: CommandError
    unknown_command: UnknownCommand
    ping_request: PingRequest
    execute_request: _common_pb2.ExecuteRequest
    is_system_running_request: IsSystemRunningRequest
    gather_facts_request: GatherFactsRequest
    put_file_request: PutFileRequest
    file_chunk: FileChunk
    chmod_request: ChmodRequest
    chown_request: ChownRequest
    get_file_request: GetFileRequest
    watch_file_request: WatchFileRequest
    def __init__(self, command_id: _Optional[str] = ..., hypervisor_welcome: _Optional[_Union[HypervisorWelcome, _Mapping]] = ..., hypervisor_departure: _Optional[_Union[HypervisorDeparture, _Mapping]] = ..., command_error: _Optional[_Union[CommandError, _Mapping]] = ..., unknown_command: _Optional[_Union[UnknownCommand, _Mapping]] = ..., ping_request: _Optional[_Union[PingRequest, _Mapping]] = ..., execute_request: _Optional[_Union[_common_pb2.ExecuteRequest, _Mapping]] = ..., is_system_running_request: _Optional[_Union[IsSystemRunningRequest, _Mapping]] = ..., gather_facts_request: _Optional[_Union[GatherFactsRequest, _Mapping]] = ..., put_file_request: _Optional[_Union[PutFileRequest, _Mapping]] = ..., file_chunk: _Optional[_Union[FileChunk, _Mapping]] = ..., chmod_request: _Optional[_Union[ChmodRequest, _Mapping]] = ..., chown_request: _Optional[_Union[ChownRequest, _Mapping]] = ..., get_file_request: _Optional[_Union[GetFileRequest, _Mapping]] = ..., watch_file_request: _Optional[_Union[WatchFileRequest, _Mapping]] = ...) -> None: ...

class AgentRequest(_message.Message):
    __slots__ = ("commands",)
    COMMANDS_FIELD_NUMBER: _ClassVar[int]
    commands: _containers.RepeatedCompositeFieldContainer[AgentRequestCommand]
    def __init__(self, commands: _Optional[_Iterable[_Union[AgentRequestCommand, _Mapping]]] = ...) -> None: ...

class AgentReplyCommand(_message.Message):
    __slots__ = ("command_id", "agent_welcome", "agent_departure", "command_error", "unknown_command", "ping_reply", "execute_reply", "is_system_running_reply", "gather_facts_reply", "file_chunk_reply", "chmod_reply", "chown_reply", "file_chunk")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    AGENT_WELCOME_FIELD_NUMBER: _ClassVar[int]
    AGENT_DEPARTURE_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ERROR_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_COMMAND_FIELD_NUMBER: _ClassVar[int]
    PING_REPLY_FIELD_NUMBER: _ClassVar[int]
    EXECUTE_REPLY_FIELD_NUMBER: _ClassVar[int]
    IS_SYSTEM_RUNNING_REPLY_FIELD_NUMBER: _ClassVar[int]
    GATHER_FACTS_REPLY_FIELD_NUMBER: _ClassVar[int]
    FILE_CHUNK_REPLY_FIELD_NUMBER: _ClassVar[int]
    CHMOD_REPLY_FIELD_NUMBER: _ClassVar[int]
    CHOWN_REPLY_FIELD_NUMBER: _ClassVar[int]
    FILE_CHUNK_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    agent_welcome: AgentWelcome
    agent_departure: AgentDeparture
    command_error: CommandError
    unknown_command: UnknownCommand
    ping_reply: PingReply
    execute_reply: _common_pb2.ExecuteReply
    is_system_running_reply: IsSystemRunningReply
    gather_facts_reply: GatherFactsReply
    file_chunk_reply: FileChunkReply
    chmod_reply: ChmodReply
    chown_reply: ChownReply
    file_chunk: FileChunk
    def __init__(self, command_id: _Optional[str] = ..., agent_welcome: _Optional[_Union[AgentWelcome, _Mapping]] = ..., agent_departure: _Optional[_Union[AgentDeparture, _Mapping]] = ..., command_error: _Optional[_Union[CommandError, _Mapping]] = ..., unknown_command: _Optional[_Union[UnknownCommand, _Mapping]] = ..., ping_reply: _Optional[_Union[PingReply, _Mapping]] = ..., execute_reply: _Optional[_Union[_common_pb2.ExecuteReply, _Mapping]] = ..., is_system_running_reply: _Optional[_Union[IsSystemRunningReply, _Mapping]] = ..., gather_facts_reply: _Optional[_Union[GatherFactsReply, _Mapping]] = ..., file_chunk_reply: _Optional[_Union[FileChunkReply, _Mapping]] = ..., chmod_reply: _Optional[_Union[ChmodReply, _Mapping]] = ..., chown_reply: _Optional[_Union[ChownReply, _Mapping]] = ..., file_chunk: _Optional[_Union[FileChunk, _Mapping]] = ...) -> None: ...

class AgentReply(_message.Message):
    __slots__ = ("commands",)
    COMMANDS_FIELD_NUMBER: _ClassVar[int]
    commands: _containers.RepeatedCompositeFieldContainer[AgentReplyCommand]
    def __init__(self, commands: _Optional[_Iterable[_Union[AgentReplyCommand, _Mapping]]] = ...) -> None: ...
