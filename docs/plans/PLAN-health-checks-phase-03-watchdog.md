# Phase 3: WATCHDOG liveness wiring (worker + elected daemons)

## Context

This is phase 3 of [`PLAN-health-checks.md`](PLAN-health-checks.md).
It gives the non-sf-api daemons a **liveness** signal — systemd's
`WATCHDOG` — so a daemon whose main loop wedges (deadlock, stuck
I/O, infinite loop) is killed and restarted instead of sitting
there alive-but-doing-nothing. This is also what **closes the
cluster-lock proof-of-life gap** (open question 11): a wedged
elected `sf-cluster` stops petting → systemd kills it → its
in-process lease refresher dies → the `cluster_locks` lease
expires → a standby steals the lock. No change to `locks.py`.

The units already run `Type=notify` and already send `READY=1`
(`send_systemd_ready`) / `STOPPING=1`; this phase adds the
periodic `WATCHDOG=1` keepalive and the `WatchdogSec=` that arms
it. The mechanism is the phase-0 D4 decision; this plan turns it
into code.

**Scope — which daemons (from the phase-0 D1 classification):**
the eight non-trivial base-class daemons get `WatchdogSec`:
`database`, `cleaner`, `cluster`, `network`, `queues`,
`resources`, `transfers`, `sidechannel`. The four others do
**not**: `sentinel-first`/`sentinel-last` are trivial ordering
units that sleep via bare `time.sleep(15)` (not `idle()`), and
`nodelock`/`privexec` are event-driven socket-accept loops (not
`idle()`); none of them go through the seam that emits
`WATCHDOG=1`, so arming `WatchdogSec` on them would make systemd
kill a perfectly healthy daemon. (sf-api is out of scope — it is
gunicorn, not a base-class daemon, and has its own worker-timeout
liveness.)

**The crux (phase-0 D4):** emitting `WATCHDOG=1` from the base
`Daemon.idle()` covers each daemon's *sleep*, but a daemon must
also pet during any *work phase* that can itself exceed
`WatchdogSec`. Two daemons have such phases — `cleaner`
(`_maintain_blobs` globbing a large blob dir) and `cluster`
(`_cluster_wide_cleanup`, per-blob/artifact/node) — and
`cluster`'s elected loop does not use `idle()` at all. Those need
explicit pets. Getting this wrong means systemd kills a busy-
but-healthy daemon, so the ordering below lands the pets
**before** arming `WatchdogSec`.

## Key references in the existing code

- `shakenfist/daemons/daemon.py`:
  - `_send_systemd_notification()` and `send_systemd_ready` /
    `send_systemd_stopping` / `send_systemd_status` (around
    `:354-373`) — the seam; add `send_systemd_watchdog()` here.
    All are gated on `NOTIFY_SOCKET`, so they no-op outside
    systemd (tests, dev).
  - `Daemon.__init__` (around `:172-190`) — initialise a
    `self._last_watchdog` timestamp here.
  - `Daemon.idle(seconds)` (around `:322-327`) — already an
    internal `for` loop ticking every 0.2s and calling
    `check_daemon_state()`; the rate-limited pet goes inside
    this tick so even a long `idle(60)` pets every ~10s.
- `shakenfist/daemons/cluster/main.py`:
  - the election-wait loop using `self.idle(5)` (`:67`) — covered
    by the base pet.
  - the **elected** loop `while self.is_elected and not
    os.path.exists(self.abort_path):` (`:440`) whose only sleep
    is `self.lock.lost_event.wait(5)` (`:468`) — **not** `idle()`,
    needs an explicit pet each iteration.
  - `_cluster_wide_cleanup` and its inner per-blob / per-artifact
    / per-node loops (heavy; a single pass on a large cluster can
    exceed `WatchdogSec`) — need pets inside the inner loops.
- `shakenfist/daemons/cleaner/main.py`: `_run_inner` (`:140`),
  `self.idle(60)` (`:188`), and `_maintain_blobs` (the
  blob-directory glob that can run long on a large node) — needs
  a pet inside its iteration.
- The other workers — `network` (`idle(5)`, `:79`), `queues`
  (`idle(0.2)`, `:141`), `resources` (`idle(1)`, `:513`),
  `transfers` (`idle(0.2)`, `:132`), `sidechannel` (`idle(1)`,
  `:994`), `database` (`idle(10)`) — all sleep via `idle()` each
  iteration with short per-iteration work, so the base pet
  suffices; audit confirms no single iteration's *work* phase
  approaches `WatchdogSec`.
- `shakenfist/deploy/ansible/files/sf.service`: shared
  `[Service]` section (`:45-68`), `Type=notify` (`:46`),
  `TimeoutStopSec=30s` (`:49`), `RestartPreventExitStatus=SIGTERM`
  (`:51`), `Restart=on-failure` / `RestartSec=5` (`:62-63`). The
  `{{ item }}` daemon name is available for a conditional
  `WatchdogSec`.
- `shakenfist/locks.py` (the refresher, ~20s `REFRESH_INTERVAL`)
  and `shakenfist/constants.py:21`
  (`CLUSTER_LOCK_LEASE_SECONDS = 60`) — the failover-time
  arithmetic for the docs.

## Inherited decisions (phase 0 D4 / OQ11)

- `WatchdogSec = 60s`; pet cadence ~10s (a comfortable 6×
  margin; well under the `WatchdogSec/2` convention).
- A watchdog miss is a SIGABRT, which is **not** `SIGTERM`, so
  `RestartPreventExitStatus=SIGTERM` does not block the restart;
  `Restart=on-failure` brings it back.
- Lock proof-of-life is closed by the kill→refresher-death→
  lease-expiry chain; **no `locks.py` change**. Worst-case
  elected-daemon failover ≈ `WatchdogSec` + lease ≈ 60 + 60 =
  **~120s**.
- The belt-and-suspenders option (refresher sheds the lease on a
  stale heartbeat without waiting for the kill) is **deferred**
  to its own future micro-plan with hysteresis tests; not in
  this phase.

## Step-level guidance

Sequential, dependent; isolation `none`; one commit each. The
pets (3a, 3b) land **before** `WatchdogSec` is armed (3c).

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a — watchdog primitive + idle() coverage | high | opus | none | In `shakenfist/daemons/daemon.py`: add `send_systemd_watchdog()` next to the other notify helpers (`:354-373`) as `_send_systemd_notification(b'WATCHDOG=1')`. Add a module constant `WATCHDOG_PET_INTERVAL = 10`. Initialise `self._last_watchdog = 0.0` in `Daemon.__init__` (`:172`). Add a `Daemon.pet_watchdog(self)` method that calls `send_systemd_watchdog()` only when `time.time() - self._last_watchdog >= WATCHDOG_PET_INTERVAL`, updating `_last_watchdog` when it does (rate-limit so a 0.2s-tick `idle` does not sendto() every tick). Call `self.pet_watchdog()` inside `Daemon.idle()`'s internal 0.2s loop (`:322-327`) so the *sleep* portion of every base-class daemon pets. Unit test: with `_send_systemd_notification` mocked and `time.time` controlled/advanced, `pet_watchdog()` emits once per ~10s window regardless of call frequency; `idle()` over a simulated long sleep pets multiple times, not every tick; outside systemd (`NOTIFY_SOCKET` unset) it no-ops. Commit subject: `daemon: emit systemd WATCHDOG keepalive from idle().` |
| 3b — pet the long work phases | high | opus | none | Add explicit `self.pet_watchdog()` calls where a single iteration's *work* (not sleep) can exceed `WatchdogSec`, so those daemons can be armed safely. (a) `cluster/main.py`: in the **elected** loop (`:440`, which sleeps on `lost_event.wait(5)` not `idle()`) pet once per iteration; and inside the per-blob / per-artifact / per-node inner loops of `_cluster_wide_cleanup` (read the function to place pets at the top of each long inner loop body). (b) `cleaner/main.py`: inside `_maintain_blobs`'s blob-directory iteration. **Audit** the other six workers (`network`, `queues`, `resources`, `transfers`, `sidechannel`, `database`): confirm each single-iteration work phase is comfortably under `WatchdogSec` (they sleep via `idle()` each loop, so the base pet covers them); if any one genuinely can approach 60s of uninterrupted work, add a top-of-`_run_inner` pet and note it. Where practical, add a focused test asserting the pet is reached on the elected/cleanup path (e.g. patch `pet_watchdog` and drive one iteration). Commit subject: `cluster, cleaner: pet watchdog during long maintenance passes.` |
| 3c — arm WatchdogSec for the eight | low | sonnet | none | In `shakenfist/deploy/ansible/files/sf.service` `[Service]` section, add a Jinja-conditional `WatchdogSec=60s` that applies to every daemon **except** the four that do not pet: `{% if item not in ["sentinel-first", "sentinel-last", "privexec", "nodelock"] %}WatchdogSec=60s{% endif %}`. Add a comment explaining that these four are excluded because they do not run the `idle()`-based loop that emits `WATCHDOG=1`, and that arming the watchdog without the keepalive would kill a healthy daemon. Confirm `Type=notify` is already set (it is) and leave `TimeoutStopSec`, `Restart=on-failure`, `RestartPreventExitStatus` unchanged. Do **not** touch `sf-api.service`. Commit subject: `deploy: arm systemd WatchdogSec for non-trivial daemons.` |
| 3d — docs + lock-failover note | medium | sonnet | none | Document the new liveness mechanism. `ARCHITECTURE.md`: a short note that the non-sf-api daemons emit a systemd `WATCHDOG` keepalive from their main loop and are restarted by systemd if it stops (with the per-daemon scope), and that for the elected `sf-cluster` this is what provides lock failover — a wedged leader is killed, its lease expires (`CLUSTER_LOCK_LEASE_SECONDS`), and a standby takes over (worst case ~120s). `AGENTS.md`: point at `pet_watchdog`/`idle()` in `daemon.py` and note any loop that does long work without `idle()` must pet. `docs/operator_guide/` (the locks page if one exists, else a brief note near the daemon/troubleshooting docs): the operator-facing version — what `WatchdogSec` does, which daemons have it, and that a crash-looping daemon in `systemctl status` may indicate a genuinely wedged loop. Mention the belt-and-suspenders lease-shedding is intentionally future work. Keep concise. Commit subject: `docs: systemd watchdog liveness and lock failover.` |

## Step ordering and dependencies

- **3a first** — defines `pet_watchdog()` and covers sleep.
- **3b** depends on 3a (calls `pet_watchdog`) and must land
  **before** 3c — arming `WatchdogSec` before `cluster`'s
  elected loop and the long maintenance passes pet would let
  systemd kill a busy leader.
- **3c** arms the watchdog only after 3a+3b guarantee every
  armed daemon pets within `WatchdogSec` in every code path.
- **3d** last (documents the assembled behaviour).

## Success criteria

- The eight non-trivial daemons emit `WATCHDOG=1` at least every
  ~10s in every code path — idle sleep, the cluster elected
  loop, and the long `cleaner`/`cluster` maintenance passes —
  and `sf.service` arms `WatchdogSec=60s` for exactly those
  eight (sentinels, `nodelock`, `privexec` excluded).
- No armed daemon can be killed by the watchdog while doing
  legitimate work: every code path that can run longer than
  ~10s reaches a pet.
- A wedged elected `sf-cluster` is killed by systemd and a
  standby acquires the cluster lock (the OQ11 failover), with
  **no change to `locks.py`**.
- The pet is rate-limited (no sendto() per 0.2s tick) and
  no-ops cleanly outside systemd.
- `pre-commit run --all-files` passes; the pet/rate-limit logic
  has unit coverage. (End-to-end watchdog-kill→restart→lease-
  steal is a systemd-level behaviour; verify it in cluster_ci /
  manually, or fold it into phase 4's rolling-upgrade test —
  unit tests cover the pet logic, not systemd's reaction.)

## Back brief

Before executing, back-brief the operator: confirm the eight-vs-
four daemon scope, the `WatchdogSec=60s` / ~10s pet values, the
ordering (pets before arming), and the decision to keep the
lock-failover purely watchdog-driven (deferring the lease-
shedding belt-and-suspenders).

## Review checklist for the management session

Standard checklist from the master plan, plus:

- [ ] `WatchdogSec` is armed **only** for daemons that provably
      pet; sentinels/`nodelock`/`privexec` are excluded.
- [ ] Every armed daemon pets within `WatchdogSec` on every
      path: idle sleep (base), cluster elected loop, and the
      `cleaner`/`cluster` long maintenance iterators.
- [ ] The pet is rate-limited and gated on `NOTIFY_SOCKET`
      (no behavioural change in tests / dev).
- [ ] `locks.py` is untouched; the lease-shedding option remains
      deferred.
- [ ] `sf-api.service` and `sf.service`'s other settings are
      unchanged apart from the conditional `WatchdogSec`.
