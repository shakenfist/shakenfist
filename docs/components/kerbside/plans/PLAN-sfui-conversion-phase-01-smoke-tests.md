# sfui conversion phase 1: template smoke tests

Master plan: `PLAN-sfui-conversion.md`. This phase was
planned at medium effort per the master plan's phase notes.

## Situation

No test has ever rendered a kerbside template: the only
API-layer tests (`kerbside/tests/unit/test_api.py`) always
send `Accept: application/json` or hit `.vv` endpoints. The
sfui conversion is about to rewrite every template, so the
safety net comes first, while the templates are still the
old ones. If these tests pass before and after each
conversion phase, we know the pages still render and still
show their data.

The established test idiom (from `test_api.py`) is:
`testtools.TestCase` subclasses driving
`api.app.test_client()` with `api.app.config['TESTING'] =
True`, JWT verification stubbed by patching
`kerbside.api.verify_jwt_in_request` to return `(None,
{})`, and the db layer mocked per-test with
`@mock.patch('kerbside.api.db.<fn>')` decorators. No real
database or auth is required. Tests run via stestr
(`tox -epy3`), discovery path `./kerbside/tests/unit`.

## Mission

One smoke test per HTML page (login, consoles, sessions,
sources, audit) plus the two HTML-mode redirect behaviours,
asserting on **fixture data markers, not markup** — the
tests must survive the conversion unchanged (except where a
later phase deliberately changes behaviour, noted below).
Two of the tests double as leak guards for the
unfiltered-template-context hazard: the HTML paths receive
raw db dicts (the JSON paths strip `ticket` and
`password`; the HTML paths do not), so the fixtures include
those sensitive fields and the tests assert they never
appear in the rendered page.

## Key facts front-loaded for the sub-agent

- Every HTML view branches on
  `flask.request.headers.get('Accept', 'text/html')`
  containing `text/html`. Send `Accept: text/html`
  explicitly in every request.
- `Root.get` (`kerbside/api.py:117-148`) calls
  `verify_jwt_in_request` directly (not via the
  `verify_token` decorator) and renders `login.html` only
  when it raises `NoAuthorizationError`. An unpatched
  unauthenticated request raises that naturally (the app
  is fully constructed at import, as `test_api.py`
  demonstrates), so the login test needs no JWT patch. If
  that proves flaky, patch
  `kerbside.api.verify_jwt_in_request` with
  `side_effect=NoAuthorizationError('missing')`
  (imported from `flask_jwt_extended.exceptions`).
- With a *successful* JWT patch, `/` responds 302 to
  `/source`.
- db functions called per view (patch targets are
  `kerbside.api.db.<fn>`, matching `test_api.py`):
  - `/console` → `db.get_consoles()` (the HTML branch
    passes no arguments and the templates use the
    embedded `audit` list).
  - `/console/<source>/<uuid>/audit` →
    `db.get_console(source, uuid)`,
    `db.count_audit_events(source, uuid)`,
    `db.get_audit_events(source, uuid, limit=20)`.
  - `/session` → `db.get_sessions()`.
  - `/source` → `db.get_sources()`.
  - Terminate views: exactly the db functions
    `test_api.py`'s `TerminateApiTestCase` already patches.
- Fixture shapes (fields the templates actually render):

  ```python
  CONSOLE = {
      'name': 'testvm', 'source': 'sf1', 'uuid': 'u-1234',
      'hypervisor': 'hv1', 'hypervisor_ip': '192.0.2.1',
      'insecure_port': 5900, 'secure_port': 5901,
      'token_count': 2, 'sessions': ['sess-1'],
      'audit': [{'timestamp': NOW, 'session_id': 'sess-1',
                 'channel': 'main',
                 'message': 'audit-marker-event'}],
      'ticket': 'sekrit-hypervisor-ticket',
  }
  SESSIONS = {'sess-1': {
      'name': 'testvm', 'source': 'sf1',
      'channels': [{'node': 'node1', 'pid': 123,
                    'created': NOW,
                    'client_ip': '198.51.100.7',
                    'client_port': 40000,
                    'connection_id': 77,
                    'channel_type': 'main'}],
  }}
  SOURCE = {
      'name': 'sf1', 'type': 'shakenfist',
      'last_seen': NOW, 'seen_by': 'node1',
      'errored': False, 'ca_cert': 'CA-CERT-MARKER',
      'password': 'sekrit-source-password',
  }
  AUDIT_EVENT = {
      'timestamp': NOW, 'session_id': 'sess-1',
      'channel': 'main', 'node': 'node1', 'pid': 123,
      'message': 'audit-marker-event',
  }
  ```

  where `NOW` is a `datetime.datetime`. Templates access
  fields with attribute syntax (`console.name`), which
  Jinja resolves for plain dicts — plain dicts are fine.
- The tests to write, all asserting status 200 (or the
  named redirect), `text/html` in `resp.content_type`, and
  the markers in `resp.get_data(as_text=True)`:
  1. Login: unauthenticated `GET /` → 200, contains
     `Username` and `Password` (the form labels; keep
     these two words when the login page converts in
     phase 4).
  2. Authenticated `GET /` → 302, `Location` ends with
     `/source`.
  3. Consoles: 200, contains `testvm`, and does **not**
     contain `sekrit-hypervisor-ticket`.
  4. Sessions: 200, contains `sess-1` and `testvm`.
  5. Sources: 200, contains `sf1` and `CA-CERT-MARKER`,
     and does **not** contain `sekrit-source-password`.
  6. Audit (`GET /console/sf1/u-1234/audit`): 200,
     contains `audit-marker-event` and `testvm`
     (`count_audit_events` returning 42 — the value is
     currently unrendered; do not assert on it).
  7. Terminate redirects: `GET
     /console/src/u/terminate` and `GET
     /session/sess-2/terminate` with `Accept: text/html`
     and the same db patches as `TerminateApiTestCase` →
     302 to `/console` and `/session` respectively.
     **Note in a comment that phase 8 deliberately
     changes these two tests when the routes move to
     POST.**
- Style: match `test_api.py` — testtools, mock decorators
  in reverse-argument order, single-quoted strings,
  docstring explaining the class's purpose, and note the
  marker-not-markup principle in that docstring so future
  editors keep the tests conversion-stable. `tox -eflake8`
  must pass.

## Execution

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 1a   | medium | sonnet | none      | Create `kerbside/tests/unit/test_api_html.py` implementing exactly the tests specified in "Key facts front-loaded for the sub-agent" in `docs/plans/PLAN-sfui-conversion-phase-01-smoke-tests.md`, following the idioms of `kerbside/tests/unit/test_api.py` (read both files first). Two test classes: one for the unauthenticated login page, one (with the JWT patch in `setUp`) for the authenticated pages and redirects. Then run `tox -epy3` and `tox -eflake8` and fix any failures in the new file. |

One step: the plan already front-loads all research, the
file is self-contained, and the sub-agent both writes and
proves it. The management session then reviews per the
master plan checklist (read the file, confirm no unrelated
changes, re-run tox) and commits.

## Success criteria

* `tox -epy3` passes with the new tests included (existing
  49-ish tests plus 8 new ones), and `tox -eflake8` is
  clean.
* Every HTML template is rendered by at least one test.
* The `ticket` and `password` leak guards are present and
  passing.
* The two terminate-redirect tests carry the phase 8
  change note.
* No production code changed — this phase touches exactly
  one new test file.

## Outcome

Complete. `kerbside/tests/unit/test_api_html.py` implements all
eight tests as specified, in two classes: `LoginPageTestCase`
(unauthenticated, no JWT patch) and `HtmlPagesTestCase` (JWT
patched in `setUp`). Fixtures are module-level constants, deep
copied per mock return so no test can mutate another's data.
`tox -epy3` runs 110 tests green (8 new, 102 pre-existing), and
flake8 is clean.

Three notes from the implementation and review:

* The plan's predictions about application behaviour all held —
  no discrepancies between what it said each view calls and what
  `api.py` does, and no production code needed to change.
* Patch targets were made consistently `kerbside.api.db.<fn>`
  across the file during review. `test_api.py` uses both that
  form and `kerbside.db.<fn>`; they patch the same attribute on
  the same module object (`api.py` does `from kerbside import
  db`), but one form per file reads better.
* The `password` leak guard was verified by mutation rather than
  trusted: adding `{{ source.password }}` to `sources.html`
  failed exactly `test_sources_page_renders_and_hides_password`
  and nothing else, then the template was reverted. The guard
  catches a real leak rather than passing vacuously, which
  matters because later phases rewrite that template.

The two terminate-redirect tests carry `NOTE(phase 8)` comments
recording that they change when those routes move to POST.

## Back brief

Before executing this phase, back brief the operator on
the intended work and how it aligns with this plan and the
master plan.
