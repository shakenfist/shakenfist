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
* Stop `suppress_exceptions_to_client` putting `repr(e)` in the
  caller's error body. Added to the phase after step 2 found it — see
  F11 and D31.

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

The count is **zero** — and it answers a narrower question than the
one that mattered, which the review of #4162 later caught. Read the
correction at the end of this finding before relying on it.

Recorded with a caution: the first cut of that
script sliced the defaults against a list it had already filtered
`self` and the `*_from_db` injected parameters out of, and reported
**22** handlers. The number was wrong in the direction that would have
made this phase look gated on phase 6. It was caught by driving one of
the 22 through the real stack — `POST /auth` without `namespace`
answers `missing namespace in request` from the handler's own guard,
not a `TypeError` — which is the check that should have been run
first. A derived number that nobody drove through the stack is exactly
the sort of assertion this plan's own review rounds keep catching.

**The corrected number was still answering the wrong question.** The
review of [#4162](https://github.com/shakenfist/shakenfist/pull/4162)
found that the hazard is not a parameter *without* a default. It is a
parameter *with* a `None` default which a handler then dereferences
without a type guard. Every declared parameter compiles to a field
with `allow_none=True`, and D17 leaves `required` unenforced, so
`None` reaches handlers routinely on the **default** `enforce` path —
which an AST check for missing defaults cannot see at all.

`InstancesEndpoint.post` is the proven instance. It defaults
`name=None`; `validators.hostname(None, ...)` returns a falsy
`ValidationError` rather than raising, so execution reaches
`contains_domain = '.' in name`, which raises `TypeError: argument of
type 'NoneType' is not iterable`. Before this phase that answered 400
with interpreter text. After step 2 it answers a recorded 500, on the
default path, for `POST /instances {}`. Fixed here, with tests.

So F5's zero covers non-defaulted parameters only, and D25's
blast-radius argument covers undeclared body keys under `warn`/`off`
only. Neither covered a plain caller mistake against a declared but
nullable parameter. `POST /networks` next door escapes only by
accident, because `ipaddress.ip_network(None)` raises `ValueError`
rather than `TypeError`.

The class as a whole is unswept, and sweeping it belongs to phase 6,
which already owns `required` and semantic validation. Filed as
[#4167](https://github.com/shakenfist/shakenfist/issues/4167), which
also carries three further latent 500s the fix's author found in the
same handler while probing its other fifteen parameters — `cpus` and
`memory` as null, `disk[].base` as a non-string, and
`network[].network_uuid` as a non-string. **None of the three is a
regression from this phase**: they raise pydantic `ValidationError` or
`AttributeError`, which the deleted arm never caught, so they answered
a 500 before it too. The last two share a structural cause worth phase
6's attention on its own — an `arrayofdict` compiles to
`fields.List(fields.Dict())` with no value schema, so every nested key
in `disk` and `network` is unvalidated by construction, in every
mode. The lesson
worth carrying is not that the script had a bug — it is that the
script was mechanically correct on the second attempt and still aimed
at the wrong property. This phase's own risk register named "a
TypeError source the survey missed becomes a 500" and the survey
still missed one.

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

### F9. Two more 500 classes on the proxy path — mostly already fixed

The full 30-day sample is 266 `Server error` records, and the five
`KeyError`s of F6 are the small half of it:

| n | class | deepest frame |
|---|---|---|
| 260 | `ConnectionError` | `requests/adapters.py` in `send` |
| 5 | `KeyError` | `util/general.py` in `get_user_agent` |
| 1 | `ConnectionRefusedError` | `util/concurrency.py:329` in `_node_lock_request` |

**This finding was substantially wrong, and step 5 caught it.** The
correction is recorded here rather than only in Progress, because a
stale finding left standing is how the next reader inherits the error.

The claim above — "a node being unreachable is answered as a 500, 260
times in a month" — reads a 30-day aggregate as a rate. It is not one.
Split by day, every `ConnectionError` in the sample falls between
2026-08-12 and 2026-08-19, and **252 of the 260 are a single burst on
08-19**. There are none afterwards, because
[`2ac50193b`](https://github.com/shakenfist/shakenfist/commit/2ac50193b)
— *Surface unreachable proxy peers as a 503*, issue #3743 — landed
2026-08-21 and already fixed that path. `proxy_request_to_node` now
catches `requests.exceptions.RequestException` and answers 503.

So the headline defect was fixed a week into the window this plan
measured, and the survey did not notice because it never asked whether
the distribution was uniform. That is the same mistake as F5's first
cut: a derived number nobody checked against a second axis.

What is actually left, verified by step 5 against the current tree and
current Loki data:

* **The `ConnectionRefusedError` is live.** It recurred 2026-08-26,
  *after* the proxy fix, on `POST /instances/<uuid>/poweroff`. It is a
  different mechanism — `_node_lock_request` speaks to a local Unix
  socket belonging to the node's own nodelock daemon, not to a peer —
  so `2ac50193b` never covered it.
* **Two more unguarded proxy call sites the survey missed**, neither
  behind `proxy_request_to_node` and so neither fixed by `2ac50193b`:
  `shakenfist/external_api/blob.py:71` (`_read_remote`) and the
  `upload_uuid` branch of `POST /artifacts`
  (`shakenfist/external_api/artifact.py:588`). Both call
  `requests.request()` with no exception handling.

Filed as
[#4161](https://github.com/shakenfist/shakenfist/issues/4161), scoped
to what is still true rather than to the table above. D28's reasoning
for not fixing it here survives the correction: it still needs a
status-code decision, a retry decision and a caller audit, which is a
phase and not a step. D31 raises its value, since a caller can no
longer see which exception it was.

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

### F11. The deletion relabels the leak rather than removing it

**Found by step 2, after this plan was written and committed.** It is
recorded here rather than silently fixed because it falsifies
something the plan assumed.

`suppress_exceptions_to_client` builds every 500 body as
`'server error: %s' % repr(e)` (`base.py:1684`). So after the arm is
deleted, an undeclared body key under `warn` or `off` answers:

```
500 {"error": "server error: TypeError(\"AuthEndpoint.post() got an
unexpected keyword argument 'zzz'\")", "status": 500}
```

which is #3612's motivating string — endpoint class, method name, and
the fact that kwargs are merged into the call — wearing a 500 instead
of a 400. The plan assumed the generic 500 body was clean. It is not,
and the assumption is load-bearing for D25 and for definition of done
item 4.

It is a second and independent leak site, common to every exception
class rather than specific to this one. It is also what puts
`KeyError('arch_string_raw')` (F6) and the 260 `ConnectionError`
reprs (F9) into caller-visible bodies. `base.py:1684` is the only
production site; `grep` finds no other.

Step 2 did not fix it, correctly: cleaning that body changes the shape
of every 500 the API emits, which is an error-contract decision of the
same size as D25. Instead it asserted what is true — every
`INTERPRETER_TEXT` marker absent *except* the exception class name,
with the exemption named and justified in the test docstring so that
tightening it is a one-line change. That is the right shape for a
finding that outruns its plan.

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

### D31. The generic 500 body stops carrying `repr(e)`

Taken by the operator after step 2 surfaced F11, and added to the
phase rather than filed.

`base.py:1684` becomes `sf_api.error(500, 'server error')`. The detail
does not disappear: `suppress_exceptions_to_client` already logs one
`Server error` line carrying `exception_class`, the full `traceback`,
`method`, `path` and the correlation fields for the on-disk record
under `/srv/shakenfist/exceptions/`, all of which say more than
`repr(e)` in a response body ever did.

Two things argued for doing it here rather than deferring it. It is
what makes the phase's own claim true — a phase whose purpose is to
stop the API answering in the interpreter's words should not ship a
version that only changes which status code the words arrive under.
And it closes the same leak for the classes F6 and F9 found in
production, which are two orders of magnitude more frequent than the
`TypeError` this phase started with.

The cost, stated plainly: a caller debugging from a response body
alone loses the exception detail, and gets `server error` with nothing
else. That is deliberate — the caller is not who the detail is for,
and an operator has both the log line and the exception record. It
goes in the release note for the same reason D25 does.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | **`log_request` returns a 400 instead of raising.** In `shakenfist/external_api/base.py`, `log_request`'s wrapper raises `TypeError('the request body must be a JSON object')` at line 1378 when the parsed JSON body is not a dict. Replace it with `return sf_api.error(400, 'the request body must be a JSON object')`. Keep the surrounding comment but rewrite it: it currently explains that the raise reproduces the old per-key merge's `TypeError`, and after this change the point is that the guard answers directly rather than depending on a catch two decorators out. Do not change anything else in `log_request`. `test_a_non_object_body_is_still_a_400` (`shakenfist/tests/external_api/test_request_validation.py:432`) must pass **unchanged** — all four payloads, same status, same message. Verify by running it before and after. Commit subject: `Answer a non-object body directly.` |
| 2 | high | opus | none | **Delete the `except TypeError` arm.** In `shakenfist/external_api/base.py`, `handle_authorization_exceptions` (line 1500) catches `TypeError` and answers `400 str(e)`. Delete that arm and leave the three JWT arms — `DecodeError`, `ExpiredSignatureError` and the eight-class tuple — untouched. Rewrite the `NOTE(mikal)` comment above the wrapper so it says what the function now catches and why (authorization conditions only; see decision D23 in this plan). Then fix the fallout, which the plan has already enumerated: `test_warn_mode_changes_no_response` (`test_request_validation.py:98`) and `test_off_mode_disables_the_layer` (`:198`) both assert `unexpected keyword argument` appears in the response body; under `warn` and `off` an undeclared body key now answers 500 (decision D25), so rewrite both to assert the new behaviour *and* to assert the response carries no interpreter text. Add the D30 test: a handler which raises `TypeError` internally answers 500, the body contains no Python message, and `util.exceptions.record_exception` was called. Reuse the `INTERPRETER_TEXT` list (`test_request_validation.py:709`) rather than writing a new one; note its comment says a marker added there belongs in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_api_validation.py` too. Before finishing, enumerate every remaining source of `TypeError` reachable from a request: grep the whole tree for `raise TypeError` (step 1 removed the only one in `external_api/`), and check the `@use_kwargs`/webargs sites and `flasgger`. Report what you found even if the answer is nothing. Commit subject: `Stop answering a TypeError as a bad request.` |
| 3 | medium | sonnet | none | **Fix #3523.** `shakenfist/util/general.py:87` `get_user_agent()` indexes `cpuinfo.get_cpu_info()` for `arch_string_raw` and `vendor_id_raw` without guarding. `py-cpuinfo` returns whatever its probes collected, so both keys can be absent, and the resulting `KeyError` reaches a caller as a 500 on `GET /instances/<uuid>/consoledata` via `proxy_request_to_node`. Use `.get()` with a sensible literal for each (`'unknown'` reads correctly in a user agent string). Add a unit test in `shakenfist/tests/` that patches `cpuinfo.get_cpu_info` to return `{}` and asserts `get_user_agent()` returns a string containing the version — mutation test it by restoring one of the bare lookups and confirming the test fails. Do not change the user agent's format for the case where both keys are present; a test should pin that too. Commit subject: `Survive a partial cpuinfo probe.` |
| 4 | low | sonnet | none | **Remove two dead decorator uses.** `@api_base.requires_namespace_exist_if_specified` is applied *below* `@api_base.arg_is_instance_ref` on `InstanceEndpoint.delete` (`shakenfist/external_api/instance.py:268`) and below `@api_base.arg_is_network_ref` on `NetworkEndpoint.delete` (`shakenfist/external_api/network.py:165`). The ref decorator wraps it and opens with `kwargs.pop('namespace', None)` (`base.py:1052`), so its `kwargs.get('namespace')` is always `None` and it can never fire. Delete both applications. Do not reorder them, and do not touch the other ten uses, which are on creation and collection routes and work (decision D29). Confirm no test asserts `namespace not found` on either route before deleting. Add a short comment to `requires_namespace_exist_if_specified` in `base.py` recording that it must be applied *above* any ref decorator, so the next use does not repeat this. Commit subject: `Drop two guards that could never fire.` |
| 6 | medium | sonnet | none | **Stop the 500 body carrying `repr(e)`.** Per D31 and F11. In `shakenfist/external_api/base.py`, `suppress_exceptions_to_client` (line ~1629) ends with `return sf_api.error(500, 'server error: %s' % repr(e), suppress_traceback=True)` at line 1684. Change the message to a bare `'server error'`. Do not touch the `LOG.with_fields(fields).exception('Server error')` line above it or the fields it builds — that line and the on-disk record under `/srv/shakenfist/exceptions/` are where the detail goes, and they already carry more than the body did. Add a comment saying why the body is deliberately opaque: the caller is not who the detail is for, and `repr(e)` in an error field is the same defect as the interpreter text this phase deleted (issue #3612, decision D31). Then find what depended on the old shape: `test_a_handler_internal_type_error_is_a_recorded_500` in `shakenfist/tests/external_api/test_request_validation.py` skips the `'TypeError'` marker in its `INTERPRETER_TEXT` sweep and says in its docstring why — **remove the exemption and the paragraph justifying it**, so the sweep runs whole. Check `shakenfist/tests/external_api/test_server_error_logging.py`, `shakenfist/tests/test_federation.py` (line ~780) and `shakenfist/external_api/label.py` (line ~59) for assertions or comments naming the old body; a comment which now describes something untrue should be corrected, not left. Finally grep `shakenfist/deploy/shakenfist_ci/` for anything reading a 500's error text. Report everything you found. Commit subject: `Stop telling callers what exception broke.` |
| 5 | medium | sonnet | none | **Documentation and close-out.** (a) `docs/developer_guide/writing_an_endpoint.md:308-316` already describes `API_VALIDATION_MODE` and calls `warn` the operator's rollback; extend that passage to say what `warn` and `off` now do with an undeclared body key (500, recorded exception). Do not add a new section. (b) `docs/release_notes/v07-v08.md:188-220` is phase 4's enforcement entry, including the `API_VALIDATION_MODE=warn` rollback sentence at line 210; extend that entry, do not start a second one, noting that the rollback modes no longer answer 400 for input the handler cannot accept. (c) File the F9 issue: the proxy path answers an unreachable node with a 500, with the Loki evidence from this plan's F9 and links to `proxy_request_to_node` and `util/concurrency.py:329`. (d) Update this plan's Progress section, the master plan's Execution table row for phase 5, and the `docs/plans/index.md` phase arithmetic. Commit subject: `Document what narrowing changed.` |

Steps 1 and 2 are strictly ordered, and step 6 follows step 2. Steps 3
and 4 are independent of both and of each other. Step 5 runs last,
because it documents what the others did. Step 2 carries a conditional
gate — see the Back brief.

Step 6 was added after the plan was committed, when step 2 found F11.
It is numbered 6 and sequenced fifth so that the earlier numbers keep
pointing at the commits that already reference them.

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
   (`test_request_validation.py:709`) **with no exemptions**, and that
   an exception was recorded. The test has been mutation-tested by
   restoring the deleted arm, and fails when it is restored. Step 2
   met this with one exemption because of F11; step 6 removes it, and
   the item is not met until the sweep runs whole.
5. The **generic** 500 path carries no exception detail: the body
   `suppress_exceptions_to_client` returns is a fixed string, and the
   item 4 sweep runs unexempted against it. Deliberate `str(e)` on a
   caught exception elsewhere is untouched and stays — nineteen
   handlers answer a Shaken Fist exception class that way, and there
   the message is the API's own vocabulary rather than the
   interpreter's. D31 is about what escapes uncaught, not about
   whether an exception may ever be quoted.

   *This item took two attempts to state, both recorded rather than
   quietly replaced. It first said `grep -n "repr(e)"
   shakenfist/external_api/base.py` returns nothing; step 5 reported
   that as not met, correctly, because step 6's own comment explains
   in prose why the body is opaque and so contains the string. The
   rewrite then over-corrected to "no `sf_api.error()` call
   interpolates an exception", which those nineteen deliberate sites
   falsify. A criterion a comment can break, and a criterion that
   condemns correct code, are both the wrong test — and this plan has
   now produced one of each.*
6. No test in the suite asserts that a response body contains
   `unexpected keyword argument` or `missing 1 required positional
   argument`.
7. `get_user_agent()` returns a string when `cpuinfo.get_cpu_info()`
   returns `{}`, pinned by a test which fails if either guarded lookup
   is restored to a bare index.
8. #3523 is closed, and the closing comment names
   `util/general.py:get_user_agent` and `KeyError: 'arch_string_raw'`
   as the raising frame the issue asked for.
9. `requires_namespace_exist_if_specified` is applied above every ref
   decorator that uses it, or not at all; the two applications named
   in F8 are gone and the other ten are untouched.
10. An issue exists for F9, linked from the master plan's Future work.
11. What `warn` and `off` do with an undeclared body key is stated the
    same way in `docs/developer_guide/writing_an_endpoint.md`, the
    v07-v08 release note, and this plan.
12. `pre-commit run --all-files` is clean.
13. The master plan's Execution table, its status line, and the
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

Executed 2026-09-10. Steps 1-4 and 6 landed as five commits, in
execution order below; step 6 was added mid-phase, after step 2
found the problem it fixes (F11). This section is step 5, and it
runs last as the step plan says it should.

### Step 1, `efc0ca6eb` — Answer a non-object body directly

`log_request`'s non-object body guard stopped depending on the catch
step 2 was about to delete: it now returns `sf_api.error(400, 'the
request body must be a JSON object')` itself instead of raising
`TypeError` for `handle_authorization_exceptions` to catch. No
behaviour change — `test_a_non_object_body_is_still_a_400` passes
with its four-payload assertions unchanged both before and after,
which is why this is its own commit ahead of the deletion (D24).

### Step 2, `6220496df` — Stop answering a TypeError as a bad request

The `except TypeError` arm in `handle_authorization_exceptions` is
deleted (D23); the three JWT arms are untouched. The `NOTE(mikal)`
comment above the wrapper was rewritten to say what it now catches —
ten JWT exception classes across three arms, nothing else — and why.
Under `enforce`, the default since phase 4, nothing changes for a
caller: an undeclared body key is refused by name before a handler is
reached. Under `warn` and `off` such a key now reaches the handler,
raises `TypeError`, and answers 500 rather than the
400-with-interpreter-text it used to (D25) — the phase's one intended
behaviour change.

The enumeration D25 rests on was re-run rather than taken on trust
from the plan, and is recorded in the commit message: no production
code raises `TypeError` outside a handler-signature mismatch, no
handler has a parameter without a default, the four `use_kwargs`
schemas filter unknown keys before the call, `_webargs_error` ends in
an `HTTPException`, and `check()` itself cannot raise.

Two tests asserted the deleted arm's behaviour without being named in
the plan's step brief: `test_findings_are_emitted_with_the_response_status`
asserted 400 twice, and `test_type_error_400_is_attributable` was a
whole test for the arm; both were rewritten rather than deleted, the
second becoming a test that a `TypeError` escapes the wrapper unlogged.
The D30 test, `test_a_handler_internal_type_error_is_a_recorded_500`
(`shakenfist/tests/external_api/test_request_validation.py:815`), was
added here.

**Found while implementing, not fixed here: F11.**
`suppress_exceptions_to_client` builds every 500 body as
`'server error: %s' % repr(e)`, so deleting the arm relabelled the
leak instead of removing it: an undeclared body key under `warn`/`off`
went on carrying interpreter text, now under a 500 instead of a 400.
This falsified the plan's assumption that the generic 500 body was
already clean, so step 2's own test had to carry an exemption for the
exception-class marker, with the exemption's reason written into its
docstring rather than silently worked around. The operator was asked
whether to clean the body here or file it, and chose to clean it —
which is step 6.

### Step 3, `dc6019d6a` — Survive a partial cpuinfo probe

Fixes [#3523](https://github.com/shakenfist/shakenfist/issues/3523).
`get_user_agent()` (`shakenfist/util/general.py`) now uses `.get()`
with a literal default for both `arch_string_raw` and `vendor_id_raw`
instead of indexing `cpuinfo.get_cpu_info()` directly. Three tests —
both keys present, an empty probe, and one key present without the
other, the third pinning that the two lookups fall back
independently — mutation-tested by restoring one bare index, which
fails with the production `KeyError: 'arch_string_raw'` signature.
#3523 is closed, with a comment naming the raising frame and this
commit.

### Step 4, `aeb318519` — Drop two guards that could never fire

Per F8. `@api_base.requires_namespace_exist_if_specified` is removed
from `InstanceEndpoint.delete` and `NetworkEndpoint.delete`, where it
sat below a ref-resolving decorator that had already popped
`namespace` out of `kwargs` before this decorator ever saw it — dead
since phase 4 found it and recorded it without filing an issue. All
twelve uses of the decorator were surveyed, not just the two; the
other ten are on creation and collection routes with no ref decorator
above them and are live. The decorator itself now carries a comment
naming the ordering constraint and the two routes it went wrong on,
so the next use does not repeat the mistake.

### Step 6, `92fc4bb6b` — Stop telling callers what exception broke

Added to the phase after step 2 found F11, above. Sequenced fifth in
execution order — after step 4, before this step 5 — while keeping the
number 6, so the earlier step numbers keep pointing at the commits
that already cite them (per the plan's own note above). The prompt
that authorised it: Michael was asked whether the phase should clean
the generic 500 body or file it, and chose to clean it here.

`suppress_exceptions_to_client`'s 500 body is now the bare string
`'server error'`. The `LOG.with_fields(fields).exception('Server
error')` line immediately above it, and the on-disk record under
`/srv/shakenfist/exceptions/`, are unchanged and carry the detail
instead — `exception_class`, the full traceback, method, path, and the
correlation fields. `test_a_handler_internal_type_error_is_a_recorded_500`
now runs the whole `INTERPRETER_TEXT` sweep with no exemption, and the
docstring paragraph justifying the exemption is gone with it.

Four comments and two assertions described the old body and were
corrected rather than left. Two are about the present and were
rewritten because they had become false: `test_server_error_logging`
asserted the exception class appears in the response, and now asserts
it must not; a comment in `shakenfist/external_api/auth.py` explaining
a guard around a damaged federation rule said the generic 500 handler
"answers with `repr(e)`", which is no longer true, and was rewritten
to say what the guard is for now (a categorised, evented refusal
rather than an unevented 500) instead of deleting the guard, since its
reason changed but it is not dead. Two are history and were left
alone except for a note: `shakenfist/external_api/label.py` and
`shakenfist/tests/test_federation.py` record what a caller saw before
each was independently fixed, and keep the string they quote because
it was true when it happened, with a note added that a 500 no longer
names anything at all.

### Step 5, this edit — Documentation and close-out

(a) `docs/developer_guide/writing_an_endpoint.md` now says what `warn`
and `off` do with an undeclared body key, in the same passage that
already described the rollback rather than in a new section. (b) The
`API_VALIDATION_MODE` entry in `docs/release_notes/v07-v08.md` is
extended, not duplicated, with both behaviour changes: the rollback no
longer answers 400 for input a handler cannot accept, and every 500
body is now the bare string `server error` for every cause, not only
this phase's. (c) An issue was filed for F9 — see the next section,
because filing it required re-verifying the finding first, and the
finding had partly gone stale. (d) This Progress section, the
Definition of done walk below, the master plan's phase 5 row and its
*Where the tracked issues stand* section, and `docs/plans/index.md`'s
phase count.

### F9 was re-verified before filing, and the picture had changed

The plan's F9 table — 30 days of sfcbr `Server error` records: 260
`ConnectionError` via `proxy_request_to_node`, 5 `KeyError` via
`get_user_agent`, 1 `ConnectionRefusedError` via `_node_lock_request`
— was measured before this phase started executing. Filing it
required re-running the query, which found the table no longer said
the whole truth:

* **The 260 `ConnectionError` records are not live any more, for that
  call site.** Re-running `{job="shakenfist"} |= "Server error" |=
  "ConnectionError"` over the same 30-day window: all 260 timestamps
  fall between 2026-08-12 and 2026-08-19.
  [#3743](https://github.com/shakenfist/shakenfist/issues/3743)
  (`2ac50193b`, PR
  [#3833](https://github.com/shakenfist/shakenfist/pull/3833), merged
  2026-08-21) made `proxy_request_to_node` answer 503 for exactly this
  condition, and there have been zero occurrences since. The five
  `KeyError`s are fixed by step 3, above.
* **Two more unguarded proxy call sites exist**, found by one grep
  pass and absent from the plan's survey:
  `shakenfist/external_api/blob.py:71` (`_read_remote`, proxying
  `GET /blobs/<uuid>/data` to whichever node holds the blob) and
  `shakenfist/external_api/artifact.py:588` (the `upload_uuid` branch
  of `POST /artifacts`, proxying to the node an upload landed on).
  Neither is covered by the #3743 fix, and neither has log evidence,
  because nobody has hit the failure window in the last 30 days.

The 1-occurrence `ConnectionRefusedError` is still live: it recurred
on 2026-08-26, five days after the #3743 fix landed, on
`POST /instances/<uuid>/poweroff`, while releasing the instance lock.
It is a different mechanism from the other two — a local Unix socket
to the node's own nodelock daemon refusing a connection, not a peer
node being unreachable — which the #3743 fix does not touch and could
not have.

Filed as [#4161](https://github.com/shakenfist/shakenfist/issues/4161),
scoped to what re-verification found still true rather than to the
table as originally surveyed, with the correction recorded in the
issue itself so a future reader does not have to reconcile these two
sources disagreeing.

### Definition of done, item by item

1. **Met.** `grep -n "except TypeError" shakenfist/external_api/base.py`
   returns nothing.
2. **Met.** `grep -rn "raise TypeError" shakenfist/external_api/`
   returns two comments in `validation.py` (explaining webargs' own
   behaviour) and no executable statement, which is exactly what the
   item excepts.
3. **Met.** `test_a_non_object_body_is_still_a_400` passes with its
   four-payload assertions unchanged; step 1's commit message records
   running it before and after both step 1 and step 2.
4. **Met, as of step 6.**
   `test_a_handler_internal_type_error_is_a_recorded_500`
   (`test_request_validation.py:815`) asserts 500, every
   `INTERPRETER_TEXT` marker absent with no exemption, and that
   `record_exception` was called once, and it is mutation-tested by
   restoring the deleted arm. Step 2 met an earlier version of this
   item with one exemption, which F11 required; step 6 removed the
   exemption, so this item is met by step 6's commit, not step 2's —
   exactly the distinction the plan's own text under item 4 called
   for.
5. **Met, with a literal caveat worth recording rather than hiding.**
   `grep -n "repr(e)" shakenfist/external_api/base.py` does not return
   nothing — it returns two hits, both inside the comment step 6 added
   directly above the fix, explaining in prose why the body no longer
   carries `repr(e)`. No *code* builds a response body from it any
   more, and the substantive claim the item is actually checking for —
   that no response body contains an exception class name, a repr, a
   traceback or a source path — is what
   `test_a_handler_internal_type_error_is_a_recorded_500` proves with
   its unexempted sweep. The item's own verification command, read
   literally, does not pass; its intent does.
6. **Met.** No test asserts either fragment appears in a response
   body. The surviving text hits are `test_request_validation.py:185`,
   which asserts the *absence* of `unexpected keyword argument`; the
   `INTERPRETER_TEXT` list itself and a docstring quoting the old
   response as history; and an unrelated comment in
   `test_clusteroperations.py` about a different code path.
7. **Met.** Pinned by the three tests step 3 added, mutation-tested by
   restoring a bare index.
8. **Met.** #3523 is closed, with a comment naming
   `util/general.py:get_user_agent` and `KeyError: 'arch_string_raw'`
   as the raising frame and linking `dc6019d6a`.
9. **Met.** The two dead applications are gone (step 4); `grep -rn
   "requires_namespace_exist_if_specified" shakenfist/external_api/*.py`
   shows the other ten, untouched.
10. **Met.** [#4161](https://github.com/shakenfist/shakenfist/issues/4161)
    filed and linked from the master plan's *Where the tracked issues
    stand* section. Scoped to what re-verification found still true
    rather than to the plan's original table — see the F9 section
    above for why that is not the same thing as the table as surveyed.
11. **Met.** The same two facts — the rollback answers 500 instead of
    400 for an undeclared body key, and every 500 body is now the bare
    string `server error` — are stated in
    `docs/developer_guide/writing_an_endpoint.md`, the v07-v08 release
    note, and this plan (D25, D31, F4, F11).
12. **Met.** `pre-commit run --all-files` is clean: 4552 tests passed,
    121 skipped, 0 failed, and every other hook (flake8, the four
    `external_api` guard checks, `check-plan-status.py`, mypy) passed.
    Recorded honestly: the first run reported the `py3` hook itself as
    `Failed` with `files were modified by this hook`, while its own
    `stestr` totals inside that same run already showed 0 failures and
    `git status`/`git diff` showed no tracked file changed beyond this
    edit's own doc changes. A second, unmodified rerun passed the `py3`
    hook cleanly with no such report, so this was a one-off (most
    likely a first-run artefact of `tox` reinstalling the package into
    a fresh environment) rather than a reproducible problem, and it is
    noted here rather than quietly rerun-until-green.
13. **Met by this edit.** The master plan's Execution table row, its
    status line, and the `docs/plans/index.md` row all say phase 5 is
    complete and agree on the arithmetic (6 of 8), checked by
    `tools/check-plan-status.py`.
