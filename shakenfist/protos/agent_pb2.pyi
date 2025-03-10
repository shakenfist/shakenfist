import common_pb2 as _common_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

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
    __slots__ = ("unique",)
    UNIQUE_FIELD_NUMBER: _ClassVar[int]
    unique: str
    def __init__(self, unique: _Optional[str] = ...) -> None: ...

class PingReply(_message.Message):
    __slots__ = ("unique",)
    UNIQUE_FIELD_NUMBER: _ClassVar[int]
    unique: str
    def __init__(self, unique: _Optional[str] = ...) -> None: ...

class IsSystemRunningRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class IsSystemRunningReply(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class AgentRequest(_message.Message):
    __slots__ = ("hypervisor_welcome", "hypervisor_departure", "command_error", "unknown_command", "ping_request", "execute_request")
    HYPERVISOR_WELCOME_FIELD_NUMBER: _ClassVar[int]
    HYPERVISOR_DEPARTURE_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ERROR_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_COMMAND_FIELD_NUMBER: _ClassVar[int]
    PING_REQUEST_FIELD_NUMBER: _ClassVar[int]
    EXECUTE_REQUEST_FIELD_NUMBER: _ClassVar[int]
    hypervisor_welcome: HypervisorWelcome
    hypervisor_departure: HypervisorDeparture
    command_error: CommandError
    unknown_command: UnknownCommand
    ping_request: PingRequest
    execute_request: _common_pb2.ExecuteRequest
    def __init__(self, hypervisor_welcome: _Optional[_Union[HypervisorWelcome, _Mapping]] = ..., hypervisor_departure: _Optional[_Union[HypervisorDeparture, _Mapping]] = ..., command_error: _Optional[_Union[CommandError, _Mapping]] = ..., unknown_command: _Optional[_Union[UnknownCommand, _Mapping]] = ..., ping_request: _Optional[_Union[PingRequest, _Mapping]] = ..., execute_request: _Optional[_Union[_common_pb2.ExecuteRequest, _Mapping]] = ...) -> None: ...

class AgentReply(_message.Message):
    __slots__ = ("agent_welcome", "agent_departure", "command_error", "unknown_command", "ping_reply", "execute_reply")
    AGENT_WELCOME_FIELD_NUMBER: _ClassVar[int]
    AGENT_DEPARTURE_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ERROR_FIELD_NUMBER: _ClassVar[int]
    UNKNOWN_COMMAND_FIELD_NUMBER: _ClassVar[int]
    PING_REPLY_FIELD_NUMBER: _ClassVar[int]
    EXECUTE_REPLY_FIELD_NUMBER: _ClassVar[int]
    agent_welcome: AgentWelcome
    agent_departure: AgentDeparture
    command_error: CommandError
    unknown_command: UnknownCommand
    ping_reply: PingReply
    execute_reply: _common_pb2.ExecuteReply
    def __init__(self, agent_welcome: _Optional[_Union[AgentWelcome, _Mapping]] = ..., agent_departure: _Optional[_Union[AgentDeparture, _Mapping]] = ..., command_error: _Optional[_Union[CommandError, _Mapping]] = ..., unknown_command: _Optional[_Union[UnknownCommand, _Mapping]] = ..., ping_reply: _Optional[_Union[PingReply, _Mapping]] = ..., execute_reply: _Optional[_Union[_common_pb2.ExecuteReply, _Mapping]] = ...) -> None: ...
