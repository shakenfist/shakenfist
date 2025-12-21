# Type Checking with mypy

Shaken Fist is incrementally adopting mypy for static type checking. This page
documents the rollout strategy and guidelines for adding type annotations to
the codebase.

## Current Status

Type checking is enforced for:

- `shakenfist/schema/sqlalchemy.py` - Pydantic to SQLAlchemy conversion

Additional modules will be added over time as they are annotated.

## Running mypy

To run type checking locally:

```bash
# Run mypy via tox (recommended)
tox -e mypy

# Or run mypy directly
mypy --strict --ignore-missing-imports shakenfist/schema/sqlalchemy.py
```

## Configuration

mypy is configured in `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
ignore_missing_imports = true

# Strict checking for typed modules
[[tool.mypy.overrides]]
module = "shakenfist.schema.sqlalchemy"
strict = true
```

The `ignore_missing_imports = true` setting is necessary because some of our
dependencies (like `shakenfist_utilities`) don't have type stubs.

## Rollout Strategy

We're adopting an incremental approach to type checking:

### Phase 1: Schema Module (Current)

Start with the `shakenfist/schema/` module because:

- Pydantic models already have type annotations
- The SQLAlchemy utilities benefit most from type checking
- It's a self-contained module with clear boundaries

### Phase 2: New Code

All new code should include type annotations. When adding new files or
significantly modifying existing ones, add type annotations and enable strict
checking.

### Phase 3: Core Utilities

Gradually add types to utility modules that are used across the codebase:

- `shakenfist/util/`
- `shakenfist/constants.py`
- `shakenfist/config.py`

### Phase 4: Object Classes

Add types to the object model layer:

- `shakenfist/baseobject.py`
- `shakenfist/instance.py`
- `shakenfist/network/`
- etc.

## Guidelines for Adding Types

### Use Modern Syntax

Use Python 3.9+ type annotation syntax:

```python
# Good - use built-in generics
def process(items: list[str]) -> dict[str, int]:
    ...

# Avoid - old typing module generics
def process(items: List[str]) -> Dict[str, int]:
    ...
```

### Use Optional Explicitly

Never use implicit Optional. If a parameter can be None, annotate it
explicitly:

```python
# Good
def find(name: Optional[str] = None) -> Optional[Result]:
    ...

# Bad - implicit Optional
def find(name: str = None) -> Result:  # mypy will reject this
    ...
```

### Use Any Sparingly

When dealing with dynamic types, use `Any` but document why:

```python
from typing import Any

def get_annotation(field: Any) -> Any:
    """Process a type annotation.

    We use Any here because type annotations can be arbitrary types,
    generic aliases, or special forms that don't have a common base.
    """
    ...
```

### Leverage Pydantic

Pydantic models are already typed. Use them as the source of truth for types
in related code:

```python
from shakenfist.schema.operations.node_blob_op import model

def process_operation(op: model) -> None:
    # op.uuid, op.blob_uuid, etc. are all properly typed
    ...
```

### Adding a New Typed Module

1. Add type annotations to all functions and classes
2. Add an override section in `pyproject.toml`:

   ```toml
   [[tool.mypy.overrides]]
   module = "shakenfist.your_module"
   strict = true
   ```

3. Add the module to the tox mypy command in `tox.ini`
4. Run `tox -e mypy` to verify

## Common Patterns

### Type Aliases

Define type aliases for complex types:

```python
from typing import TypeAlias

IndexColumns: TypeAlias = tuple[str, ...]
IndexDefinition: TypeAlias = tuple[str, IndexColumns, bool]
```

### Generic Classes

When writing generic utilities, use TypeVar:

```python
from typing import TypeVar, Generic

T = TypeVar('T', bound='DatabaseBackedObject')

class Repository(Generic[T]):
    def get(self, uuid: str) -> Optional[T]:
        ...
```

### Callable Types

For callbacks and function parameters:

```python
from typing import Callable

def with_retry(
    func: Callable[[], T],
    retries: int = 3
) -> T:
    ...
```

## IDE Integration

Most modern IDEs support mypy integration:

- **VS Code**: Install the Pylance or mypy extensions
- **PyCharm**: Built-in support for type checking
- **vim/neovim**: Use ALE or coc.nvim with mypy

## CI Integration

The mypy check runs as part of CI. Any PR that modifies a typed module must
pass mypy strict checks.
