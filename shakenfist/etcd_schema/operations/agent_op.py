from enum import Enum
from typing import List
from typing import Literal
from typing import Union

from pydantic import BaseModel
from pydantic import Field
from pydantic import UUID4


object_type = 'agentoperation'
initial_version = 1
current_version = 1


class command_types(str, Enum):
    get_file = 'get-file'
    put_blob = 'put-blob'
    chmod = 'chmod'
    execute = 'execute'


class get_file_command(BaseModel):
    command: Literal[command_types.get_file] = Field(
        default=command_types.get_file)
    path: str


class put_blob_command(BaseModel):
    command: Literal[command_types.put_blob] = Field(
        default=command_types.put_blob)
    blob_uuid: UUID4
    path: str


class chmod_command(BaseModel):
    command: Literal[command_types.chmod] = Field(
        default=command_types.chmod)
    path: str
    mode: str


class execute_command(BaseModel):
    command: Literal[command_types.execute] = Field(
        default=command_types.execute)
    commandline: str
    block: bool = Field(False, alias='block-for-result')

    class Config:
        # This is required so that we can use "block" for the field name
        # in python code, but still get "block-for-result" in JSON dictionaries
        # as dictated by our historical mistakes.
        populate_by_name = True


class model(BaseModel):
    uuid: UUID4
    namespace: str
    instance_uuid: UUID4
    commands: List[
        Union[
            get_file_command, put_blob_command, chmod_command, execute_command
        ]
    ]
    version: int = Field(ge=initial_version, le=current_version)
