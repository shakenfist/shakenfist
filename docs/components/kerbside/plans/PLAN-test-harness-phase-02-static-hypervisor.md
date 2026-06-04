# Phase 2: Static source driver

Part of [PLAN-test-harness.md](/components/kerbside/plans/PLAN-test-harness/). This phase
lives entirely in kerbside. The plan file lives here in
`docs/plans/` per the master plan's single-home rule.

## Goal

Add a new `static` source driver to kerbside that reads its VM
mapping from a `sources.yaml` entry — no real hypervisor required,
no control plane to talk to. The driver becomes:

- The backend kerbside fronts in the upcoming direct-qemu CI lane
  (phase 5).
- A first-class debugging tool any time you want to point kerbside
  at a hand-rolled qemu without spinning up a Shaken Fist or oVirt
  deployment first.

Out of scope for phase 2:

- The CI workflow that uses the driver (phase 5).
- The Uncalibrated Sextant qcow2 distribution (phase 5).
- TLS-backed static consoles. The driver's data model will
  accommodate `secure_port` and `host_subject`, but exercising TLS
  against a static backend is deferred — Sextant currently exposes
  SPICE over plaintext via QEMU, and a real CI run can fold TLS in
  later.
- Hot-reload of the sources.yaml mapping. Restart kerbside to
  pick up changes; cleaner watchdog story is future work.
- Schema validation of the static-driver config via pydantic.
  Manual key checks at `__init__` time are sufficient for v1;
  formal schemas can come later.

## Decisions baked into this plan

These were judgment calls made while drafting the phase plan
rather than questions to ask the operator. Flagged explicitly so
they can be challenged before code lands.

- **File location and class name**: `kerbside/sources/static.py`,
  class `StaticSource(BaseSource)`. Mirrors the existing
  `ovirt.py::oVirtSource` and `shakenfist.py::ShakenFistSource`
  naming.
- **Type discriminator**: `type: static` in `sources.yaml`.
  Dispatched from `kerbside/main.py::_parse_sources()` alongside
  the existing `shakenfist` / `ovirt` / `openstack` branches.
- **Console mapping is inline in the sources.yaml entry**, not in
  a separate file. Smallest implementation, no file plumbing to
  worry about; an external mapping file with hot-reload can be
  added later if real-world use demands it. Each entry under a
  `consoles:` key in the source dict.
- **Tickets are persisted to the Console DB** via the existing
  `db.add_console(..., ticket=...)` kwarg, which already accepts
  and stores tickets on first insert. The driver yields console
  dicts with the `ticket` field populated; no separate driver
  lookup at `.vv`-generation time is required. This is the
  smallest possible API-side change — fundamentally just one new
  `elif` branch in the proxy virtviewer handler that reads the
  ticket from the persisted Console row instead of calling a
  hypervisor-specific ticket-acquisition method.
- **Required fields per console entry**: `uuid`, `name`,
  `hypervisor`, `hypervisor_ip`, `insecure_port`, `ticket`.
  Optional: `secure_port`, `host_subject`. This matches the
  Console DB model's column set (see
  `kerbside/db.py::Console`).
- **Validation at `__init__` time**: missing required fields set
  `self.errored = True` and log a clear error. Mirrors how the
  oVirt and Shaken Fist drivers handle init-time failures.
- **No background polling, no caching layer.** `__call__` walks
  the in-memory list every time and yields. Cost is trivial; the
  list is bounded by sources.yaml size.

## Situation

The hypervisor abstraction in kerbside is intentionally thin.
`kerbside/sources/base.py::BaseSource` defines exactly three
methods, all defaulting to `...`:

```python
class BaseSource(ABC):
    def __init__(self, **kwargs):
        ...
    def __call__(self):
        ...
    def close(self):
        ...
```

The contract subclasses follow (per
`kerbside/sources/{ovirt,shakenfist}.py`):

- `__init__(**kwargs)` receives every key/value from the
  sources.yaml entry. It must set `self.errored` to `True` on
  initialisation failure.
- `__call__()` is a generator that yields console dicts. Each
  dict carries `uuid`, `source`, `hypervisor`, `hypervisor_ip`,
  `insecure_port`, `secure_port`, `name`, `host_subject`, and
  optionally `ticket`.
- `close()` cleans up; optional.

Driver dispatch happens in `kerbside/main.py::_parse_sources()`
via hard-coded `if/elif` chains on `source['type']`. To add a new
driver type we add an `elif` branch and an import.

The Console DB model
(`kerbside/db.py::Console`, line 162) already has a `ticket`
column, and `db.add_console()` (line 204) already accepts a
`ticket=None` kwarg that gets passed to the Console constructor on
first insert. On subsequent updates `add_console()` deliberately
does **not** overwrite the ticket — useful for sources whose
tickets rotate; harmless for the static driver whose tickets
don't.

`.vv` file generation happens in `kerbside/api.py` at two
endpoints: `/console/direct/<source>/<uuid>/console.vv` and
`/console/proxy/<source>/<uuid>/console.vv`. The proxy path is
the load-bearing one for kerbside-as-a-proxy and the one phase 5
will exercise. The oVirt branch in that handler instantiates an
`oVirtSource` and calls `get_console_for_vm(uuid,
acquire_ticket=True)` to pull a fresh ticket at request time. The
Shaken Fist branch reads from the Console DB directly because
Shaken Fist's ticket lifecycle is server-managed. The static
driver is closer to the Shaken Fist shape: the ticket is already
persisted by enumeration time, so the API handler just reads from
the Console DB.

`sources.yaml` is loaded via the pydantic `SOURCES_PATH` setting
(`kerbside/config.py:170`). The whole sources.yaml entry becomes
kwargs to the driver's `__init__`, with the `type` key consumed
by the dispatch and the rest forwarded verbatim.

Unit tests for sources live under
`kerbside/tests/unit/test_main.py` and mock the driver classes;
functional/integration tests under
`kerbside/tests/functional/` require live clusters and are not
the right home for a static-driver suite.

## Mission and problem statement

After phase 2:

- `kerbside/sources/static.py::StaticSource` implements
  `BaseSource` and yields persisted Console rows from an inline
  `consoles:` list in its sources.yaml entry, complete with
  pre-known tickets.
- `kerbside/main.py::_parse_sources()` dispatches `type: static`
  to it.
- `kerbside/api.py`'s proxy virtviewer handler reads the ticket
  from the Console DB row for `type: static` sources, mirroring
  the Shaken Fist branch.
- Unit tests cover construction (valid + invalid configs),
  yielding behaviour, and at least one end-to-end mock through
  `_parse_sources` that proves the dispatch wiring.
- Documentation (`AGENTS.md`, `ARCHITECTURE.md`,
  `docs/console-sources.md`) describes the new driver and shows a
  worked sources.yaml example.
- An example `etc/example-static-sources.yaml` lands in the repo
  so phase 5's CI workflow has something to start from.

## Open questions

These need answering before or during the phase, but do not block
writing this plan:

- **Should the `consoles:` list accept a single uuid → console
  dict, or always a list of dicts?** Leaning list-of-dicts — even
  for a single-VM case it's two extra characters, and it scales
  cleanly. Lock at start of step 2a unless you push back.
- **What happens if two static-source entries declare the same
  uuid?** The Console DB primary-keys on uuid, so the second
  insert wins (or, if the column is `Unique`, fails). Driver-level
  detection (warn loudly at init) is nicer than DB-level
  surprise. Decide during step 2a — the cheap thing is "log and
  use the last definition".
- **Should `StaticSource` expose `get_console_for_vm()` despite
  not strictly needing it?** Defining the method (even as a thin
  read-from-config) gives symmetry with the oVirt branch and
  leaves the door open for the API handler to query the driver
  for tickets without going through the DB. Lean: no, keep the
  surface minimal; the DB-read path is simpler. Reconsider only
  if the API handler change in step 2c gets awkward.

## Execution

Each step is one logical change → one commit on a feature branch
of this kerbside `test-harness` branch (or off `develop` if you'd
rather land phase 2 as its own PR — see Sequencing notes below).

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a. Implement `StaticSource` | medium | sonnet | none | New file `kerbside/sources/static.py`. Class `StaticSource(BaseSource)`. `__init__(**kwargs)` takes the source dict; required keys: `source`, `type`, `consoles` (list of dicts). For each entry in `consoles` validate required fields (`uuid`, `name`, `hypervisor`, `hypervisor_ip`, `insecure_port`, `ticket`); optional fields (`secure_port`, `host_subject`) default to None. On any validation failure log a clear error and set `self.errored = True`. Build `self._consoles_by_uuid: dict[str, dict]` for fast lookup; warn if duplicate UUIDs appear and use last-wins. `__call__()` yields each console dict with the `source` and `ticket` fields populated. `close()` is a no-op (inherits the `...` stub). Module-level docstring documents the sources.yaml shape with a worked example. |
| 2b. Wire driver dispatch | low | sonnet | none | In `kerbside/main.py`: add `from .sources import static as static_source` alongside the existing imports; add `elif source['type'] == 'static': lookup = static_source.StaticSource(**source)` to the `_parse_sources` dispatch chain. Verify by running the existing `tests/unit/test_main.py` suite. |
| 2c. API: proxy virtviewer handler | medium | sonnet | none | In `kerbside/api.py`, find the proxy virtviewer handler (`ConsolesProxyVirtViewer.get()`). The existing branches handle source type via direct DB reads (Shaken Fist) and per-source-driver method calls (oVirt). Add a static branch that reads the ticket from the Console DB row (`console.ticket`, populated at enumeration time by `db.add_console`) and skips the driver instantiation entirely. Mirror the Shaken Fist branch's shape. The direct virtviewer handler probably needs the same treatment; if the existing code already falls through to a sensible default for static, leave that path alone and document why in the brief. |
| 2d. Unit tests | medium | sonnet | none | New `kerbside/tests/unit/test_sources_static.py`. Cover: (1) empty `consoles:` list constructs cleanly, yields nothing. (2) Single valid console entry constructs and yields the expected dict. (3) Multi-entry yields in order. (4) Missing required field sets `self.errored = True` and logs an error (use `unittest.mock.patch` on the logger). (5) Duplicate uuids → warning + last-wins. (6) `close()` is a no-op. Also add a single mock-heavy end-to-end test in `kerbside/tests/unit/test_main.py` that exercises `_parse_sources()` with a static source entry, verifying `db.add_console` is called with `ticket=...`. Follow the project's stestr + unittest convention. |
| 2e. Documentation | low | sonnet | none | Update `AGENTS.md` (file inventory under "Key Files to Understand" — add `kerbside/sources/static.py`), `ARCHITECTURE.md` (the sources section gains a static-driver paragraph), and `docs/console-sources.md` (add a section "Static source" with a worked sources.yaml example and a paragraph noting it's intended for CI and one-off debugging, not production). |
| 2f. Example sources.yaml | low | sonnet | none | Land `etc/example-static-sources.yaml` (or `docs/examples/static-sources.yaml`, whichever fits this repo's convention better — check `etc/` for existing example configs). Show one source entry with two consoles. Document the file's purpose in a top-of-file comment block. This file gets consumed by phase 5's CI workflow. |

### Sequencing notes

- 2a is the prerequisite for everything else.
- 2b must land before any integration testing.
- 2c needs 2a + 2b. The API edits should be reviewed carefully —
  this is the spot where the static driver intersects the rest of
  kerbside.
- 2d can be drafted concurrently with 2a — the test cases pin the
  contract and might surface API mistakes before they ship.
- 2e and 2f land anytime after the code is in place.

**Branch and PR shape**: All phase 2 commits land on the
`test-harness` branch alongside the planning docs and (eventually)
later phases' implementation. One unified branch keeps the work
chronologically reviewable and avoids juggling per-phase branches.
The eventual PR off `test-harness` will be wider than a single
phase, which is by design.

## Agent guidance

This phase plan follows the conventions in `PLAN-TEMPLATE.md` at
the kerbside repo root. The execution model, effort levels,
model-choice guidance, brief-writing standards, and
management-session review checklist all apply unchanged and are
not duplicated here. Phase plans should fill in the per-step
tables described there.

Notes specific to phase 2:

- **Verify no DB migration is needed.** The Console DB row
  already has a `ticket` column; `add_console` already accepts
  the kwarg. The static driver should not require any alembic
  changes. If a sub-agent finds otherwise, stop and raise — that
  is a signal the plan is wrong, not a green light to invent
  schema changes.
- **The API handler is the integration point.** Step 2c is where
  most bugs will hide. The sub-agent should read
  `ConsolesProxyVirtViewer.get()` end-to-end before changing it,
  and write at least one mocking test (in step 2d) that exercises
  the new branch. Asserting on the rendered virt-viewer file
  content (the password / ticket field) is the cheap, high-signal
  test.
- **Cross-source coexistence.** The CI in phase 5 will run with
  *only* a static source. But production deployments may have
  static + oVirt + Shaken Fist all active. Step 2d's tests should
  confirm a static source doesn't break the dispatch for other
  source types running in the same daemon.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the step and how the work
you intend to do aligns with that step's brief.

## Administration and logistics

### Success criteria

Phase 2 is done when:

- `tox -eflake8` and `tox -epy3` are clean on the
  `test-harness` branch with phase 2's commits on top.
- The new `StaticSource` unit tests pass; existing unit tests
  still pass; coverage from `tox -ecover` does not regress in
  touched modules.
- A manual smoke test confirms the end-to-end flow: a sources.yaml
  with one static source pointing at a hand-launched qemu, a
  kerbside daemon, a SPICE client (Ryll or virt-viewer) fetching
  the `.vv` and connecting. The smoke does not need to be
  automated in this phase — phase 5 handles that.
- `README.md`, `AGENTS.md`, `ARCHITECTURE.md`, and
  `docs/console-sources.md` describe the new driver. The example
  sources.yaml ships under `etc/` (or wherever the repo's
  convention puts examples).
- The `test-harness` branch is pushed with phase 2's commits
  visible on origin. A PR will be opened later, scoped to
  whatever set of phases the operator wants to land together;
  the management session does not open the PR.

### Future work

Items deliberately deferred from phase 2:

- **External static-consoles file with hot-reload.** Useful for
  long-running debug daemons where the operator wants to swap
  qemu backends without restarting kerbside. Out of scope for
  v1.
- **Pydantic schema for the static config.** A proper pydantic
  model in `kerbside/config.py` (or alongside the driver) would
  catch typos and unknown fields at startup. Lift if/when adding
  static-driver fields starts to feel error-prone.
- **TLS-backed static consoles.** Sextant doesn't expose SPICE
  over TLS today; QEMU does support `-spice tls-port=...`. The
  driver's data model accommodates `secure_port` and
  `host_subject`, so the addition is "wire it up when needed",
  not "redesign".
- **Multi-source race detection.** If the operator declares the
  same uuid in two different static sources, the daemon currently
  picks one and moves on. A loud cross-source uniqueness check
  could live in `_parse_sources` later.

### Bugs fixed during this work

(None yet.)
