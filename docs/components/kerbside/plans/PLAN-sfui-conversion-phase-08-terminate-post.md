# sfui conversion phase 8: terminate actions to POST

Planning effort: **high**, as the master plan requires. The
phase changes an authenticated HTTP contract that four
out-of-browser callers depend on, and it is the first phase
of this conversion to touch `kerbside/api.py` at all.

## Situation

Issue #133 reports a blind CSRF: destructive admin actions are
GET handlers, `verify_token` accepts the JWT from cookies, and
flask-jwt-extended's cookie CSRF protection does not cover GET.
A logged-in admin who loads a hostile page can be made to issue
authenticated GETs that fire the side effects.

The master plan's decision 4 committed to fixing this by
converting the terminate actions to POST, and phases 5–7 built
towards it: phase 5 made the terminate buttons real `<button>`s
driven by a data attribute, phase 6 extracted the shared script
into `templates/includes/two-step-terminate.html`, and phase 7
exposed `window.kbPoll` so a page can repaint on demand. The
include even carries a comment naming this phase
(`two-step-terminate.html:24-26`).

## What the survey found

The survey checked every claim in the master plan's phase 8
section and decision 4 against the tree at `aa7e5da`. Four of
them are wrong, and it found one destructive handler the master
plan does not mention at all.

1. **There are three destructive GETs, not two.** Issue #133
   names a "ticket write" as well as the two terminates:
   `ConsolesProxyVirtViewer.get` (`kerbside/api.py:434-490`)
   calls `db.store_console_ticket` at `:472` and `:475` and
   mints a console token at `:477`. The master plan's phase 8
   section says only "terminate actions". See decision 3 for
   why this phase does not convert it, and issue #319, filed
   during this survey, for the follow-up.

2. **The tempest plugin does not touch these endpoints.**
   Decision 4 says the tempest plugin must be updated. It has
   no terminate calls at all — the only `terminat*` matches in
   `tempest-plugin/` are prose about CRLF-terminated lines
   (`tests/scenario/test_sextant_scenario.py:179-180`).

3. **`tools/direct-qemu/lane-up.sh` does not call terminate
   either.** Decision 4 names it as an affected consumer. It
   contains zero occurrences of the string; it mints the JWT
   that `verify-terminate-live.sh` later reads from
   `${WORKDIR}/kerbside-api-token.txt`. Only the verify script
   changes.

4. **Two real consumers are missing from the master plan.**
   Both would break silently on a method change, and neither is
   mentioned anywhere in decision 4:
   - `tools/sf-e2e/drive-happy-path.py:253` —
     `requests.get(url, headers={'Authorization': 'Bearer ...'})`
   - `tools/ovirt-e2e/drive-console.py:494` —
     `requests.get(url, headers=headers)` with the Bearer
     headers built at `:395-396`

5. **The CSRF machinery is already on.** Probed empirically
   against `flask-jwt-extended==4.7.4` (not the 4.6.0 issue
   #133 cites) with kerbside's exact wiring — see *Key facts*
   below for the matrix. `JWT_COOKIE_CSRF_PROTECT` already
   defaults to `True`, so converting to POST buys the
   double-submit check with no config change. The corollary
   matters more: **a browser POST without the header gets a
   401**, so the JS must send it or terminate simply stops
   working.

6. **Bearer-token callers need no CSRF token.** A POST
   carrying `Authorization: Bearer` and no cookie returns 200.
   All four out-of-browser callers authenticate that way, so
   they need a method change and nothing else.

7. **The unit tests cannot prove CSRF enforcement.** Both
   terminate test cases patch `kerbside.api.verify_jwt_in_request`
   to a no-op (`test_api.py:55-58`), so no test in the tree
   exercises the auth path at all. Verification 2 covers this
   with a standalone probe instead of pretending a unit test
   can.

8. **Both terminate pages poll.** `Consoles.get` (`api.py:283`)
   and `Sessions.get` (`api.py:770`) both render with
   `refresh=True`, so `window.kbPoll` from phase 7 exists on
   both pages that carry terminate buttons. Decision 5 uses it.

9. **`ARCHITECTURE.md`'s endpoint table is wrong in two ways.**
   Line 220 lists `GET /session/<id>/terminate`; the console
   terminate route is absent entirely, and line 217 gives the
   `.vv` path as `/console/<source>/<uuid>/console.vv` when the
   real routes are `/console/direct/...` and `/console/proxy/...`
   (`api.py:848-849`).

10. **`ConsolesDirectVirtViewer.get` is a pure read.**
    (`api.py:369-433`.) It needs no change, which is worth
    stating because it sits beside the proxy handler that does.

Corrections 1–4 have been applied to the master plan's decision
4 and phase 8 note, and to the `docs/plans/index.md` row, in the
same commit as this plan. A later step should not redo them.

## Mission

Convert `ConsolesTerminate` and `SessionTerminate` to POST, wire
the browser's fire path to send the CSRF double-submit header,
move the four out-of-browser callers, and harden the cookie so
the vector #133 describes is dead even for the GET that remains.
Close #133, with #319 carrying the residual.

## Design decisions

### 1. POST, not DELETE

DELETE is equally CSRF-protected and arguably more RESTful for
"tear down this session". POST wins on two counts: the master
plan already committed to it in decision 4 and in the comment
left in `two-step-terminate.html`, and the endpoints are not a
clean resource delete — `ConsolesTerminate` tears down *every*
session for a console and returns the list of affected tokens.
POST also keeps `curl --request POST` working through any
intermediary that filters exotic methods.

### 2. The server change is the method keyword and nothing else

`def get` becomes `def post` on both classes. The bodies,
including the `Accept: text/html` 302 branch, stay exactly as
they are. Keeping the redirect branch costs nothing, preserves
the behaviour the existing HTML-mode tests assert, and leaves a
working path for a non-JavaScript client that posts a form.

### 3. The `.vv` ticket write stays GET, and is mitigated rather
### than converted

This is the decision most likely to be argued with, so the
reasoning is spelled out. `ConsolesProxyVirtViewer` returns a
file that the browser hands to `remote-viewer`; today that is a
plain navigation. Converting it to POST means either a form
navigation — which does **not** satisfy the CSRF check, because
`JWT_CSRF_CHECK_FORM` defaults to `False` — or replacing a
browser-native download with a `fetch` and a synthesised blob.
It also sits on the product's core path, exercised by three CI
lanes, one of which (oVirt) only runs in a merge queue that is
currently blocked by #308.

Instead this phase sets `JWT_COOKIE_SAMESITE = 'Lax'`, which
stops the browser sending the cookie on cross-site subrequests —
the `<img src=...>` vector #133 actually describes. The residual
is a cross-site top-level navigation, which Lax still permits:
the victim sees a download, and the attacker still cannot read
the minted token. That residual is issue #319.

Lax rather than Strict because kerbside is linked to from other
console UIs; under Strict, following such a link would land the
operator on the login page.

### 4. Make the CSRF defaults explicit

`JWT_COOKIE_CSRF_PROTECT = True` is already the library default,
so setting it changes no behaviour today. It goes in anyway,
beside the SameSite line and with a comment, because the whole
protection of this phase rests on a default in a dependency
Renovate upgrades automatically. A future default flip should
break a test, not silently unprotect the endpoints.

`JWT_COOKIE_SECURE` stays unset: turning it on would break the
plain-HTTP CI lanes and local development. Recorded as future
work rather than smuggled in here.

### 5. The browser posts JSON and then calls `window.kbPoll()`

The fire line sends `Accept: application/json`, so the handler
returns its JSON body and the 302 branch never runs for the
browser — no redirect for `fetch` to follow, no full page load.
On success the JS calls `window.kbPoll()` (phase 7, present on
both pages per survey finding 8, guarded with a truthiness check
anyway) so the table repaints immediately instead of waiting up
to 30 seconds.

### 6. A failed terminate says so on the button

On a non-2xx response or a network failure the button's label
becomes `Failed` and it stays disarmed. There is no toast
component yet (master plan future work), and silence after a
destructive click is the worst option. This self-heals: a
`Failed` button is not `window.armedTerminate`, so phase 7's
`onBeforeElUpdated` hook does not protect it and the next poll
tick repaints it with the server's label.

### 7. Historical phase plans are left alone

`PLAN-sfui-conversion-phase-05-consoles.md:299-300` says the
routes are "both GET", and `PLAN-rust-proxy-phase-07-ci.md:152`
says POST when they are GET today. Both are records of what was
true, or intended, when written. Only live documents get
corrected: the master plan, `index.md`, `ARCHITECTURE.md`,
`AGENTS.md` and `docs/`.

## Key facts front-loaded for the sub-agents

Measured on `flask-jwt-extended==4.7.4` with kerbside's exact
wiring (`JWT_SECRET_KEY` the only JWT config,
`verify_jwt_in_request(False, False, False, ['headers',
'cookies'], True)`):

| Request | Result |
|---|---|
| GET + cookie, no header | 200 |
| POST + cookie, no header | 401 `Missing CSRF token` |
| POST + cookie + `X-CSRF-TOKEN` | 200 |
| POST + cookie + wrong token | 401 `CSRF double submit tokens do not match` |
| POST + `Authorization: Bearer`, no cookie | 200 |

Relevant defaults, none of them set in `kerbside/api.py` today:

```
JWT_COOKIE_CSRF_PROTECT      True
JWT_CSRF_METHODS             ['POST', 'PUT', 'PATCH', 'DELETE']
JWT_ACCESS_CSRF_HEADER_NAME  'X-CSRF-TOKEN'
JWT_CSRF_IN_COOKIES          True
JWT_CSRF_CHECK_FORM          False
JWT_COOKIE_SAMESITE          None
JWT_COOKIE_SECURE            False
```

`set_access_cookies` sets two cookies: `access_token_cookie`
(HttpOnly) and `csrf_access_token` (**not** HttpOnly, `Path=/`),
whose value is the token to echo in the header. Reading it from
`document.cookie` is the intended mechanism.

Template JS style, unchanged since phase 4: `var`, function
statements, no arrow functions, no template literals, all
listeners delegated on `document`.

## Repository and branch logistics

Worktree `/srv/kasm_profiles/mikal/vscode/src/shakenfist/kerbside-wt-terminate-post`,
branch `sfui-conversion-phase-08`, cut from `origin/develop` at
`aa7e5da`. One commit per step. Concurrent sessions share this
clone: check `git status --short` immediately before staging,
and never fetch into a branch that is checked out elsewhere.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | high | opus | none | The contract change, server and browser together so the commit is self-contained. In `kerbside/api.py`: rename `ConsolesTerminate.get` (`:495`) and `SessionTerminate.get` (`:783`) to `post`, changing nothing else in either body — the `Accept: text/html` 302 branches stay. Beside `app.config['JWT_SECRET_KEY']` (`:54`) add `app.config['JWT_COOKIE_CSRF_PROTECT'] = True` and `app.config['JWT_COOKIE_SAMESITE'] = 'Lax'` with a comment saying the first is already the library default and is pinned here so a dependency upgrade cannot silently unprotect the endpoints, and the second kills the cross-site subrequest vector of #133 for the `.vv` GET that remains (see #319). In `kerbside/api/templates/includes/two-step-terminate.html`: replace the single line `window.location = button.dataset.kbTerminateUrl;` (`:27`) and its three-line comment above it with a `fetch` POST — `{method: 'POST', headers: {'Accept': 'application/json', 'X-CSRF-TOKEN': <value of the csrf_access_token cookie>}}` — plus a small `document.cookie` reader function. On a non-ok response or a rejected promise set `button.textContent = 'Failed'` and leave `armedTerminate` null; on success call `window.kbPoll()` if it is defined. Do not attach listeners to rendered content; the existing delegated listener stays exactly as it is, and so does all the arming logic. Update the four existing tests that still issue GETs — `kerbside/tests/unit/test_api.py:70` and `:91`, `kerbside/tests/unit/test_api_html.py:286` and `:303` — to `self.client.post(...)`, and delete the now-stale NOTE comments at `test_api_html.py:273-275` and `:292` that forward-reference this work. Add one new test per class asserting a GET now returns 405, which is the falsifiable proof the method actually moved. Keep the smoke-test discipline: markers, never markup, and never assert on the JavaScript text of a template. |
| 8b | medium | sonnet | none | The four out-of-browser callers. All authenticate with `Authorization: Bearer` and therefore need a method change and nothing else — no CSRF token, no other edit. `tools/direct-qemu/verify-terminate-live.sh`: add `--request POST` to the `curl` at `:76-82` and fix the `echo` at `:75` that prints `GET ${TERMINATE_URL}`. `tools/sf-e2e/drive-happy-path.py:253`: `requests.get` → `requests.post`. `tools/ovirt-e2e/drive-console.py:494`: `requests.get` → `requests.post`. Check the surrounding log lines in both Python drivers for the word GET and fix any that now lie. Do not touch `tools/direct-qemu/lane-up.sh` (it only mints the JWT) or the tempest plugin (it never calls these endpoints). |
| 8c | medium | sonnet | none | Documentation. `ARCHITECTURE.md`: in the endpoint table around `:214-220`, change `GET /session/<id>/terminate` to POST, add the missing `POST /console/<source>/<uuid>/terminate` row, and correct `:217`'s `.vv` path — the real routes are `/console/direct/<source>/<uuid>/console.vv` and `/console/proxy/<source>/<uuid>/console.vv` (`api.py:848-849`). Check `ARCHITECTURE.md:140-143` and `docs/proxy-architecture.md:393-400` for method claims and fix any that say GET. `AGENTS.md:213-215` describes `verify-terminate-live.sh` calling the REST terminate endpoint — make the method explicit there. Add a short note to `docs/development.md` recording that browser terminates now carry the `X-CSRF-TOKEN` double-submit header and that the cookie is `SameSite=Lax`, so a developer who sees a 401 on terminate knows where to look. Do not edit any file under `docs/plans/` — the master plan and index were corrected in the planning commit, and historical phase plans are left as written. |

## Verification

Run all of these before proposing the closeout commit.

1. **Mechanical greps.** Both classes define `post` and neither
   defines `get`; the include no longer contains
   `window.location`; the two config lines are present:

   ```
   grep -n 'def get\|def post' kerbside/api.py | sed -n '/49[0-9]\|50[0-9]\|78[0-9]/p'
   grep -c 'window.location' kerbside/api/templates/includes/two-step-terminate.html   # expect 0
   grep -n 'JWT_COOKIE' kerbside/api.py                                                # expect 2
   grep -rn "requests.get\|--request GET" tools/sf-e2e tools/ovirt-e2e tools/direct-qemu | grep -i terminate   # expect nothing
   ```

2. **The auth-behaviour probe.** The unit tests cannot cover
   this (survey finding 7), so re-run the standalone probe: a
   throwaway venv with `flask-jwt-extended==4.7.4`, a Flask app
   mirroring `verify_token` and `set_access_cookies`, asserting
   the five rows of the matrix in *Key facts*. The probe is
   scratchpad-only and is not committed; its purpose is to
   confirm the dependency still behaves as this plan assumes.

3. **A DOM harness for the fire path**, in the style of phase
   7's. Render the consoles page with `tools/preview-templates.py`,
   serve it over HTTP, append a script that stubs `window.fetch`
   and `document.cookie`, then assert:
   - two clicks on a terminate button issue exactly one fetch,
     to the button's `data-kb-terminate-url`, with
     `method: 'POST'`;
   - the request carries `X-CSRF-TOKEN` equal to the
     `csrf_access_token` cookie value, and
     `Accept: application/json`;
   - one click alone issues no fetch at all (the arming step is
     unchanged);
   - a 401 response leaves the button reading `Failed` and
     `window.armedTerminate` null;
   - a successful response calls `window.kbPoll`.

   Assertions are reported through `document.title` and captured
   with `chromium --headless --dump-dom`, as in phase 7.

4. **The test suite.** `pre-commit run --all-files` green,
   including the two new 405 tests.

5. **CI lanes.** The direct-qemu lane runs
   `verify-terminate-live.sh` on the PR itself, so 8b's shell
   change is proven by the PR's own smoke tier. sf-e2e is also a
   PR gate. The oVirt driver is merge-queue only and will not be
   exercised until the queue runs — noted in Risks.

## Success criteria

* `ConsolesTerminate` and `SessionTerminate` accept POST and
  return 405 for GET, and no other handler's method changed.
* A browser terminate fires exactly one POST carrying a
  `X-CSRF-TOKEN` header whose value matches the
  `csrf_access_token` cookie, and the table repaints without a
  page load.
* A failed terminate is visible on the button rather than
  silent.
* `grep -rn` finds no caller of either endpoint still issuing a
  GET, in `kerbside/`, `tools/`, or `.github/`.
* `JWT_COOKIE_SAMESITE` is `'Lax'` and `JWT_COOKIE_CSRF_PROTECT`
  is explicitly `True`.
* No fact about either endpoint's method is stated differently
  in `ARCHITECTURE.md`, `AGENTS.md` and `docs/`.
* Nothing under `kerbside/api/static/` changes, so the daily
  `sfui-vendor` audit stays green.
* #133 can be closed honestly, with #319 recording exactly what
  was not fixed and why.

## Risks

- **The browser path breaks and terminate silently stops
  working.** A missing or misnamed header means a 401 on every
  click. Caught by harness assertion 3 (header present and equal
  to the cookie) and by decision 6 making the failure visible on
  the button rather than silent.
- **A consumer is missed and a CI lane fails in the merge
  queue.** The survey enumerated every caller in the tree; the
  grep in Verification 1 is the backstop. The oVirt driver is
  the one that cannot be proven before the queue runs, which is
  itself blocked by #308 — expect the same triage outcome as
  #311 and hold the re-queue rather than debugging phantom
  failures.
- **`SameSite=Lax` breaks an embedded deployment.** If any
  deployment renders the kerbside admin UI inside an iframe on
  another origin, the cookie will no longer be sent. Nothing in
  this repo or its docs describes such a deployment, and the
  admin UI is not the console handoff path, but the operator
  should confirm before merge.
- **A dependency upgrade silently unprotects the endpoints.**
  Mitigated by decision 4 pinning the default explicitly. The
  deeper mitigation — a test that fails when the default flips —
  is out of reach while the tests patch out auth entirely
  (survey finding 7); recorded as future work.

## Future work recorded here

- Issue #319: the `.vv` ticket-write GET, its options and its
  residual risk.
- `JWT_COOKIE_SECURE` for HTTPS deployments, with a way to keep
  the plain-HTTP CI lanes working (a config knob defaulting to
  on, overridden in the lanes).
- A test fixture that exercises the real auth path instead of
  patching `verify_jwt_in_request`, which would let CSRF
  enforcement be asserted in CI rather than by a scratchpad
  probe.
- The toast-notification replacement for the two-step confirm
  (already in the master plan) would subsume decision 6's
  `Failed` label.

## Back brief

Before executing any step, back brief the operator on this plan
and how the intended work aligns with it. Step 8a is the phase;
if its shape needs arguing — POST versus DELETE, leaving the
`.vv` handler on GET, the SameSite change, the `Failed` label —
argue before it runs, because the harness in Verification 3 and
the tests in 8a are written against those decisions.

## Outcome

Four commits on `sfui-conversion-phase-08`, cut from `aa7e5da`:

- `d28853c` — this plan, plus the four master-plan corrections
  from *What the survey found* applied at their source.
- `9b9bc4a` — 8a: both handlers to POST, the cookie config, the
  browser fetch, the four migrated tests and the two new
  405 tests.
- `2c07218` — 8b: the three out-of-browser callers.
- `cbec8f1` — 8c: `ARCHITECTURE.md`, `AGENTS.md` and
  `docs/development.md`.

**One accepted deviation, in 8a.** The brief said the arming and
disarming logic was to be left exactly as it was, and the
implementer changed the fire path anyway: it now calls
`disarmTerminate()` before issuing the fetch. That is correct and
the brief was wrong. `window.location` used to make the question
moot by navigating away; a `fetch` does not, so without the
disarm `armedTerminate` would still point at the button while the
request is in flight — a second click would fire a second POST,
decision 6 could not "leave `armedTerminate` null", and phase 7's
`onBeforeElUpdated` hook would protect the button from repaint
forever. The management session had reached the same conclusion
independently: `disarmed-after-success` was already an assertion
in the harness before the implementer reported.

**Verification results.**

- The DOM harness grew from the five assertions specified in
  Verification 3 to sixteen, and all sixteen pass — against both
  the consoles page *and* the sessions page, since the include is
  shared and phase 6 taught that per-page differences bite.
  Non-vacuity is structural here: `arm-issues-no-fetch` asserts
  zero requests and `posts-once` asserts one, so the transition
  is real, and `csrf-header` compares against a value obtainable
  only through the stubbed `document.cookie`, so it cannot pass
  unless the token reader actually runs.
- The flask-jwt-extended matrix in *Key facts* was re-measured on
  4.7.4 and holds.
- The config keys were checked for the silent-typo failure mode
  the Risks section worries about: with both lines applied,
  `Set-Cookie` really does carry `SameSite=Lax`. A misspelled
  config key would have been ignored without complaint.
- Mechanical greps: both classes define `post` and neither
  defines `get`; no `window.location` remains in the include;
  exactly two `app.config['JWT_COOKIE...']` assignments; no
  caller anywhere in `tools/` still issues a GET; and
  `kerbside/api/static/` is byte-identical to develop, so the
  daily `sfui-vendor` audit stays green.
- 160 tests pass, including the two new 405 tests.

**Considered and found harmless.** A poll tick can land between
the fetch starting and failing. morphdom updates matched nodes in
place rather than replacing them (phase 7's `identity-kept`
assertion), so the `Failed` label is written to the live node
either way; if the tick lands after the label is set, that is the
self-healing repaint decision 6 intends.

**Not done, deliberately.** `ConsolesProxyVirtViewer` remains a
GET that mints a token and writes a ticket. Decision 3 explains
why, the `SameSite=Lax` cookie removes the vector #133 actually
describes, and #319 carries the residual with its three options.
#133 is closed by this phase on that basis rather than on a claim
that every destructive GET is gone.
