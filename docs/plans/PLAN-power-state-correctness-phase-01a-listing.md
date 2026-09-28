# Phase 1a -- Honest libvirt domain listing

Part of [PLAN-power-state-correctness.md](PLAN-power-state-correctness.md).
Phase 0 landed as [#4327](https://github.com/shakenfist/shakenfist/pull/4327)
(`250a40871`), so `power_state` is now asserted throughout the lifecycle
tests before anything in this phase or phase 1b touches the cleaner.

**Planning effort:** medium, as the master plan asks. This phase changes
names and test fakes, not behaviour. The judgement calls are the helpers'
shape (D1), what an honest fake says about a crashed domain (D4), and
how the tests phase 1b will need are held ready (D5).

## Scope

**In scope:**

* Two helpers on `LibvirtConnection` in `shakenfist/util/libvirt.py`,
  `get_active_sf_domains()` and `get_inactive_sf_domains()`, each one
  `listAllDomains()` call. Unit tests for both.
* Moving the four callers of `get_all_domains()` and `get_sf_domains()`
  to `get_active_sf_domains()`, which keeps their current behaviour, and
  deleting the two old helpers.
* Rewriting `test_daemon_cleaner.py`'s libvirt fake so it lists domains
  the way libvirt does, and turning the one expectation that fails as a
  result into an expected failure for phase 1b.
* Correcting the stale claims the survey found. These are fixed at their
  source in the planning commit, so later steps should not redo them.

**Out of scope:**

* Any behaviour change. In particular, the cleaner's second loop and the
  apparmor sweep keep iterating active domains, and `instances_total`
  keeps counting only active domains. Pointing the cleaner at inactive
  domains is phase 1b. That phase needs the guards listed in the master
  plan first.
* `get_active_domain_ids()`. It stays on `listDomainsID()` (D3).
* A helper listing foreign (non-`sf:`) domains for the apparmor sweep.
  Phase 1b decides what that sweep reads, because changing its input
  changes what it deletes.
* Unit tests for the resources daemon's domain counting and the
  sidechannel's monitor discovery. Neither has one today, and this phase
  changes only which method each calls (D6).

## What the survey found

The master plan's phase 1a section is right in substance. The survey
found one helper it does not mention, a fake detail it does not decide,
and an overlapping future-work item in another plan. The master plan and
`docs/plans/index.md` are corrected in this phase's planning commit.

### S1 -- the two old helpers are one filter applied twice

`get_all_domains()` (`util/libvirt.py:217-229`) iterates
`listDomainsID()`, calls `lookupByID()` for each id, and already skips
any domain whose name does not start with `sf:`. `get_sf_domains()`
(`:182-190`) wraps it and applies the same filter again. Despite their
names, both return exactly the active SF domains. This matches F1.

### S2 -- a third helper exists, and stays

`get_active_domain_ids()` (`util/libvirt.py:192-215`) was added by
`9ffb387b8` on 2026-09-13, after the master plan's audit, for the
resources daemon's 5 second poll (`resources/main.py:824`). It returns
`set(self.conn.listDomainsID())`, unfiltered, and its docstring explains
why. It is honestly named already. Its docstring refers to
`get_all_domains()`, so that reference needs updating when the old
helper goes.

### S3 -- four callers, all wanting active SF domains

| Caller | Current call | What it does with the result |
|---|---|---|
| `daemons/cleaner/scheduled_tasks.py:108` | `get_sf_domains()` | First loop: records power state and fills `seen`. |
| `daemons/cleaner/scheduled_tasks.py:222` | `get_all_domains()` | Second loop: acts on domains absent from `seen`, which is none of them in practice (F1). Its `startswith('sf:')` check at `:227` is dead, and its comment says "Inactive VMs just have a name", which is false. |
| `daemons/resources/main.py:497` | `get_all_domains()` | Sums `info()` over domains where `isActive() == 1`, and counts `instances_total` and `instances_active` from the same set, so the two are always equal. |
| `daemons/sidechannel/main.py:2011` | `get_sf_domains()` | Starts monitors for SF domains not `off`, `crashed` or `paused`. |

`grep -rn 'get_all_domains\|get_sf_domains'` over `shakenfist/` finds
nothing else. No test patches either method by name.

### S4 -- a crashed domain is never active in production

Our domain XML sets `<on_crash>restart</on_crash>`
(`deploy/collection/roles/hypervisor/files/libvirt.tmpl:40`) and
declares no panic device. qemu therefore never reports a guest crash to
libvirt, and a guest that panics simply reboots or hangs. libvirt
reports `VIR_DOMAIN_CRASHED` for an *active* domain only under
`on_crash` `preserve`, so the cleaner's first-loop `crashed` branch
(`scheduled_tasks.py:155-161`) is unreachable on a Shaken Fist
hypervisor. Libvirt's API still permits it, though. This bears on D4, and
on phase 1b's open question about mapping a `CRASHED` shutoff reason, so
the master plan's phase 1b section now says so.

### S5 -- which fake expectations an honest listing breaks

`FakeLibvirtConnection` (`tests/test_daemon_cleaner.py:48-97`) serves
seven domains from `listDomainsID()`: running, a foreign `apache2`,
shutoff, crashed, paused, PM-suspended and I/O-error paused. In libvirt
only the shutoff one is inactive. When the fake lists only active
domains:

* `test_update_power_states` loses its `('shutoff', 'off')` expectation:
  no loop sees that domain, so its instance keeps `power_state` unset.
  This expectation is phase 1b's to meet.
* `test_update_power_states_does_not_enforce_capacity` still passes,
  because the running domain is still placed.
* `test_update_power_states_pets_watchdog` still passes.
* `test_crashed_delete_wait_instance_is_marked_deleted` passes only if
  the crashed domain stays active (D4).

### S6 -- the test runner honours `unittest.expectedFailure`

The repository has no expected-failure test yet. A check against the
pinned `testtools` 2.9.1 in `.tox/py3` confirmed that
`@unittest.expectedFailure` on a `testtools.TestCase` method is reported
as an expected failure when it fails. An unexpected success fails the
run. So phase 1b cannot make the test pass and forget to remove the
decorator.

### S7 -- another plan already asked for this

`PLAN-transient-capacity-refusals.md`'s Future work has an item, "Make
`get_all_domains()` one libvirt call" (`:1199-1209`). This phase
resolves it. Its line references are stale: it cites the resources
caller at `:401`, which is now `:497`. Step 2 updates the entry to point
here.

### S8 -- mypy is strict on this file

`tox -emypy` runs `util/libvirt.py` with `--disallow-untyped-defs`
(`tox.ini:107`), so the new helpers need full annotations. They follow
the existing `Iterator[Any]` return type.

## Decisions

### D1 -- two helpers, each one `listAllDomains()` call

Add `get_active_sf_domains()` and `get_inactive_sf_domains()`, both
returning `Iterator[Any]`. They share a private
`_list_sf_domains(flags)`, which calls
`self.conn.listAllDomains(flags)` once and yields the domains whose
`name()` starts with `sf:`. The flags are
`self.libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE` and
`VIR_CONNECT_LIST_DOMAINS_INACTIVE`. Read them from the module
`get_libvirt()` returned, as `extract_power_state()` does, so the test
fakes can supply them.

The `sf` in the names stays. Every caller wants SF domains only, and a
name that hides a filter is how `get_all_domains()` misled the apparmor
sweep (F1).

`get_inactive_sf_domains()` has no caller until phase 1b. It is added
here anyway so that phase 1b is a change to callers and guards against a
helper this phase has already unit-tested, and so the pair's names make
the choice visible at every call site. This is the decision most open
to argument: it adds a method with no caller. The alternative is to
leave it to phase 1b, and the cost of that is small either way.

`listAllDomains()` returns domain objects, so the per-domain
`lookupByID()` and the race between listing and looking up both go
away. A domain that disappears between `listDomainsID()` and
`lookupByID()` used to be skipped silently. Now it is either in the
listing or not. `name()` reads a field the python binding already holds,
so it adds no round trip.

### D2 -- move every caller to the active helper, and delete the old ones

All four callers in S3 call `get_active_sf_domains()`. `get_all_domains()`
and `get_sf_domains()` are deleted, with no aliases: an alias would keep
the misleading name callable.

The second cleaner loop gets the same call, which makes its uselessness
visible. Its comment is replaced with one saying that it deliberately
still iterates active domains, so it acts only when the first loop left
`seen` incomplete, and that phase 1b points it at inactive domains (F1).
The dead `startswith('sf:')` check at `:227` stays. Phase 1b rewrites
this loop, and leaving it keeps this diff to the call and its comment.

The resources daemon keeps its `isActive()` check. It is redundant
against an active listing, but it guards a domain that stops between the
listing and `info()`, and removing it is not this phase's business.

### D3 -- `get_active_domain_ids()` is left on `listDomainsID()`

It is a change detector for a 5 second poll. It deliberately does not
filter by name, because filtering needs a per-domain call, and its
docstring gives the reasons. `listAllDomains()` would also be a single
call, but it would return objects the poll does not need, and the
function is honestly named already. Only its docstring changes: it drops
the comparison with `get_all_domains()` and describes the cost
difference against `get_active_sf_domains()` instead.

### D4 -- the honest fake lists by state, and keeps crashed active

The cleaner test's `FakeLibvirtConnection` gets
`listAllDomains(flags)` in place of `listDomainsID()` and `lookupByID()`.
It builds the same seven domains and returns those whose state matches
the flags. `VIR_DOMAIN_SHUTOFF` is inactive and every other state is
active. `FakeLibvirt` gains the two flag constants, with libvirt's values
1 and 2.

The fake **drops** `listDomainsID()` and `lookupByID()` rather than
keeping them. Code that goes back to the old path then fails with an
`AttributeError` instead of quietly passing against a fake which still
serves it.

The crashed domain stays in the active set. S4 says our hypervisors
never produce an active crashed domain. But the fake's job is to behave
like libvirt, not like our template, and libvirt does return one under
`on_crash` `preserve`. Keeping it active also keeps
`test_crashed_delete_wait_instance_is_marked_deleted` covering a branch
of production code which still exists. Whether that branch should exist
is phase 1b's CRASHED question.

### D5 -- the shutoff expectation becomes its own expected failure

`test_update_power_states` drops `('shutoff', 'off')` from its
expectation list and keeps the other four. A new test,
`test_update_power_states_detects_shutoff`, sets up the same instances,
runs `update_power_states()`, and asserts the shutoff instance's
`power_state` is `off`. It is decorated with `@unittest.expectedFailure`
and has a docstring naming F1 and phase 1b.

A separate test, not a decorator on the existing one: an expected
failure hides every other assertion in the same test. The four
expectations that do hold must keep failing loudly if they break.

By S6, phase 1b has to remove the decorator in the same change that
makes the test pass.

### D6 -- no new tests for the resources or sidechannel callers

Each caller changes one method name for another with the same contract.
That contract is unit-tested in `test_util_libvirt.py` by step 1. Testing
`_get_stats()` or the monitor discovery loop would mean mocking large
functions for a rename. The functional suite exercises both:
`instances_active` feeds the metrics the node tests read, and every
guest test that waits for agent ready needs a sidechannel monitor. The
PR's smoke job covers them, and step 3 dispatches the guest suite
because the sidechannel path matters to every guest test.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | See brief 1. Commit: "libvirt: list domains by active or inactive." |
| 2 | medium | sonnet | none | See brief 2. Commit: "Move domain listing callers to named helpers." |
| 3 | low | management session | none | Run `tox -epy3`, `tox -emypy` and `pre-commit run --all-files`. Then dispatch `functional-tests.yml` on the branch (`gh workflow run functional-tests.yml --ref power-state-correctness-phase-01a-listing`). Confirm that the `Guests (collection)` job and the pull request's smoke job pass, and that `test_state_changes.py`'s power state assertions ran. Record both run URLs in the pull request description. |

The steps run in order. Step 1 is additive and passes on its own. Step 2
changes the callers and the fake together, because the fake has to serve
`listAllDomains()` from the moment the cleaner calls it.

### Brief 1 -- the helpers and their unit tests

In `shakenfist/util/libvirt.py`, add to `LibvirtConnection`, next to
`get_active_domain_ids()`:

```python
def _list_sf_domains(self, flags: int) -> Iterator[Any]:
    """One listAllDomains() call, filtered to Shaken Fist domains."""
    for domain in self.conn.listAllDomains(flags):
        if domain.name().startswith('sf:'):
            yield domain

def get_active_sf_domains(self) -> Iterator[Any]:
    """Shaken Fist domains libvirt considers active: running, paused or
    PM suspended. A powered off domain is not in this list."""
    return self._list_sf_domains(
        self.libvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE)

def get_inactive_sf_domains(self) -> Iterator[Any]:
    """Shaken Fist domains which are defined but not running. Our
    domains are persistent, so a powered off instance is here."""
    return self._list_sf_domains(
        self.libvirt.VIR_CONNECT_LIST_DOMAINS_INACTIVE)
```

Do not touch `get_all_domains()`, `get_sf_domains()` or any caller in
this step.

In `shakenfist/tests/test_util_libvirt.py`, next to the
`get_active_domain_ids()` tests (`:180-206`), add tests which use the
same setup: `_connection()` with `lc.conn = mock.Mock()`. Add
`VIR_CONNECT_LIST_DOMAINS_ACTIVE = 1` and
`VIR_CONNECT_LIST_DOMAINS_INACTIVE = 2` to `FakeLibvirtModule` first,
since `_connection()` installs it as `lc.libvirt`. The tests are:

* Each helper calls `listAllDomains` exactly once, with the matching
  flag, and never calls `listDomainsID` or `lookupByID`.
* Each yields only the `sf:` domains from a list which mixes `sf:` and
  foreign names, in listing order.
* An empty listing yields nothing.

Before committing, mutate the code and check the tests fail. Swap the
two flags, then remove the `sf:` filter, and each mutation must fail at
least one test. Say in the step report that you did this. Run
`stestr run shakenfist.tests.test_util_libvirt`, `tox -emypy` and
`pre-commit run --all-files` with the files staged. Style: single
quotes, double-quoted docstrings, 120 columns.

### Brief 2 -- the callers, the fake, the expected failure

1. **Callers.** Replace `lc.get_sf_domains()` at
   `daemons/cleaner/scheduled_tasks.py:108` and
   `daemons/sidechannel/main.py:2011`, and `lc.get_all_domains()` at
   `daemons/cleaner/scheduled_tasks.py:222` and
   `daemons/resources/main.py:497`, with `lc.get_active_sf_domains()`.
   Change nothing else in those loops, except the cleaner's second loop
   comment. At `:219-220` it reads "Inactive VMs just have a name, and
   are powered off in our state system", which is false. Replace it with
   a comment saying the loop deliberately still iterates active domains,
   so it only acts on domains the first loop failed to add to `seen`, and
   that phase 1b of `docs/plans/PLAN-power-state-correctness.md` points
   it at `get_inactive_sf_domains()` (F1). Leave the dead
   `startswith('sf:')` check and the resources `isActive()` check alone
   (D2).
2. **Delete** `get_all_domains()` and `get_sf_domains()` from
   `util/libvirt.py`. Update `get_active_domain_ids()`'s docstring so it
   compares itself to `get_active_sf_domains()`: that is one
   `listAllDomains()` call returning domain objects to filter by name,
   and this method needs neither the objects nor the filter (D3).
   `grep -rn 'get_all_domains\|get_sf_domains' shakenfist tools` must
   then find nothing.
3. **The fake** (`tests/test_daemon_cleaner.py:25-97`). Add
   `VIR_CONNECT_LIST_DOMAINS_ACTIVE = 1` and
   `VIR_CONNECT_LIST_DOMAINS_INACTIVE = 2` to `FakeLibvirt`. In
   `FakeLibvirtConnection`, replace `listDomainsID()` and `lookupByID()`
   with `listAllDomains(self, flags)`. It builds the same domains from
   the same map, keeping `id7` conditional on `'ioerror'` as today, and
   returns those whose state matches the flags: `VIR_DOMAIN_SHUTOFF` is
   inactive and every other state is active. Keep `lookupByName()`. Add
   a comment saying the fake mirrors libvirt's listing, not our domain
   template, which is why the crashed domain is active (D4, S4). Do not
   keep `listDomainsID()` or `lookupByID()`.
4. **The expected failure.** In `test_update_power_states`, remove
   `('shutoff', 'off')` from the expectations. Add
   `test_update_power_states_detects_shutoff` with the same decorators
   and setup, asserting that the shutoff instance's
   `power_state['power_state']` is `'off'`. Decorate it with
   `@unittest.expectedFailure`, and give it a docstring saying that the
   cleaner cannot see inactive domains (F1) and that phase 1b removes
   the decorator in the change which makes this pass. Add
   `import unittest` in the stdlib import group.
5. **The other plan.** In `docs/plans/PLAN-transient-capacity-refusals.md`,
   rewrite the Future work entry "Make `get_all_domains()` one libvirt
   call" (`:1199-1209`) to say that phase 1a of
   `PLAN-power-state-correctness.md` resolved it: `get_all_domains()` is
   gone, and its callers use the single-call `get_active_sf_domains()`.
   Keep the entry rather than deleting it, so the link from that plan's
   phase 3 D22 still leads somewhere.

Run `stestr run shakenfist.tests.test_daemon_cleaner` and confirm the
output reports one expected failure and no other failure. Then run the
full `tox -epy3`, `tox -emypy` and `pre-commit run --all-files` with the
files staged.

## Risks and mitigations

* **`listAllDomains()` behaves differently from the pair it replaces on
  a real hypervisor.** The unit tests use fakes, and the fakes are what
  this plan is correcting. Mitigation: step 3's guest suite run. Every
  guest test depends on the sidechannel finding its domain through the
  new call, and phase 0's `power_state` assertions depend on the cleaner
  not writing a wrong value. The management session reads both jobs,
  not just the overall result.
* **A caller silently widens to inactive domains.** This is only
  possible if someone picks the wrong helper. Mitigation: brief 2 names
  the one helper every caller uses, and the Definition of done greps for
  `get_inactive_sf_domains` outside `util/libvirt.py` and its test.
* **The expected failure outlives phase 1b.** Mitigation: by S6, an
  unexpected success fails the run, so phase 1b cannot pass its unit
  tests without removing the decorator.
* **Worktree pre-commit stash races.** Steps run one after another, in
  one worktree, and never in parallel.

## Definition of done

* [ ] `grep -rn 'get_all_domains\|get_sf_domains' shakenfist tools`
      prints nothing.
* [ ] `grep -rn 'get_inactive_sf_domains' shakenfist` prints only
      `util/libvirt.py` and `tests/test_util_libvirt.py`.
* [ ] `grep -n 'listDomainsID\|lookupByID' shakenfist/tests/test_daemon_cleaner.py`
      prints nothing.
* [ ] `stestr run shakenfist.tests.test_daemon_cleaner` reports exactly
      one expected failure, `test_update_power_states_detects_shutoff`,
      and no failure.
* [ ] `stestr run shakenfist.tests.test_util_libvirt` passes, and the
      step 1 report says that both mutations in brief 1 failed a test.
* [ ] `tox -emypy` and `pre-commit run --all-files` pass.
* [ ] A `workflow_dispatch` run of `functional-tests.yml` on the branch
      has a passing `Guests (collection)` job, and the pull request's
      smoke job passes. Both run URLs are in the pull request
      description.
* [ ] No behaviour change outside the test fake.
      `git diff --stat develop... -- shakenfist/daemons` lists only
      `cleaner/scheduled_tasks.py`, `resources/main.py` and
      `sidechannel/main.py`, and every changed line in
      `git diff develop... -- shakenfist/daemons` is one of the four
      calls or part of the second loop's comment.

## Back brief

Before starting step 1, back brief Mikal on what will change: two new
helpers, four callers moved, two helpers deleted, the cleaner fake made
honest, and one expected failure left for phase 1b. State D1's choice to
add `get_inactive_sf_domains()` before it has a caller, and D4's choice
to keep the crashed domain active in the fake. Either is cheap to change
now, and changing it after step 2 means rewriting tests.
