# Audit: Console script logging setup

## What we check

Projects using `shakenfist_utilities.logs.setup_console()` in their
CLI entry point must also configure the root logger so that INFO
messages from all module loggers are visible.

Required pattern:

```python
LOG = logs.setup_console(__name__)
logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).propagate = False
```

When `--verbose` is used, update the root handler level:

```python
if verbose:
    logging.root.setLevel(logging.DEBUG)
    for handler in logging.root.handlers:
        handler.setLevel(logging.DEBUG)
    LOG.setLevel(logging.DEBUG)
```

## Template

No template -- this is a code-level pattern.

## Projects

| Project | Status | Issue |
|---------|--------|-------|
| agent-python | needs checking | |
| client-python | needs checking | |
| clingwrap | needs checking | |
| cloudgood | N/A | - |
| imago | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | compliant | - |
| ryll | N/A | - |
| shakenfist | needs checking | |

N/A: Does not use `shakenfist_utilities.logs.setup_console()`.
