#!/usr/bin/env python3
"""Generate protobuf enum definitions from Python pydantic schemas.

This script parses Python enum classes from the shakenfist.schema package and
generates corresponding protobuf enum definitions. This ensures the protobuf
enums are always in sync with the Python source of truth.

The script uses AST parsing to avoid needing to import the full shakenfist
package with all its dependencies (pydantic, etc). This makes it usable in
minimal environments like the proto generation step.

Usage:
    python3 _generate_enums.py > shakenfist_enums.proto

The generated .proto file should be imported by other .proto files that need
to use these enum types.
"""

import ast
from pathlib import Path
from typing import NamedTuple


class EnumMember(NamedTuple):
    """A single enum member with its name, string value, and protobuf ID."""
    name: str
    string_value: str
    proto_id: int


class EnumDefinition(NamedTuple):
    """An enum class with all its members."""
    name: str
    members: list[EnumMember]
    source_file: str


def parse_enum_from_file(
    file_path: Path, enum_name: str, value_type_name: str
) -> EnumDefinition:
    """Parse an enum class from a Python file using AST.

    Args:
        file_path: Path to the Python file containing the enum
        enum_name: Name of the enum class to parse
        value_type_name: Name of the NamedTuple value type (e.g., ObjectTypeValue)

    Returns:
        An EnumDefinition with the parsed members

    Raises:
        ValueError: If the enum class is not found in the file
    """
    with open(file_path, 'r') as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == enum_name:
            members = []
            for item in node.body:
                # Look for assignments like:
                # INSTANCE = ObjectTypeValue(string='instance', proto_id=5)
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            # Check for the NamedTuple constructor call
                            if isinstance(item.value, ast.Call):
                                func = item.value.func
                                if isinstance(func, ast.Name):
                                    if func.id == value_type_name:
                                        # Extract keyword arguments
                                        string_value = None
                                        proto_id = None
                                        for kw in item.value.keywords:
                                            if kw.arg == 'string':
                                                if isinstance(kw.value, ast.Constant):
                                                    string_value = kw.value.value
                                            elif kw.arg == 'proto_id':
                                                if isinstance(kw.value, ast.Constant):
                                                    proto_id = kw.value.value
                                        if string_value and proto_id is not None:
                                            members.append(EnumMember(
                                                name=target.id,
                                                string_value=string_value,
                                                proto_id=proto_id
                                            ))
            return EnumDefinition(
                name=enum_name,
                members=members,
                source_file=str(file_path.relative_to(file_path.parent.parent))
            )

    raise ValueError(f'Enum class {enum_name} not found in {file_path}')


def python_enum_to_proto_name(enum_name: str, value_name: str) -> str:
    """Convert a Python enum value name to protobuf convention.

    Protobuf enum values should be prefixed with the enum name to avoid
    collisions. For example, ObjectType.INSTANCE becomes OBJECT_TYPE_INSTANCE.

    Args:
        enum_name: The name of the enum class (e.g., 'ObjectType')
        value_name: The name of the enum value (e.g., 'INSTANCE')

    Returns:
        The protobuf-style name (e.g., 'OBJECT_TYPE_INSTANCE')
    """
    # Convert CamelCase to UPPER_SNAKE_CASE
    # ObjectType -> OBJECT_TYPE
    # ReservationType -> RESERVATION_TYPE
    prefix_parts = []
    for i, char in enumerate(enum_name):
        if char.isupper() and i > 0:
            prefix_parts.append('_')
        prefix_parts.append(char.upper())
    prefix = ''.join(prefix_parts)

    return f'{prefix}_{value_name}'


def generate_proto_enum(enum_def: EnumDefinition) -> list[str]:
    """Generate protobuf enum definition from a parsed enum.

    Args:
        enum_def: The parsed enum definition

    Returns:
        Lines of protobuf enum definition
    """
    lines = []
    lines.append(f'enum {enum_def.name} {{')

    # Protobuf requires the first value to be 0 (the default/unspecified value)
    # We add an UNSPECIFIED value that doesn't exist in the Python enum
    prefix = python_enum_to_proto_name(enum_def.name, '').rstrip('_')
    lines.append(f'  {prefix}_UNSPECIFIED = 0;')

    # Generate values using the explicit proto_id from each member
    for member in enum_def.members:
        proto_name = python_enum_to_proto_name(enum_def.name, member.name)
        # Add a comment showing the Python string value for reference
        lines.append(
            f'  {proto_name} = {member.proto_id};  // "{member.string_value}"')

    lines.append('}')
    return lines


def generate_proto_file() -> str:
    """Generate the complete .proto file with all enums.

    Returns:
        The complete protobuf file content as a string
    """
    # Locate the schema files relative to this script
    protos_dir = Path(__file__).parent
    schema_dir = protos_dir.parent / 'shakenfist' / 'schema'

    # Parse the enums from source files
    object_type = parse_enum_from_file(
        schema_dir / 'object_types.py', 'ObjectType', 'ObjectTypeValue')
    reservation_type = parse_enum_from_file(
        schema_dir / 'ipam_reservation.py', 'ReservationType',
        'ReservationTypeValue')

    lines = []

    # File header
    lines.append('// Auto-generated by _generate_enums.py - DO NOT EDIT')
    lines.append('//')
    lines.append('// This file is generated from Python enum definitions in')
    lines.append('// shakenfist/schema/. To regenerate:')
    lines.append('//')
    lines.append('//   cd protos')
    lines.append('//   python3 _generate_enums.py > shakenfist_enums.proto')
    lines.append('//')
    lines.append(f'// Source of truth: {object_type.source_file}')
    lines.append(f'//                  {reservation_type.source_file}')
    lines.append('')
    lines.append('syntax = "proto3";')
    lines.append('')
    lines.append('package shakenfist.protos;')
    lines.append('')

    # Generate ObjectType enum
    lines.append('// ObjectType enum - all valid object types in Shaken Fist')
    lines.append(f'// Maps to Python: shakenfist.schema.object_types.{object_type.name}')
    lines.extend(generate_proto_enum(object_type))
    lines.append('')

    # Generate ReservationType enum
    lines.append('// ReservationType enum - IPAM reservation types')
    lines.append(
        f'// Maps to Python: shakenfist.schema.ipam_reservation.{reservation_type.name}')
    lines.extend(generate_proto_enum(reservation_type))
    lines.append('')

    return '\n'.join(lines)


if __name__ == '__main__':
    print(generate_proto_file())
