---
name: add-grpc-service
description: Use this skill when adding new gRPC service methods to the Shaken Fist database microservice - defining proto messages, generating stubs, implementing handlers with Prometheus counters, and adding client functions in mariadb.py.
---

# Add gRPC Service Methods

This skill guides adding new gRPC service methods to the Shaken Fist database
microservice. The database service provides a centralized gRPC interface for
all database operations, with Prometheus metrics for observability.

## When to Use This Skill

Use this skill when:
- Adding new database operations (MariaDB)
- Exposing new query patterns to other daemons
- Implementing data migrations that need service-level access
- Adding CRUD operations for new data types

## Prerequisites

- Understanding of Protocol Buffers syntax
- Familiarity with the database daemon architecture
- Knowledge of the data being exposed

## File Modification Order

| Step | File | Purpose |
|------|------|---------|
| 1 | `protos/database.proto` | Define messages and RPC methods |
| 2 | Run `tox -e genprotos` | Generate Python stubs |
| 3 | `shakenfist/daemons/database/main.py` | Implement gRPC handlers |
| 4 | `shakenfist/mariadb.py` (or other module) | Add client functions |

## Detailed Steps

### Step 1: Define Protocol Buffer Messages

Edit `protos/database.proto`:

#### 1.1 Add RPC Methods to Service

Add new RPC methods to the `DatabaseService` block. Group related operations
with a comment header:

```protobuf
service DatabaseService {
  // ... existing RPCs ...

  // {Feature} Operations (MariaDB)
  rpc Create{Object} (Create{Object}Request) returns (StatusReply) {}
  rpc Get{Object} (Get{Object}Request) returns (Get{Object}Reply) {}
  rpc Get{Object}s (Get{Object}sRequest) returns (Get{Object}sReply) {}
  rpc Update{Object} (Update{Object}Request) returns (StatusReply) {}
  rpc Delete{Object} (Delete{Object}Request) returns (StatusReply) {}
}
```

#### 1.2 Define Messages

Add message definitions at the end of the file:

```protobuf
// {Feature} Operations (MariaDB)
// [Brief description of what this data represents]

message {Object}Data {
  string uuid = 1;           // UUID as string (consistent with other messages)
  string field1 = 2;         // [describe field]
  double timestamp = 3;      // Unix timestamp
  int32 version = 4;         // Object version number
  // Add more fields as needed
}

message Create{Object}Request {
  {Object}Data data = 1;
}

message Get{Object}Request {
  string uuid = 1;
}

message Get{Object}Reply {
  bool found = 1;
  {Object}Data data = 2;
}

message Get{Object}sRequest {
  // Add filter parameters if needed
}

message Get{Object}sReply {
  repeated {Object}Data items = 1;
}

message Update{Object}Request {
  {Object}Data data = 1;
}

message Delete{Object}Request {
  string uuid = 1;
}
```

#### Message Design Patterns

**Common reply types (reuse these):**
- `StatusReply` - For operations that return success/error
- `DeleteCountReply` - For bulk deletes that return count
- `CountReply` - For count queries

**Field types:**
- Use `string` for UUIDs (for consistency with existing code)
- Use `double` for Unix timestamps
- Use `int32` for version numbers and counts
- Use `repeated` for lists
- Use nested messages for complex structures

**Using enums:**
Enums are auto-generated from Python source. If you need a new enum:

1. Define in Python (e.g., `shakenfist/schema/object_types.py`)
2. Add to `protos/generate_enums.py` if needed
3. Import in proto: `import "shakenfist_enums.proto";`
4. Use: `ObjectType object_type = 5;`

### Step 2: Generate Python Stubs

```bash
tox -e genprotos
```

This will:
1. Generate enum definitions from Python source
2. Compile `.proto` files to Python
3. Generate type stubs (`.pyi` files)
4. Fix import statements

**Always use `tox -e genprotos`** - never run protoc directly.

After generation, verify the new files:
```bash
ls -la shakenfist/protos/database_pb2*.py*
```

### Step 3: Implement gRPC Handlers

Edit `shakenfist/daemons/database/main.py`:

#### 3.1 Add Handler Methods

Add methods to the `DatabaseService` class:

```python
# {Feature} Operations (MariaDB)
# [Brief description]

def Create{Object}(
    self,
    request: database_pb2.Create{Object}Request,
    context: grpc.ServicerContext
) -> database_pb2.StatusReply:
    """Create a {object} record in MariaDB."""
    try:
        self.monitor.counters['create_{object}'].inc()
        success = mariadb._direct_create_{object}(
            UUID(request.data.uuid),
            request.data.field1,
            request.data.timestamp,
            request.data.version
        )
        return database_pb2.StatusReply(success=success, error='')
    except Exception as e:
        util_exceptions.ignore_exception(
            'database Create{Object} failed', e)
        return database_pb2.StatusReply(success=False, error=str(e))


def Get{Object}(
    self,
    request: database_pb2.Get{Object}Request,
    context: grpc.ServicerContext
) -> database_pb2.Get{Object}Reply:
    """Get {object} data from MariaDB."""
    try:
        self.monitor.counters['get_{object}'].inc()
        data = mariadb._direct_get_{object}(UUID(request.uuid))
        if data is None:
            return database_pb2.Get{Object}Reply(found=False)
        return database_pb2.Get{Object}Reply(
            found=True,
            data=database_pb2.{Object}Data(
                uuid=data['uuid'],
                field1=data['field1'],
                timestamp=data['timestamp'],
                version=data['version']
            )
        )
    except Exception as e:
        util_exceptions.ignore_exception(
            'database Get{Object} failed', e)
        context.set_code(grpc.StatusCode.INTERNAL)
        context.set_details(str(e))
        return database_pb2.Get{Object}Reply(found=False)


def Get{Object}s(
    self,
    request: database_pb2.Get{Object}sRequest,
    context: grpc.ServicerContext
) -> database_pb2.Get{Object}sReply:
    """Get all {objects} from MariaDB."""
    try:
        self.monitor.counters['get_{object}s'].inc()
        items = mariadb._direct_get_all_{object}s()
        return database_pb2.Get{Object}sReply(
            items=[
                database_pb2.{Object}Data(
                    uuid=item['uuid'],
                    field1=item['field1'],
                    timestamp=item['timestamp'],
                    version=item['version']
                )
                for item in items
            ]
        )
    except Exception as e:
        util_exceptions.ignore_exception(
            'database Get{Object}s failed', e)
        return database_pb2.Get{Object}sReply(items=[])


def Delete{Object}(
    self,
    request: database_pb2.Delete{Object}Request,
    context: grpc.ServicerContext
) -> database_pb2.StatusReply:
    """Delete a {object} record from MariaDB."""
    try:
        self.monitor.counters['delete_{object}'].inc()
        success = mariadb._direct_delete_{object}(UUID(request.uuid))
        return database_pb2.StatusReply(success=success, error='')
    except Exception as e:
        util_exceptions.ignore_exception(
            'database Delete{Object} failed', e)
        return database_pb2.StatusReply(success=False, error=str(e))
```

#### 3.2 Add Prometheus Counters

In the `Monitor` class `__init__` method, add counter names to the operations
list:

```python
operations = [
    # ... existing operations ...
    # {Feature} operations
    'create_{object}', 'get_{object}', 'get_{object}s', 'delete_{object}'
]
```

### Step 4: Add Client Functions

Edit `shakenfist/mariadb.py` (or the appropriate module):

#### 4.1 Add gRPC Client Functions

```python
def _grpc_create_{object}(obj_uuid: UUID, field1: str, timestamp: float,
                          version: int) -> bool:
    """Create a {object} record via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.Create{Object}Request(
            data=database_pb2.{Object}Data(
                uuid=str(obj_uuid),
                field1=field1,
                timestamp=timestamp,
                version=version
            )
        )
        reply = stub.Create{Object}(request)
        return bool(reply.success)
    except grpc.RpcError as e:
        LOG.warning(f'gRPC Create{Object} failed for {obj_uuid}: {e}')
        return False


def _grpc_get_{object}(obj_uuid: UUID) -> Optional[dict[str, Any]]:
    """Get {object} data via the database microservice."""
    try:
        stub = _get_database_stub()
        request = database_pb2.Get{Object}Request(uuid=str(obj_uuid))
        reply = stub.Get{Object}(request)
        if not reply.found:
            return None
        return {
            'uuid': reply.data.uuid,
            'field1': reply.data.field1,
            'timestamp': reply.data.timestamp,
            'version': reply.data.version
        }
    except grpc.RpcError as e:
        LOG.warning(f'gRPC Get{Object} failed for {obj_uuid}: {e}')
        return None


# Add _grpc_get_all_{object}s and _grpc_delete_{object} similarly
```

#### 4.2 Add Public API Functions

```python
def create_{object}(obj_uuid: UUID, field1: str, timestamp: float,
                    version: int) -> bool:
    """Create a {object} record."""
    if _use_database_service():
        return _grpc_create_{object}(obj_uuid, field1, timestamp, version)
    return _direct_create_{object}(obj_uuid, field1, timestamp, version)


def get_{object}(obj_uuid: UUID) -> Optional[dict[str, Any]]:
    """Get {object} data."""
    if _use_database_service():
        return _grpc_get_{object}(obj_uuid)
    return _direct_get_{object}(obj_uuid)


# Add get_all_{object}s and delete_{object} similarly
```

## Handler Patterns

### Error Handling

Always wrap handler logic in try/except:

```python
try:
    self.monitor.counters['operation_name'].inc()
    # ... operation logic ...
except Exception as e:
    util_exceptions.ignore_exception('database OperationName failed', e)
    return database_pb2.StatusReply(success=False, error=str(e))
```

### Setting gRPC Status Codes

For Get operations that might not find data:

```python
if data is None:
    return database_pb2.Get{Object}Reply(found=False)
```

For operations that fail unexpectedly:

```python
except Exception as e:
    context.set_code(grpc.StatusCode.INTERNAL)
    context.set_details(str(e))
    return database_pb2.Get{Object}Reply(found=False)
```

### UUID Handling

- Proto messages use `string` for UUIDs
- Convert to `UUID` when calling direct functions: `UUID(request.uuid)`
- Convert back to `string` for replies: `str(uuid_obj)`

## Verification

1. **Proto compilation**: `tox -e genprotos` completes without errors
2. **Type checking**: `tox -e mypy` passes
3. **Unit tests**: Add tests that mock the gRPC calls
4. **Integration test**: Verify round-trip through the service

## Common Pitfalls

- **Forgetting to regenerate stubs** after proto changes
- **Missing Prometheus counters** - add to Monitor class
- **Inconsistent UUID handling** - always convert at boundaries
- **Missing error handling** - wrap all handlers in try/except
- **Not using `_use_database_service()`** check in public functions
