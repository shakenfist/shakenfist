---
name: add-mypy-coverage
description: Use this skill when adding mypy type annotations to a Shaken Fist module, expanding the incremental mypy rollout in tox.ini, or fixing type errors found during development.
---

# Add Mypy Type Coverage

This skill guides adding mypy type annotations to Shaken Fist modules. Type
annotations help catch errors at development time rather than runtime, and
improve IDE support and code documentation.

## When to Use This Skill

Use this skill when:
- Adding type hints to an existing module
- Creating a new module that should be type-checked
- Fixing type errors found during development
- Expanding mypy coverage as a development goal

## Background

A CI failure caused by passing a string instead of an `ObjectType` enum led
to the mypy rollout initiative. Type annotations catch such errors during
development:

```
# Runtime error without types:
AttributeError: 'str' object has no attribute 'proto_id'

# With types, mypy catches this at dev time:
error: Argument 1 has incompatible type "str"; expected "ObjectType"
```

## Current Coverage

Files already checked in `tox -e mypy` include:

- `shakenfist/schema/*.py` (strict)
- `shakenfist/mariadb.py` (strict, follow-imports=silent)
- `shakenfist/daemons/database/main.py`
- `shakenfist/operations/baseoperation.py`
- `shakenfist/util/*.py`
- `shakenfist/client/ctl.py`
- `shakenfist/blob.py`
- `shakenfist/eventlog.py`

Check `tox.ini` `[testenv:mypy]` for the authoritative, current list.

## Implementation Steps

### Step 1: Analyze the Module

Before adding types, understand the module:

1. **Identify function signatures** - Parameters and return types
2. **Check imports** - What types are used from other modules?
3. **Look for patterns** - Common return types, optional values
4. **Note any circular imports** - May need `TYPE_CHECKING` guards

### Step 2: Add Type Imports

Add typing imports at the top of the file:

```python
from typing import Any
from typing import Optional
from typing import TYPE_CHECKING

# For forward references to avoid circular imports
if TYPE_CHECKING:
    from shakenfist.namespace import Namespace
    from shakenfist.instance import Instance
```

Common types to import:
- `Any` - For truly dynamic values (use sparingly)
- `Optional[X]` - For values that can be `None`
- `Union[X, Y]` - For values that can be multiple types
- `dict[str, Any]` - For dictionaries with string keys
- `list[str]` - For lists of a specific type
- `Callable[[Args], Return]` - For function parameters
- `Generator[Yield, Send, Return]` - For generator functions

### Step 3: Add Function Annotations

#### Basic Pattern

```python
# Before
def process_data(uuid, data, timeout=30):
    ...

# After
def process_data(
    uuid: str, data: dict[str, Any], timeout: int = 30
) -> bool:
    ...
```

#### Common Patterns

**Functions returning None:**
```python
def log_event(message: str) -> None:
    LOG.info(message)
```

**Functions returning Optional:**
```python
def get_object(uuid: str) -> Optional[dict[str, Any]]:
    data = database.get(uuid)
    if not data:
        return None
    return data
```

**Generator functions:**
```python
def get_all_objects() -> Generator[tuple[str, Any], None, None]:
    for key, value in database.scan():
        yield key, value
```

**Class methods:**
```python
class MyObject:
    @classmethod
    def from_db(cls, uuid: str) -> Optional['MyObject']:
        ...

    def hard_delete(self) -> None:
        ...
```

**Methods returning self:**
```python
def with_fields(self, fields: dict[str, Any]) -> 'Logger':
    ...
    return self
```

### Step 4: Handle Forward References

When a type isn't defined yet or would cause circular imports:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shakenfist.instance import Instance

class Network:
    def get_instances(self) -> list['Instance']:
        # Use string quotes for forward reference
        ...
```

### Step 5: Add to tox.ini

Edit `tox.ini` to include the new file in mypy checks:

```ini
[testenv:mypy]
commands =
    # ... existing commands ...
    mypy --ignore-missing-imports --follow-imports=silent \
        --disallow-untyped-defs shakenfist/your_module.py
```

#### Mypy Strictness Levels

Start relaxed and increase strictness over time:

1. **Initial** (for new files):
   ```
   --ignore-missing-imports --follow-imports=silent
   ```

2. **Intermediate** (after basic types added):
   ```
   --ignore-missing-imports --follow-imports=silent --disallow-untyped-defs
   ```

3. **Strict** (for schema files and well-typed modules):
   ```
   --strict
   ```

### Step 6: Run and Fix Errors

```bash
# Check the specific file
tox -e mypy -- shakenfist/your_module.py

# Check all files
tox -e mypy
```

Common errors and fixes:

**Missing return type:**
```
error: Function is missing a return type annotation
# Fix: Add -> ReturnType to function signature
```

**Incompatible types:**
```
error: Incompatible types in assignment (expression has type "str",
       variable has type "int")
# Fix: Correct the type annotation or fix the code
```

**Optional access without check:**
```
error: Item "None" of "Optional[str]" has no attribute "split"
# Fix: Add None check before accessing
if value is not None:
    parts = value.split(',')
```

**Missing import:**
```
error: Cannot find implementation or library stub for module named "xxx"
# Fix: Add to ignore-missing-imports or add stub
```

## Type Annotation Patterns for Shaken Fist

### Object Types

```python
from shakenfist.schema.object_types import ObjectType

def set_state(object_type: ObjectType, uuid: str, state: str) -> None:
    ...
```

### State Objects

```python
from shakenfist.schema.object_state import State

def get_state(self) -> State:
    ...
```

### UUID Handling

```python
from uuid import UUID

def create_object(obj_uuid: UUID) -> bool:
    ...

# For string UUIDs
def get_object(uuid_str: str) -> Optional[dict[str, Any]]:
    ...
```

### Database Operations

```python
def get_all(
    objecttype: str,
    subtype: Optional[str],
    prefix: Optional[str] = None,
    limit: int = 0
) -> Generator[tuple[str, Any], None, None]:
    ...
```

### Pydantic Models

```python
from shakenfist.schema.upload import UploadData

def get_upload(uuid: str) -> Optional[UploadData]:
    data = mariadb.get_upload(uuid)
    if not data:
        return None
    return UploadData(**data)
```

## Verification Checklist

1. **Run mypy on the file:**
   ```bash
   tox -e mypy -- shakenfist/your_module.py
   ```

2. **Run all tests:**
   ```bash
   tox
   ```

3. **Check pre-commit hooks pass:**
   ```bash
   pre-commit run --all-files
   ```

4. **Verify no runtime errors:**
   - Type annotations should not change runtime behavior
   - Test the module functionality after adding types

## Common Pitfalls

### Don't Over-Use Any

`Any` disables type checking. Use specific types when possible:

```python
# Bad
def process(data: Any) -> Any:
    ...

# Better
def process(data: dict[str, str]) -> list[str]:
    ...
```

### Handle Optional Properly

```python
# Bad - will error if value is None
def get_name(obj: Optional[MyObject]) -> str:
    return obj.name  # error: Item "None" has no attribute "name"

# Good
def get_name(obj: Optional[MyObject]) -> Optional[str]:
    if obj is None:
        return None
    return obj.name
```

### Use TYPE_CHECKING for Imports

```python
# Bad - causes circular import
from shakenfist.instance import Instance

# Good - only imported during type checking
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from shakenfist.instance import Instance
```

### String Literals for Forward References

```python
class Parent:
    def get_children(self) -> list['Child']:  # quotes needed
        ...

class Child:
    def get_parent(self) -> 'Parent':  # quotes needed
        ...
```

## Priority Files for Type Coverage

Improving mypy coverage is a standing development goal (see `CLAUDE.md`).
Files not yet in `tox.ini` `[testenv:mypy]` with high impact include:

- `shakenfist/baseobject.py` - Base framework for all persistable objects
- `shakenfist/constants.py` - Constants and object type mappings

## Related Documentation

- [docs/developer_guide/mypy.md](../../../docs/developer_guide/mypy.md) -
  Rollout strategy and guidelines
- [mypy documentation](https://mypy.readthedocs.io/)
- [PEP 484 - Type Hints](https://peps.python.org/pep-0484/)
