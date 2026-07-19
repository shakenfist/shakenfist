# Phase 1 — stop the idle-loop polls

Master plan: [PLAN-database-load-reduction.md](PLAN-database-load-reduction.md)

## Goal

Remove the two per-tick database reads that `check_daemon_state()`
issues from every daemon's idle loop. These are ~57% of the measured
sf-database load (154/s `get_node` + 149/s `get_node_daemon_state` on
the sfcbr cluster). This is the shippable-alone quick win and lands
as its own PR.

## Findings (the code as it stands)

`Daemon.idle(seconds)` (`daemons/daemon.py:379`) does not sleep for
`seconds`; it loops in 0.2s sub-ticks, and on every tick calls
`check_daemon_state()`, `pet_watchdog()`, and checks `abort_path`:

```python
def idle(self, seconds):
    for _ in range(int(seconds / 0.2)):
        time.sleep(0.2)
        self.check_daemon_state()
        self.pet_watchdog()
        if os.path.exists(self.abort_path):
            break
```

`check_daemon_state()` (`daemon.py:336`) issues two DB round trips
per call:

```python
n = Node.this_node(suppress_failure_audit=True)      # -> mariadb.get_node
if not n:
    return
daemon_state = n.get_daemon_state(self.daemon_name).value  # -> mariadb.get_node_daemon_state
if daemon_state in [DAEMON_STATE_STOPPED, DAEMON_STATE_STOPPING]:
    set_abort_path(...)
```

Four loops call `check_daemon_state()` — `idle()` (`:382`) plus the
queues (`queues/main.py:166`), cluster (`cluster/main.py:68`) and
database (`database/main.py:5181`) daemons' own loops — so a fix
placed *inside* `check_daemon_state()` covers all of them.

Two facts make this safe to cut hard:

1. **`get_node` is pure waste here.** `this_node()` is called only to
   obtain a `Node` so `get_daemon_state()` can be invoked; the only
   value used is the daemon-state string. `NodeData` is immutable
   (`{uuid, fqdn, ip, version}`) and the node UUID is already in
   `config.NODE_UUID`, resolved once at startup by
   `_resolve_node_uuid()` (`daemon.py:233`, called in `run()` before
   any idle loop). The daemon-state row can be read directly by UUID
   via `mariadb.get_node_daemon_state(node_uuid, daemon)`
   (`mariadb.py:10378`), whose result row exposes `.value` directly
   (`NodeDaemonStateData.value`, used the same way in
   `Node.get_degraded_daemons`). So the `get_node` call is removed
   outright, not cached.

2. **The daemon-state read does not need 5 Hz.** It is not the
   fast-path shutdown signal. Normal shutdown is SIGTERM →
   `exit_gracefully()` (`daemon.py:323`), which sets `abort_path`
   *locally* and is noticed on the next 0.2s tick by the
   `abort_path` check in `idle()`. That check is left untouched, so
   local shutdown latency is unchanged. `check_daemon_state()`'s DB
   read exists only to notice a state transition written by *another*
   actor into the database (an externally requested / cluster-driven
   stop). Reacting to that within a couple of seconds is ample.

`pet_watchdog()` (`daemon.py:369`) already establishes the idiom for
this: it runs on every 0.2s tick but rate-limits its actual work to
`WATCHDOG_PET_INTERVAL` (10s) via a `self._last_watchdog` timestamp.
Phase 1 applies the same pattern to the daemon-state read.

## Open question 1 — daemon-state poll interval (resolved: 2s)

**Resolved to 2 seconds.** A full inventory of daemon-state writers
and readers confirms no actor depends on sub-second reaction to a
DB-written state change:

* Graceful stop is **SIGTERM-driven**, not poll-driven. The systemd
  unit's `ExecStop` runs `sf-ctl stop`, which only writes `STOPPING`
  and returns immediately; systemd then delivers SIGTERM (default
  `KillMode=control-group`), and `exit_gracefully()` sets `abort_path`
  locally — noticed on the next 0.2s tick. That path is unchanged by
  this phase.
* The **only** actor that writes `STOPPING` and relies on the poll to
  notice is a *manual* `sf-ctl stop <daemon>` invoked outside systemd
  (no signal sent). It too returns immediately without waiting on the
  daemon, so it has no latency expectation; a manual graceful stop
  being observed within ~2s instead of ~0.2s is fine.
* There is **no cross-node or cluster-driven** `STOPPING` write. The
  cluster and cleaner daemons only *read* their own state. The single
  cross-daemon writer is the queues health monitor, which writes
  `RUNNING`/`STOPPED` *health verdicts* to the non-framework
  privexec / nodelock / gunicorn(api) rows (which do not poll their
  own state) and never writes `STOPPING` — so it triggers no
  shutdown and is irrelevant here.

2s keeps the full win (it is the `get_node` elimination plus a 10x
cut on the daemon-state read) while staying conservative on
manual-stop responsiveness; going higher (e.g. 5s) buys only ~2/s
more cluster-wide and is not worth the slower manual stop.

## Approach

Two independent, behaviour-preserving-except-for-cadence changes, both
in `daemons/daemon.py`:

**(a) Eliminate the `get_node` round trip.** Rewrite
`check_daemon_state()` to read the daemon-state row directly by node
UUID from `config.NODE_UUID`, instead of constructing a `Node` via
`this_node()`:

```python
node_uuid = config.NODE_UUID
if not node_uuid:
    return
try:
    row = mariadb.get_node_daemon_state(uuid.UUID(node_uuid), self.daemon_name)
except DatabaseUnavailable:
    return
daemon_state = row.value if row is not None else None
if daemon_state in [Node.DAEMON_STATE_STOPPED, Node.DAEMON_STATE_STOPPING]:
    set_abort_path(self.abort_path, 'from check_daemon_state')
```

This preserves every existing behaviour: `None`/missing row → no
abort (matched the old `if not n: return` and the `State(value=None)`
fallback), `DatabaseUnavailable` swallowed, abort only on
STOPPING/STOPPED. Requires adding `import uuid` to the module.

**(b) Rate-limit the DB read to `DAEMON_STATE_POLL_INTERVAL`.** Mirror
`pet_watchdog`: add a module constant `DAEMON_STATE_POLL_INTERVAL = 2`
next to `WATCHDOG_PET_INTERVAL`, a `self._last_daemon_state_check = 0.0`
field in `__init__`, and gate the read at the top of
`check_daemon_state()`:

```python
now = time.time()
if now - self._last_daemon_state_check < DAEMON_STATE_POLL_INTERVAL:
    return
self._last_daemon_state_check = now
```

The timestamp is advanced before the read (not only on success) so a
database outage is polled every 2s, not hammered every 0.2s. The
`abort_path` check in `idle()` is untouched, so local SIGTERM
shutdown is still observed within 0.2s.

Net effect: `get_node` from this path → 0; `get_node_daemon_state`
from this path → 1 per daemon per 2s (a 10x cut). Expected cluster
total ~527/s → ~250/s.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 1a | medium | (implemented in management session per user request) | none | In `daemons/daemon.py`: add `import uuid`; add `DAEMON_STATE_POLL_INTERVAL = 2` beside `WATCHDOG_PET_INTERVAL`; initialise `self._last_daemon_state_check = 0.0` in `Daemon.__init__`; rewrite `check_daemon_state()` per Approach (a)+(b) — rate-limit gate first, then read `mariadb.get_node_daemon_state(uuid.UUID(config.NODE_UUID), self.daemon_name)` directly, keeping the `DatabaseUnavailable` swallow and the STOPPING/STOPPED → `set_abort_path` behaviour. Do not touch `idle()`'s `abort_path` check. |
| 1b | medium | (management session) | none | Update `shakenfist/tests/test_database_unavailable.py::CheckDaemonStateTestCase` for the new code path (mock `daemon.mariadb.get_node_daemon_state` rather than `Node.this_node`; set `config.NODE_UUID` and `d._last_daemon_state_check = 0.0` on the `__new__`-built daemon). Add tests: `get_node` is never called; abort set on STOPPING and STOPPED; no abort on RUNNING/None; the DB read is skipped inside the interval and re-issued after it; `DatabaseUnavailable` swallowed. |

## Verification

* `pre-commit run --all-files` green (flake8, unit suite, mypy). The
  existing `test_daemon_watchdog.py` (mocks `check_daemon_state`) and
  `test_database_unavailable.py` must stay green.
* Behavioural check of the two invariants that could regress:
  local SIGTERM still aborts within 0.2s (abort_path path unchanged),
  and an externally written STOPPING is still observed (within the
  poll interval). Covered by unit tests; the full cluster-lifecycle
  CI additionally exercises daemon start/stop.
* On deploy to sfcbr: `database_get_node_total` and
  `database_get_node_daemon_state_total` rates drop sharply
  (`get_node` toward its non-idle-loop baseline ~20/s, daemon-state
  toward ~1/daemon/2s). Record the before/after here when the PR
  lands.

## Risks

* **A daemon reacting too slowly to an externally requested stop.**
  Bounded by `DAEMON_STATE_POLL_INTERVAL` (2s) and only affects the
  DB-written stop path, not local SIGTERM. See open question 1.
* **`config.NODE_UUID` unset when `check_daemon_state` runs.** Guarded
  by the early `return`; in practice `_resolve_node_uuid()` sets it in
  `run()` before any idle loop. Same effective safety as the old
  `if not n: return`.

## Success criteria

* `check_daemon_state()` issues no `get_node`, and at most one
  `get_node_daemon_state` per `DAEMON_STATE_POLL_INTERVAL` per daemon.
* Local shutdown latency unchanged; external-stop latency ≤ the poll
  interval.
* `pre-commit` and CI green; sfcbr dashboard shows the two counters
  fall as predicted.
