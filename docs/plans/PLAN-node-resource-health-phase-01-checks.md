# Phase 1: the health-check primitive and the path check

Master plan: [PLAN-node-resource-health.md](PLAN-node-resource-health.md).

## Context

Phase 1 builds the **reusable primitive** the whole model rests on: a
`HealthCheck` abstraction, a deadline-guarded runner for checks that
can block, and the first concrete implementation — a **path check**
that reports whether a storage path is healthy (present, writable, and
not a hung mount).

Phase 1 deliberately delivers **no wiring into node state and no
daemon changes**. It produces a self-contained, fully unit-tested
module that phase 2 (the sf-resources evaluator) consumes. Keeping it
standalone is the point: the check interface must stand on its own so
later check types — "libvirtd is answering", "the mesh has
connectivity" (master plan D3) — slot in beside the path check without
reshaping anything.

Two properties from the master plan drive the design:

- **D4 — two-tier probing.** A cheap `statvfs` read every call
  (raising `OSError` ⇒ the store is gone; `f_flag & ST_RDONLY` ⇒
  read-only remount), plus an authoritative `_heartbeat` write+`fsync`
  no more often than every `write_interval` seconds (default 300),
  which also leaves a forensic "last seen live" timestamp.
- **D5 — deadline-guarded.** sfcbr mounts NFS `hard`, and a hard mount
  *hangs* on server death rather than returning EIO. A `statvfs` or
  write on it blocks forever, so the probe must run under a deadline
  off the calling thread; **a probe that does not return in time is
  itself the unhealthy signal** (`timeout`). This is why the path
  check is the *primary* detector for NFS-backed storage — the
  instance-level `werror=stop` pause never fires on a hang.

## Key references in the existing code

- **`shakenfist/daemons/resources/main.py:247-252`** — the existing
  per-path loop (`for path in ['', 'blobs', 'events', 'image_cache',
  'instances', 'uploads']: os.makedirs(...); os.statvfs(...)`) whose
  EIO is currently swallowed at **`:629`**
  (`util_exceptions.ignore_exception('resource statistics', e)`).
  Phase 1 does **not** touch this; phase 2 replaces the swallow with
  the evaluator. It is the reference for which paths matter and how
  `statvfs` is already used.
- **`shakenfist/util/libvirt.py:1-9`** — the util-module conventions to
  mirror: `from shakenfist_utilities import logs` then
  `LOG, _ = logs.setup(__name__)`; type hints throughout.
- **`shakenfist/config.py:607`** (`STORAGE_PATH` `Field(...)`) — the
  pydantic settings style. Phase 1 adds **no** config; the check takes
  `write_interval` and `timeout` as constructor parameters (phase 2
  wires config → those params).
- **`shakenfist/schema/object_types.py`** — precedent for a small typed
  value object (`ObjectTypeValue(NamedTuple)`); `HealthResult` follows
  the same taste.
- **`shakenfist/tests/test_util_general.py:1-6`** — the unit-test
  pattern: `from shakenfist.tests import base`, subclass
  `base.ShakenFistTestCase`, `unittest.mock`.

## Inherited decisions (from the master plan)

- **D3** — the abstraction admits non-path checks; only the path check
  is built now. Do **not** add other check types speculatively.
- **D4 / D5** — two-tier probe, deadline-guarded (above).
- **D8** — no transitive dependency graph; not relevant to phase 1
  (that is a phase-2 evaluator concern) but noted so the check API
  stays about a *single* resource, not a graph.

## Design

### Module

New module **`shakenfist/resource_health.py`** (deliberately *not*
`health.py` — `shakenfist/external_api/health.py` already exists for
the unrelated sf-api daemon-readiness work, and the two must not be
confused). Public surface:

```python
class HealthStatus:                 # string constants, not an enum, to
    OK = 'ok'                       # match the str-valued style used
    MISSING = 'missing'             # elsewhere (ObjectType is str-Enum;
    READONLY = 'readonly'           # these are simpler still)
    UNWRITABLE = 'unwritable'
    TIMEOUT = 'timeout'

@dataclass(frozen=True)
class HealthResult:
    identity: str                   # which check produced this
    status: str                     # one of HealthStatus.*
    detail: str | None = None       # human-readable, e.g. the OSError
    @property
    def healthy(self) -> bool:
        return self.status == HealthStatus.OK

class HealthCheck(abc.ABC):
    @property
    @abc.abstractmethod
    def identity(self) -> str: ...  # stable id; the evaluator dedups on it
    @abc.abstractmethod
    def check(self) -> HealthResult: ...
```

### The deadline runner (the reusable, subtle part)

A small helper `DeadlineProbe` runs a callable under a wall-clock
deadline and **never launches a second run while the previous one is
still outstanding** — the outstanding case *is* the hung-mount signal.

Critical implementation constraint: **use a `daemon` `threading.Thread`,
not `concurrent.futures.ThreadPoolExecutor`.** `concurrent.futures`
registers an `atexit` handler that joins every worker thread at
interpreter shutdown; a probe thread blocked forever in a hung-NFS
`statvfs` would then hang process exit. A daemon thread is abandoned at
exit instead, which is exactly what we want for a syscall that may
never return.

```python
class DeadlineProbe:
    def __init__(self):
        self._thread = None
        self._event = None
        self._holder = None      # [result_or_None, exc_or_None]

    def run(self, fn, timeout):
        # -> (completed: bool, result). completed False means either a
        # prior probe is still blocked, or this one did not return in
        # `timeout` s. The thread is left running (daemon) in both cases.
        if self._thread is not None and self._thread.is_alive():
            return (False, None)
        self._event = threading.Event()
        self._holder = [None, None]
        holder, event = self._holder, self._event
        def worker():
            try:
                holder[0] = fn()
            except BaseException as e:   # backstop; fn is expected to
                holder[1] = e            # return a result, not raise
            finally:
                event.set()
        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        if event.wait(timeout):
            if holder[1] is not None:
                raise holder[1]
            return (True, holder[0])
        return (False, None)
```

Each `PathCheck` owns one `DeadlineProbe`, so a hung path leaks at most
one daemon thread until the mount recovers (then the next `run` starts
fresh). No shared executor to size.

### The path check

```python
class PathCheck(HealthCheck):
    def __init__(self, path, *, write_interval=300, timeout=30):
        self._path = os.path.abspath(path)
        self._write_interval = write_interval
        self._timeout = timeout
        self._probe = DeadlineProbe()
        self._last_write = 0.0

    @property
    def identity(self):
        return self._path            # two checks on the same path dedup

    def check(self):
        now = time.time()
        do_write = (now - self._last_write) >= self._write_interval
        completed, result = self._probe.run(
            lambda: self._probe_once(do_write), self._timeout)
        if not completed:
            return HealthResult(
                self._path, HealthStatus.TIMEOUT,
                f'probe did not return within {self._timeout}s '
                '(store hung or previous probe still outstanding)')
        if result.status == HealthStatus.OK and do_write:
            self._last_write = now   # only advance on a successful write
        return result

    def _probe_once(self, do_write):     # runs in the daemon thread
        try:
            st = os.statvfs(self._path)
        except OSError as e:
            return HealthResult(self._path, HealthStatus.MISSING, str(e))
        if st.f_flag & os.ST_RDONLY:
            return HealthResult(
                self._path, HealthStatus.READONLY,
                'filesystem is mounted read-only')
        if not do_write:
            return HealthResult(self._path, HealthStatus.OK)
        hb = os.path.join(self._path, '_heartbeat')
        try:
            fd = os.open(hb, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, f'{time.time()}\n'.encode('utf-8'))
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            return HealthResult(self._path, HealthStatus.UNWRITABLE, str(e))
        return HealthResult(self._path, HealthStatus.OK)
```

Notes for the implementer:

- `write_interval` and `timeout` are **constructor params**, not config
  reads — phase 1 has no config dependency. Defaults (300 / 30) are the
  master plan's D4 value and a proposed D5/Q4 timeout; the 30 s is
  provisional and gets its real value when phase 2 wires it to a config
  knob.
- The heartbeat write **doubles as the writability probe** — a real
  `write`+`fsync` that fails on a read-only or dead FS, unlike
  `os.makedirs(exist_ok=True)` on an existing directory, which proves
  nothing (master plan D4).
- `_last_write` advances only on a *successful* write, so a store that
  fails the write keeps being write-probed every cycle rather than
  waiting out the interval.
- The cheap `statvfs` runs on **every** call (inside the probe), so a
  full store failure (`OSError`) or a read-only remount is caught
  immediately, not only on a write cycle.

## Step-level guidance

Two sequential steps; 1b builds on 1a. Review and commit each before
the next. Isolation `none` throughout (the dependency chain and the
per-step review contain the risk). One commit per step.

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 1a — result types, `HealthCheck` ABC, `DeadlineProbe` | high | opus | none | Create `shakenfist/resource_health.py` with `HealthStatus` (string constants), `HealthResult` (`@dataclass(frozen=True)`: `identity`, `status`, `detail=None`, `healthy` property), the `HealthCheck` ABC (`identity` property + `check()` abstract), and `DeadlineProbe` exactly as specified above — **daemon `threading.Thread`, not `ThreadPoolExecutor`** (avoid the atexit-join hang; state the reason in a comment). Module header follows the `shakenfist_utilities.logs` convention (`util/libvirt.py:1-9`). Unit tests in `shakenfist/tests/test_resource_health.py` (`base.ShakenFistTestCase`): (a) a fn that returns promptly → `(True, value)`; (b) a fn that blocks on a `threading.Event` past a short timeout → `(False, None)`, and a second `run()` while it is still blocked also returns `(False, None)` **without** starting a second thread (assert the same thread object / that the blocked fn ran once); (c) after the event is released the thread ends and the next `run()` starts fresh and returns `(True, value)`; (d) a fn that raises → the exception propagates from `run()`; (e) `HealthResult.healthy` is True only for `status == OK`. Commit subject: `resource_health: health check abstraction and deadline probe.` |
| 1b — `PathCheck` | high | opus | none | Add `PathCheck(HealthCheck)` to `shakenfist/resource_health.py` exactly as specified (statvfs cheap tier → `MISSING`/`READONLY`, heartbeat write+fsync → `UNWRITABLE`, deadline via the check's own `DeadlineProbe` → `TIMEOUT`, `identity` = abspath, `_last_write` advances only on a successful write). Unit tests (real `tempfile.TemporaryDirectory` where possible, `mock` where a failure must be forced): healthy dir → `OK` and the `_heartbeat` file exists with a numeric timestamp; `os.statvfs` raising `OSError` → `MISSING` with the errno detail; a `statvfs` result whose `f_flag` has `ST_RDONLY` set → `READONLY`; `os.open`/`os.write` raising `OSError` → `UNWRITABLE`; **interval gating** — with `write_interval` large, two `check()` calls (advance `time.time` via mock only a little between them) write the heartbeat **once** (second call is a read-only cycle: assert one `os.open`, `_last_write` unchanged); **hang** — patch the probe body to block on an `Event` and assert `check()` returns `TIMEOUT` within ~`timeout` and a second `check()` also returns `TIMEOUT` without launching a new probe; **dedup** — two `PathCheck`s on the same path have equal `identity`. Commit subject: `resource_health: path health check.` |

## Step ordering and dependencies

- **1a first** — it defines `HealthResult`/`HealthCheck`/`DeadlineProbe`
  that 1b imports and uses.
- **1b** depends only on 1a.
- No deploy, config, daemon, or proto changes in this phase.

## Success criteria

- `shakenfist/resource_health.py` exists with `HealthStatus`,
  `HealthResult`, `HealthCheck`, `DeadlineProbe`, and `PathCheck`, and
  nothing outside the module imports it yet (phase 2 does the wiring).
- `PathCheck.check()` returns, for a given path: `OK` when healthy
  (writing a `_heartbeat` timestamp no more than once per
  `write_interval`), `MISSING` on `statvfs` `OSError`, `READONLY` on an
  `ST_RDONLY` mount, `UNWRITABLE` on a failed write, and `TIMEOUT` when
  a probe does not return within `timeout` — the last **without** the
  call blocking longer than `timeout` and **without** launching a
  second probe while one is outstanding.
- No `concurrent.futures` executor is used (daemon-thread rationale
  documented in-code), so a permanently-hung probe cannot block
  interpreter shutdown.
- Two `PathCheck`s on the same path share an `identity` (so phase 2 can
  de-duplicate).
- `pre-commit run --all-files` passes (flake8, unit tests, mypy — the
  module is fully type-hinted).

## Back brief

Before implementing, confirm the understanding that phase 1 is the
**standalone primitive only**: no node-state changes, no daemon
integration, no config. Confirm the two non-obvious constraints — the
daemon-thread choice over `ThreadPoolExecutor` (atexit-join hang), and
that the write heartbeat is the writability probe (not `makedirs`).

## Review checklist for the management session

- [ ] `resource_health.py` created; no other module imports it yet.
- [ ] `DeadlineProbe` uses a `daemon` `threading.Thread`; a hung probe
      is proven not to block a second `run()` and (by construction) not
      to block interpreter exit.
- [ ] The cheap `statvfs` runs every call; the write runs at most once
      per `write_interval`, and `_last_write` advances only on success.
- [ ] `TIMEOUT` is returned without the call blocking past `timeout`.
- [ ] Tests force each of `OK`/`MISSING`/`READONLY`/`UNWRITABLE`/
      `TIMEOUT` and the interval-gating and dedup behaviours.
- [ ] `pre-commit run --all-files` passes; the module is type-hinted
      (mypy is in the hook set).
- [ ] Commit messages follow project conventions (Co-Authored-By with
      model, context window, effort level).
