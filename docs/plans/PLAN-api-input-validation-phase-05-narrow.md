# Phase 5: Narrow the handlers

Phase 5 of [`PLAN-api-input-validation.md`](PLAN-api-input-validation.md),
following [phase 4](PLAN-api-input-validation-phase-04-enforce.md).

Phase 4 made the validation layer reject. This phase removes the thing
it was built to replace: the `except TypeError` arm in
`handle_authorization_exceptions`, which has been handing callers the
interpreter's own words as a 400 since 2021 (`ffecee9f9`, *Refactor API
to be more tractable*). That catch is the second
half of the mechanism issue
[#3612](https://github.com/shakenfist/shakenfist/issues/3612)
describes, and it could not be removed earlier because it was absorbing
the malformed input phase 4 now refuses.

**Planning effort:** high. The change is four lines of deletion, and
every difficulty in it is in establishing what those four lines are
still load-bearing for. The survey found one deliberate `raise
TypeError` that depends on them and one documented rollback path whose
behaviour they define.

**Review effort:** high, for the same reason. A reviewer's job here is
to find the TypeError source the survey missed.

## Context

The catch lives at `shakenfist/external_api/base.py:1509`:

```python
except TypeError as e:
    _authorization_failure_log(e).info('API request rejected as malformed')
    return sf_api.error(400, str(e), suppress_traceback=False)
```

It sits inside `handle_authorization_exceptions`, alongside three
further `except` arms covering ten JWT exception classes. That is a category error: a `TypeError` is not an
authorization condition, and nothing in `flask_jwt_extended` or `PyJWT`
signals one with it. The arm is there because a handler called with a
body key it does not accept raises `TypeError`, and answering 400 was
better than answering 500 — a workaround for the absence of the
validation layer this plan spent four phases building.

With `API_VALIDATION_MODE` defaulting to `enforce`, decision D14
answers an undeclared body key with `<name>: not declared by this
endpoint` *before* the handler is reached. The arm's original purpose
is therefore already served on the default path, and what remains
behind it is the thing it was never meant to catch: a genuine
`TypeError` from inside a handler, currently reported to the caller as
a 400 with a Python message and to the operator as an INFO line saying
the *request* was malformed.

## Scope

**In:**

* Delete the `except TypeError` arm from
  `handle_authorization_exceptions`, so a handler-internal `TypeError`
  becomes a recorded 500 like every other unexpected exception.
* Replace `log_request`'s deliberate `raise TypeError` with an explicit
  400 return first, in its own commit, because the deletion depends on
  it.
* Fix [#3523](https://github.com/shakenfist/shakenfist/issues/3523),
  whose raising frame this survey identified (see F6).
* Update the two unit tests which currently assert interpreter text as
  the expected response under `warn` and `off`.
* Remove the two dead `requires_namespace_exist_if_specified` uses
  phase 4 recorded and did not file (F8).
* Document what the `warn`/`off` rollback now does, since this phase
  changes it.

**Out:**

* [#2094](https://github.com/shakenfist/shakenfist/issues/2094) — the
  bare 403 on deleting an already-unrouted address. It is response
  *semantics* for one endpoint, not the error contract of the chain;
  phase 6 owns it. See D27.
* Enforcing `required`, and semantic validation of the prose formats.
  Phase 6.
* The proxy path answering an unreachable node with a 500 (F9). Real,
  and a status-code contract change of its own. Filed, not fixed.
* Response validation. Ruled out in phase 0 (D7).

## What the survey found

The master plan's phase 5 section is stale in one important way and
correct in the rest. Corrections have been made at source in
`PLAN-api-input-validation.md` and `docs/plans/index.md` as part of the
planning commit, so this section is the record of *why* they changed
rather than a second copy of the claims.

### F1. Three of the four attribution issues are now closed, not two

The master plan says "#3615 landed 2026-08-10, #3606 is in flight as
PR #3714, leaving #3523 and #3371". As of this survey:

| Issue | State | Note |
|---|---|---|
| [#3615](https://github.com/shakenfist/shakenfist/issues/3615) | Closed | As recorded |
| [#3606](https://github.com/shakenfist/shakenfist/issues/3606) | Closed | PR #3714 merged 2026-08-12 |
| [#3371](https://github.com/shakenfist/shakenfist/issues/3371) | Closed | Closed since the master plan was written |
| [#3523](https://github.com/shakenfist/shakenfist/issues/3523) | Open | The only one left |

So the master plan's prediction — that by the time phases 3 and 4
landed, phase 5 would be "likely to be the single item that actually
depends on them" — is now the fact rather than the forecast. Phase 5
is the `except TypeError` narrowing, plus one issue.

### F2. The catch is still broad, and phases 3 and 4 did not touch it

`base.py:1509`, unchanged by phases 3 and 4. Phase 3's plan said
explicitly that it does not narrow it and that doing so before
enforcement "would turn 400s into 500s". Enforcement has landed, so
that gate is open.

### F3. One deliberate `raise TypeError` depends on the catch

`log_request` raises it on purpose (`base.py:1378`):

```python
if not isinstance(j, dict):
    raise TypeError('the request body must be a JSON object')
```

with a comment saying the per-key merge it replaced raised `TypeError`
for a non-object body and that this guard "keeps it that way". The
400 it produces comes entirely from the arm being deleted. It is
pinned by `test_a_non_object_body_is_still_a_400`
(`shakenfist/tests/external_api/test_request_validation.py:432`),
which drives four payloads — `['a', 'b']`, `'abc'`, `['ab', 'cd']` and
`5` — through the real stack.

Deleting the arm without changing this first turns every non-object
JSON body into a 500. This is why step 1 exists and why it is a
separate commit.

`grep -rn "raise TypeError" shakenfist/ --include=*.py` outside tests
returns exactly this one site; the two other matches are comments in
`validation.py`.

### F4. The rollback modes are what the deletion actually changes

In `enforce` — the default since phase 4 — an undeclared body key
never reaches the handler, so the arm is unreachable for that input.
In `warn` and `off` it is reachable, and two unit tests assert the
interpreter text as the expected response:

* `test_warn_mode_changes_no_response`
  (`test_request_validation.py:98`) asserts `unexpected keyword
  argument` appears in the response body.
* `test_off_mode_disables_the_layer` (`:198`) asserts the same.

Both are correct today and both describe a leak. After this phase
those inputs answer 500. That is the substantive behaviour change of
the phase, and it lands on the path an operator uses when they have
rolled enforcement back — see D25, which is the decision most likely
to be argued with.

### F5. No handler can raise `TypeError` from a *missing* parameter

Worth checking because D17 deliberately leaves `missing-required`
unenforced, which looks like it should leave a live path from an
omitted parameter to `TypeError: missing 1 required positional
argument` to a 400 carrying interpreter text.

It does not. Every endpoint handler in the tree gives every non-path
parameter a default. Verified with the repository's own AST
machinery rather than by reading:

```python
# tools/... (written into step 2's brief; run from the repo root)
from shakenfist.external_api import declarations as d

for path, tree, cls, fn in d.handlers():
    allpos = list(fn.args.posonlyargs) + list(fn.args.args)
    nd = len(fn.args.defaults)
    nodefault = [a.arg for a in (allpos[:len(allpos) - nd] if nd else allpos)]
    nodefault += [a.arg for a, dv in zip(fn.args.kwonlyargs, fn.args.kw_defaults)
                  if dv is None]
    ...
```

The count is **zero**. Recorded with a caution: the first cut of that
script sliced the defaults against a list it had already filtered
`self` and the `*_from_db` injected parameters out of, and reported
**22** handlers. The number was wrong in the direction that would have
made this phase look gated on phase 6. It was caught by driving one of
the 22 through the real stack — `POST /auth` without `namespace`
answers `missing namespace in request` from the handler's own guard,
not a `TypeError` — which is the check that should have been run
first. A derived number that nobody drove through the stack is exactly
the sort of assertion this plan's own review rounds keep catching.

### F6. #3523's raising frame, found

The issue asks for "a live Loki drill-down for the untruncated
traceback to identify the raising frame (single event, so it may take
a recurrence)". It has recurred, and the drill-down is done. Thirty
days of `{job="shakenfist"} |= "Server error"` on the `sfcbr` tenant:

```
KeyError: 'arch_string_raw'
  File ".../shakenfist/external_api/base.py", line 933, in proxy_request_to_node
  File ".../shakenfist/util/general.py", line 93, in get_user_agent
```

on `GET /instances/<uuid>/consoledata`, five occurrences in thirty
days. The code is `shakenfist/util/general.py:87-96`:

```python
def get_user_agent() -> str:
    architecture = cpuinfo.get_cpu_info()
    return ('Mozilla/5.0 (%(distribution)s; %(vendor)s %(architecture)s) '
            ...
                'architecture': architecture['arch_string_raw'],
                'vendor': architecture['vendor_id_raw'],
```

`py-cpuinfo` is not guaranteed to populate either key — it probes
through several mechanisms and returns what it managed to collect —
so when the probe comes back partial the API answers an opaque 500 to
a caller asking for console data. The fix is two guarded lookups in
`util/general.py`, and it is not in the API at all.

### F7. #3523's logging half is already fixed

The issue's second complaint — a traceback showing only wrapper frames,
plus a content-free `Recorded new exception` WARNING 195µs away — was
resolved by the #3433 and #3590 work now visible in
`record_exception` (`already_logged=True`, correlation fields stashed
on `flask.g`) and `suppress_exceptions_to_client` (one line carrying
`exception_class`, the full `traceback`, `method` and `path`). The
Loki records in F6 are the proof: the traceback reaches `get_user_agent`,
which is what the issue said it could not do. So #3523 reduces to F6.

### F8. A defect phase 4 recorded and never filed

Phase 4's plan records, under *Found while implementing, recorded
rather than fixed*, that `requires_namespace_exist_if_specified` is
dead on `InstanceEndpoint.delete` and `NetworkEndpoint.delete` because
the ref decorator above it has already popped `namespace`. Verified:
`instance.py:266-270` applies `@arg_is_instance_ref` above
`@api_base.requires_namespace_exist_if_specified`, so the ref
decorator wraps it and runs first, and `arg_is_instance_ref:1052`
opens with `kwargs.pop('namespace', None)`.

**No issue was filed.** `gh issue list --state all --search
requires_namespace_exist_if_specified` returns nothing, so the record
exists only in a phase plan nobody has reason to read again. That is
the silent-disappearance failure mode, caught here rather than lost.

The observable effect is small, which is why removal rather than
reordering is the right fix: `arg_is_instance_ref` already resolves
the namespace itself and answers its own errors, so a caller naming a
non-existent namespace gets `instance not found` (404) instead of
`namespace not found` (404). Reordering would change that message;
removing the dead call acknowledges the ref decorator already covers
the case. See D29.

### F9. Two more 500 classes on the proxy path, recorded not fixed

The full 30-day sample is 266 `Server error` records, and the five
`KeyError`s of F6 are the small half of it:

| n | class | deepest frame |
|---|---|---|
| 260 | `ConnectionError` | `requests/adapters.py` in `send` |
| 5 | `KeyError` | `util/general.py` in `get_user_agent` |
| 1 | `ConnectionRefusedError` | `util/concurrency.py:329` in `_node_lock_request` |

A node being unreachable is answered as a 500 with an exception repr,
260 times in a month. That is a genuine error-contract defect of the
same family as this plan's, and it is a different mechanism with a
different right answer (502 or 503, and a decision about retries). To
be filed rather than absorbed — see D28.

### F10. The arm has fired once in the only window that can be counted

`handle_authorization_exceptions` logged **nothing** on the `TypeError`
path until 2026-09-05, when `804b165d8` added
`_authorization_failure_log` and the line `API request rejected as
malformed`. Before that the arm returned a silent 400: the leak this
phase closes has had no operator-side record for its whole life, which
is its own small argument for closing it.

Since that line exists, sfcbr has fired the arm exactly **once**, on
2026-09-08:

```
"error-class": "TypeError",
"error": "InstancesEndpoint.get() got an unexpected keyword argument 'banana'",
"method": "GET", "path": "/instances"
```

which is phase 4's own hand probe — the control its measurement log
records, not a caller. Five days is a short window and it is the only
one there is; the honest statement is that nothing is known about the
arm's rate before 2026-09-05, and that in the days since, no real
caller has reached it.

### Nothing else in the master plan's phase 5 section was wrong

The description of what the narrowing *is*, and the reasoning about
why nobody picks it up incidentally, both hold.

## Decisions

### D23. The arm is deleted, not narrowed to a subset

"Narrow `except TypeError` to JWT errors" reads two ways. It means
delete the `TypeError` arm and leave the three JWT arms, not keep a
`TypeError` arm that tries to distinguish JWT-originated ones: nothing
in `flask_jwt_extended` or `PyJWT` raises `TypeError` to signal an
authorization failure, and there is no attribute on a `TypeError` that
would let the handler tell where it came from. A conditional there
could only work by matching the message text, which is the technique
this plan exists to remove.

### D24. `log_request` stops raising, in its own commit

The non-object body guard becomes an explicit `return sf_api.error(400,
'the request body must be a JSON object')`. Two reasons it is a
separate commit ahead of the deletion: the deletion is otherwise a
behaviour change bundled with a regression fix and cannot be bisected
apart, and `test_a_non_object_body_is_still_a_400` should pass
unchanged across both commits, which is only meaningful if they are
separate.

`log_request` is a decorator wrapper and already returns responses on
other paths, so this is a return, not a new mechanism.

### D25. Under `warn` and `off`, an undeclared body key becomes a 500

**This is the decision most likely to be argued with.** The
documented rollback for enforcement is `API_VALIDATION_MODE=warn` or
`off`. Today, rolling back restores the old 400-with-interpreter-text
answer for an undeclared body key. After this phase, rolling back
gives a 500 with a recorded server exception instead.

The argument against: a rollback should restore the previous
behaviour, and this makes the rollback strictly worse for the caller
who triggered it.

The argument for, which wins:

* The 400 it restores is the defect. `str(TypeError)` in a caller's
  error field is #3612, and preserving it in a mode reachable by an
  operator flag preserves the leak that four phases were spent
  closing. An operator rolling back is buying "requests that were
  working keep working", not "malformed requests get a tidier error".
* A request whose kwargs do not match its handler's signature *is* a
  server-side surprise once a validation layer exists and is switched
  off. 500 with a recorded exception is the honest answer; the
  operator gets a file in `/srv/shakenfist/exceptions/` and a log line
  carrying the class, traceback, method and path, which is more than
  the INFO line said.
* The population is small and known. Thirty days of sfcbr warn data
  (phase 4's measurement log, *What thirty days of sfcbr says about
  the flip's blast radius*) contains exactly **one**
  `unknown-parameter` finding — a hand probe on 2026-08-13 which
  already answered 400. No real caller in that window sent a body key
  its handler could not accept. The callers who would meet the new
  500 are callers who are already being refused. F10 says the same
  thing from the other side: the arm itself has fired once since it
  started logging, on a hand probe.

The alternative considered and rejected: keep a `TypeError` arm gated
on `config.API_VALIDATION_MODE != 'enforce'`. It preserves the
rollback exactly, and it makes the response to a malformed request
depend on a mode flag in two places instead of one, leaving the leak
in the tree with a longer justification attached. Recorded here so
the option is visible rather than absent.

Documented in the release note and in
`docs/developer_guide/writing_an_endpoint.md`, because a rollback
whose behaviour changed is a thing an operator finds out at the worst
possible moment otherwise.

### D26. #3523 is fixed in `util/general.py`, not in the API

Per F6 and F7 the issue's logging half is done and its remaining half
is an unguarded dict lookup in a utility function. The fix belongs
where the bug is. `get_user_agent()` gets a default for each key it
reads, and returns a user agent string naming what it does know.

It is in this phase rather than filed separately because #3523 is one
of the four issues the master plan assigns to phase 5, and because it
is a live 500 on a user-facing endpoint.

### D27. #2094 stays out

`DELETE .../route/<addr>` on an already-unrouted address returns a bare
403. Making it idempotent is a decision about what the endpoint means,
not about how the chain reports errors, and it needs the same
judgement phase 6 will be making about `required`. It is not blocked
by anything this phase does.

### D28. F9 is filed, not fixed

An unreachable node answering 500 is a real defect, and fixing it means
choosing a status code, deciding whether the proxy retries, and
auditing every caller of `proxy_request_to_node` and
`_node_lock_request`. That is a phase, not a step. File it with the
Loki evidence and link it from the master plan's Future work.

### D29. The dead decorator uses are removed, not reordered

Per F8. Removal is behaviour-neutral because the call cannot fire;
reordering would change a 404's message text on two delete routes and
would need its own justification. The four now-redundant
`namespace=None` signature parameters phase 4 also recorded are left
alone: they are populated by the body merge and read by nothing, and
removing them is a signature change to four handlers for no observable
gain. Recorded so the next phase does not rediscover them.

### D30. A test that a handler-internal `TypeError` is a 500

The deletion is a removal, and a removal is exactly the change a test
suite is least likely to notice. The phase adds a test that mounts a
handler which raises `TypeError` and asserts the caller gets 500 with
no interpreter text in the body, and that an exception was recorded.
Without it, the property "the API never answers a `TypeError` in the
interpreter's words" is asserted nowhere and could be undone by
someone restoring the arm.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | **`log_request` returns a 400 instead of raising.** In `shakenfist/external_api/base.py`, `log_request`'s wrapper raises `TypeError('the request body must be a JSON object')` at line 1378 when the parsed JSON body is not a dict. Replace it with `return sf_api.error(400, 'the request body must be a JSON object')`. Keep the surrounding comment but rewrite it: it currently explains that the raise reproduces the old per-key merge's `TypeError`, and after this change the point is that the guard answers directly rather than depending on a catch two decorators out. Do not change anything else in `log_request`. `test_a_non_object_body_is_still_a_400` (`shakenfist/tests/external_api/test_request_validation.py:432`) must pass **unchanged** — all four payloads, same status, same message. Verify by running it before and after. Commit subject: `Answer a non-object body directly.` |
| 2 | high | opus | none | **Delete the `except TypeError` arm.** In `shakenfist/external_api/base.py`, `handle_authorization_exceptions` (line 1500) catches `TypeError` and answers `400 str(e)`. Delete that arm and leave the three JWT arms — `DecodeError`, `ExpiredSignatureError` and the eight-class tuple — untouched. Rewrite the `NOTE(mikal)` comment above the wrapper so it says what the function now catches and why (authorization conditions only; see decision D23 in this plan). Then fix the fallout, which the plan has already enumerated: `test_warn_mode_changes_no_response` (`test_request_validation.py:98`) and `test_off_mode_disables_the_layer` (`:198`) both assert `unexpected keyword argument` appears in the response body; under `warn` and `off` an undeclared body key now answers 500 (decision D25), so rewrite both to assert the new behaviour *and* to assert the response carries no interpreter text. Add the D30 test: a handler which raises `TypeError` internally answers 500, the body contains no Python message, and `util.exceptions.record_exception` was called. Reuse the `INTERPRETER_TEXT` list (`test_request_validation.py:709`) rather than writing a new one; note its comment says a marker added there belongs in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_api_validation.py` too. Before finishing, enumerate every remaining source of `TypeError` reachable from a request: grep the whole tree for `raise TypeError` (step 1 removed the only one in `external_api/`), and check the `@use_kwargs`/webargs sites and `flasgger`. Report what you found even if the answer is nothing. Commit subject: `Stop answering a TypeError as a bad request.` |
| 3 | medium | sonnet | none | **Fix #3523.** `shakenfist/util/general.py:87` `get_user_agent()` indexes `cpuinfo.get_cpu_info()` for `arch_string_raw` and `vendor_id_raw` without guarding. `py-cpuinfo` returns whatever its probes collected, so both keys can be absent, and the resulting `KeyError` reaches a caller as a 500 on `GET /instances/<uuid>/consoledata` via `proxy_request_to_node`. Use `.get()` with a sensible literal for each (`'unknown'` reads correctly in a user agent string). Add a unit test in `shakenfist/tests/` that patches `cpuinfo.get_cpu_info` to return `{}` and asserts `get_user_agent()` returns a string containing the version — mutation test it by restoring one of the bare lookups and confirming the test fails. Do not change the user agent's format for the case where both keys are present; a test should pin that too. Commit subject: `Survive a partial cpuinfo probe.` |
| 4 | low | sonnet | none | **Remove two dead decorator uses.** `@api_base.requires_namespace_exist_if_specified` is applied *below* `@api_base.arg_is_instance_ref` on `InstanceEndpoint.delete` (`shakenfist/external_api/instance.py:268`) and below `@api_base.arg_is_network_ref` on `NetworkEndpoint.delete` (`shakenfist/external_api/network.py:165`). The ref decorator wraps it and opens with `kwargs.pop('namespace', None)` (`base.py:1052`), so its `kwargs.get('namespace')` is always `None` and it can never fire. Delete both applications. Do not reorder them, and do not touch the other ten uses, which are on creation and collection routes and work (decision D29). Confirm no test asserts `namespace not found` on either route before deleting. Add a short comment to `requires_namespace_exist_if_specified` in `base.py` recording that it must be applied *above* any ref decorator, so the next use does not repeat this. Commit subject: `Drop two guards that could never fire.` |
| 5 | medium | sonnet | none | **Documentation and close-out.** (a) `docs/developer_guide/writing_an_endpoint.md:308-316` already describes `API_VALIDATION_MODE` and calls `warn` the operator's rollback; extend that passage to say what `warn` and `off` now do with an undeclared body key (500, recorded exception). Do not add a new section. (b) `docs/release_notes/v07-v08.md:188-220` is phase 4's enforcement entry, including the `API_VALIDATION_MODE=warn` rollback sentence at line 210; extend that entry, do not start a second one, noting that the rollback modes no longer answer 400 for input the handler cannot accept. (c) File the F9 issue: the proxy path answers an unreachable node with a 500, with the Loki evidence from this plan's F9 and links to `proxy_request_to_node` and `util/concurrency.py:329`. (d) Update this plan's Progress section, the master plan's Execution table row for phase 5, and the `docs/plans/index.md` phase arithmetic. Commit subject: `Document what narrowing changed.` |

Steps 1 and 2 are strictly ordered. Steps 3 and 4 are independent of
both and of each other. Step 5 runs last. Step 2 carries a conditional
gate — see the Back brief.

## Risks and mitigations

* **A `TypeError` source the survey missed becomes a 500.** The
  survey enumerated `raise TypeError` across the tree and reasoned
  about the handler-signature paths, but a library on a code path with
  no test could raise one. *Mitigation:* step 2's brief makes the
  enumeration an explicit deliverable and asks for a report even when
  it is empty; the reviewer's job is named as finding the one that was
  missed. Note what the production sample can and cannot say: the
  `Server error` records in F6/F9 carry no `TypeError` *by
  construction*, because the arm catches it before the outer wrapper
  sees it, so that sample is not evidence. F10 is — the arm's own log
  line, which has fired once since it started existing on 2026-09-05,
  on a hand probe.
* **The rollback becomes worse and an operator finds out during an
  incident.** *Mitigation:* D25 is documented in both the developer
  guide and the release note by step 5, and the release note entry
  sits with the enforcement entry an operator reading about the
  rollback would already be looking at.
* **Step 4 removes a guard that is not actually dead.** *Mitigation:*
  the claim was verified by reading the decorator application order
  and `arg_is_instance_ref`'s first statement, and the brief requires
  confirming no test asserts `namespace not found` on those routes
  before deleting. If either check fails, the step stops rather than
  proceeding.
* **The phase looks trivial and is reviewed as though it were.** Four
  lines of deletion is the whole change, and the plan's value is in
  what the deletion is load-bearing for. *Mitigation:* the review
  effort is stated as high above, and D30's test makes the property
  the phase establishes assertable rather than argued.

## Definition of done

1. `handle_authorization_exceptions` contains no `except TypeError`,
   and `grep -n "except TypeError" shakenfist/external_api/base.py`
   returns nothing.
2. `grep -rn "raise TypeError" shakenfist/external_api/` returns no
   executable statement (comments in `validation.py` excepted).
3. `test_a_non_object_body_is_still_a_400` passes with its assertions
   unchanged, for all four payloads, after both step 1 and step 2.
4. A test proves a handler which raises `TypeError` internally answers
   500, that the body carries none of the shapes in `INTERPRETER_TEXT`
   (`test_request_validation.py:709`), and that an exception was
   recorded. The test has been mutation-tested by restoring the
   deleted arm, and fails when it is restored.
5. No test in the suite asserts that a response body contains
   `unexpected keyword argument` or `missing 1 required positional
   argument`.
6. `get_user_agent()` returns a string when `cpuinfo.get_cpu_info()`
   returns `{}`, pinned by a test which fails if either guarded lookup
   is restored to a bare index.
7. #3523 is closed, and the closing comment names
   `util/general.py:get_user_agent` and `KeyError: 'arch_string_raw'`
   as the raising frame the issue asked for.
8. `requires_namespace_exist_if_specified` is applied above every ref
   decorator that uses it, or not at all; the two applications named
   in F8 are gone and the other ten are untouched.
9. An issue exists for F9, linked from the master plan's Future work.
10. What `warn` and `off` do with an undeclared body key is stated the
    same way in `docs/developer_guide/writing_an_endpoint.md`, the
    v07-v08 release note, and this plan.
11. `pre-commit run --all-files` is clean.
12. The master plan's Execution table, its status line, and the
    `docs/plans/index.md` row all say phase 5 is complete and agree on
    the phase arithmetic.

## Back brief

Before executing any step, back brief the operator on the plan and how
the intended work aligns with it.

One gate: **step 2 stops and reports before committing** if its
enumeration of remaining `TypeError` sources finds anything beyond the
two the survey already knows about (`log_request`'s, removed by step
1, and handler-internal ones). A third source changes what D25 says
about the rollback, and that is a decision to take in the management
session rather than in the step.

## Progress

Planned 2026-09-10. No steps executed.
