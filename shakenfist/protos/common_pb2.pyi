from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EnvironmentVariable(_message.Message):
    __slots__ = ("name", "value")
    NAME_FIELD_NUMBER: _ClassVar[int]
    VALUE_FIELD_NUMBER: _ClassVar[int]
    name: str
    value: str
    def __init__(self, name: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...

class ExecuteRequest(_message.Message):
    __slots__ = ("command", "environment_variables", "network_namespace", "io_priority", "working_directory", "request_id", "execution_id")
    class IOPriority(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        NORMAL: _ClassVar[ExecuteRequest.IOPriority]
        LOW: _ClassVar[ExecuteRequest.IOPriority]
        HIGH: _ClassVar[ExecuteRequest.IOPriority]
    NORMAL: ExecuteRequest.IOPriority
    LOW: ExecuteRequest.IOPriority
    HIGH: ExecuteRequest.IOPriority
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    ENVIRONMENT_VARIABLES_FIELD_NUMBER: _ClassVar[int]
    NETWORK_NAMESPACE_FIELD_NUMBER: _ClassVar[int]
    IO_PRIORITY_FIELD_NUMBER: _ClassVar[int]
    WORKING_DIRECTORY_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    command: str
    environment_variables: _containers.RepeatedCompositeFieldContainer[EnvironmentVariable]
    network_namespace: str
    io_priority: ExecuteRequest.IOPriority
    working_directory: str
    request_id: str
    execution_id: str
    def __init__(self, command: _Optional[str] = ..., environment_variables: _Optional[_Iterable[_Union[EnvironmentVariable, _Mapping]]] = ..., network_namespace: _Optional[str] = ..., io_priority: _Optional[_Union[ExecuteRequest.IOPriority, str]] = ..., working_directory: _Optional[str] = ..., request_id: _Optional[str] = ..., execution_id: _Optional[str] = ...) -> None: ...

class ExecuteReply(_message.Message):
    __slots__ = ("stdout", "stderr", "exit_code", "request_id", "execution_id", "execution_seconds")
    STDOUT_FIELD_NUMBER: _ClassVar[int]
    STDERR_FIELD_NUMBER: _ClassVar[int]
    EXIT_CODE_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_ID_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    stdout: str
    stderr: str
    exit_code: int
    request_id: str
    execution_id: str
    execution_seconds: float
    def __init__(self, stdout: _Optional[str] = ..., stderr: _Optional[str] = ..., exit_code: _Optional[int] = ..., request_id: _Optional[str] = ..., execution_id: _Optional[str] = ..., execution_seconds: _Optional[float] = ...) -> None: ...
