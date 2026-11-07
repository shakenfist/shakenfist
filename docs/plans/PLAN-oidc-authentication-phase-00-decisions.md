# Phase 0: Research and decisions for OIDC authentication

## Context

This is phase 0 of
[`PLAN-oidc-authentication.md`](PLAN-oidc-authentication.md), and
it is the first phase of that plan to be cut. It is a **decisions
phase: no production code changes.** Its output is documentation
— the audit and probe results recorded below, and committed
answers written onto the master plan's open questions.

The master plan cannot proceed without it. What phases 3 to 9
*are* depends on how open question 13 goes: whether a human
session is a direct-bearer presentation of the IdP's own token,
or an exchange of that token for an ordinary namespace key. And
open question 14 — whether one credential may name more than one
namespace — decides whether phase 4 is a mechanical rename with
a handful of hand-written exceptions or a redesign of every
authorisation check in the codebase.

Phases 1 and 2 of the master plan were superseded: they were
built as `PLAN-auth-federation.md` phase 3 and their `Merged`
cells already name the merges that landed them. Those three
merges were re-verified as merge commits and as ancestors of
`develop` while planning this phase, so there is no previous
phase of this plan to close out and the planning commit carries
no close-out.

**Planning effort:** high. The master plan's Agent guidance
reserves high effort for phases turning on design decisions and
subtle correctness questions, and this phase is nothing but
those.

## Scope

**In scope.** Settling five of the master plan's open questions
and recording the evidence that settled them:

* **Open question 13** — direct-bearer versus exchange-based
  human sessions. The central question.
* **Open question 14** — multi-namespace authorisation,
  including where the line sits between a multi-namespace
  credential and the namespace trust mechanism that already
  exists.
* **Open question 5** — whether `caller_is_admin` can drop its
  `system`-namespace half now that a `cluster-admin` scope
  exists. The master plan says this should be taken alongside 13
  and it is, here.
* **Open question 7** — nonce versus bounded-delay revocation.
  This is no longer an independent decision; it is a consequence
  of 13 and is recorded as one.
* **Open question 11** — whether namespace keys are renamed to
  service-account credentials. Cheap, and it decides whether
  phase 6 exists at all, so it is answered before phase 6 is
  cut rather than at the time.

Plus the housekeeping that the survey below showed this plan
needs: correcting the master plan's stale file references and
its dead `CLAUDE.md` pointer, and filing the deferred work the
survey turned up.

**Out of scope.**

* Any production code change. Nothing under `shakenfist/` is
  committed by this phase. The feasibility probe of step 0b is
  deliberately thrown away; see D4.
* Open questions 1, 2, 3, 4 and 6, which are already answered or
  subsumed on the master plan.
* Open question 8 (inter-node authentication), which the master
  plan argues should be left open on purpose, and this phase
  does not overturn that.
* Open questions 9, 10 and 12 — CLI flow choice, client-side
  credential cache, documentation surface. The first two are
  `client-python`'s and are blocked on phase 3's discovery work;
  the third is phase 8's own planning problem once 13 and 14 are
  settled.
* Deciding the *content* of phases 3 to 9. Phase 0 may delete or
  rewrite an Execution row that its answers make wrong, and must
  say so in its commit message, but it does not plan those
  phases.
* Fixing the two defects the survey found. They are filed as
  issues by step 0f and left for their own work; see D5.

## What the survey found

Surveyed at `32de6cec7f`. **Every substantive claim the master
plan makes about the tree still holds.** Nothing in its premises
has been invalidated by the three months since
`PLAN-auth-federation-phase-05-oidc-plan-refresh.md` brought it
current, and the two numbers its argument leans on hardest are
exactly right: `request_namespace()` has 57 call sites outside
the tests and `shakenfist/external_api/artifact.py` holds 17 of
them.

```sh
# 58 lines; one of them is the definition itself. The .tox exclusion
# matters -- without it a plain grep(1) also finds the two installed
# copies of the package under .tox/ and returns 172.
grep -rn 'request_namespace()' --include='*.py' . |
    grep -v '/tests/' | grep -v '/\.tox/'
```

Nine things are nevertheless worth recording, and the last is
the important one.

### 1. Almost every file reference has drifted

The master plan carries 39 distinct `file:line` references, cited
about 70 times. Roughly three quarters no longer point at what
they claim. `shakenfist/external_api/base.py` has grown by more
than a thousand lines and all five of its references are wrong:

| Cited as | Actually at | Out by |
|---|---|---|
| `base.py:128-148` (`caller_is_admin`) | 279-299 | +151 |
| `base.py:140-145` (the `satisfies` test) | 291-292 | +151 |
| `base.py:1234` (`_enforce_scope`) | 2458 | +1,224 |
| `base.py:1291-1298` (`method_decorators`) | 2637 | +1,346 |
| `access_tokens.py:76` (`request_namespace`) | 83 | +7 |
| `namespace.py:386-410` (`_service_key_*`) | ~509 | +123 |
| `namespace.py:413` (`namespace_is_trusted`) | 521 | +108 |
| `app.py:383` (`/auth/federated` route) | 645 | +262 |
| `auth.py:1258` (its handler) | 1887 | +629 |
| `pyproject.toml:157-158` (`sf-ctl`, `sf-backup`) | 190-191 | +33 |

`shakenfist/federation.py`, `shakenfist/trusted_issuer.py`,
`shakenfist/mapping_rule.py` and
`shakenfist/schema/trusted_issuer_attributes.py` are the
exceptions and still resolve. This loop prints what each
reference currently lands on, 39 lines of output, and is the
check both step 0e and the Definition of done use — call it the
**reference verifier**:

```sh
grep -o '`[a-zA-Z0-9_/.]*\.\(py\|md\|toml\):[0-9]*\(-[0-9]*\)\?`' \
        docs/plans/PLAN-oidc-authentication.md |
    tr -d '`' | sort -u |
    while IFS=: read -r f l; do
        printf '%-52s %s\n' "$f:$l" "$(sed -n "${l%%-*}p" "$f" | cut -c1-60)"
    done
```
 Step 0e sweeps the rest; the
planning commit fixes only the Execution section's own
references and the index row, so that a reader of *this* phase
is not misled (D8).

### 2. `CLAUDE.md` no longer exists

The master plan's Prompt section says to consult `CLAUDE.md` for
build commands and conventions. It was folded into `AGENTS.md`
and `docs/` by `2f443906ff`. Corrected in the planning commit.

### 3. The missing-`scopes` wildcard default has three
enforcement sites, not two

The master plan names two places where `api_scopes.satisfies()`
treats a `None` scope list as a wildcard: `caller_is_admin` and
`_enforce_scope`. There is a third, added since the refresh, and
it runs in the opposite direction:

| Site | What it does |
|---|---|
| `base.py:292` | `caller_is_admin` — grants administrative access |
| `base.py:2464` | `_enforce_scope` — grants the derived scope |
| `auth.py:1109-1118` | caps what scopes a *mapping rule may grant* |

The third is a narrowing check: a caller holding `None` may
author a rule granting anything, because `None` means "legacy
operator, unrestricted". An issuer-dependent missing-claim
default has to answer this site too, and the answer is not
simply "deny" — denying there would stop an IdP-authenticated
administrator authoring any rule at all. Step 0b's brief names
this explicitly, because it is the kind of site a probe that
only checks "does the request authenticate" will miss.

### 4. A foreign token cannot reach `verify_token`'s body

`verify_token` (`base.py:1422`) opens with
`verify_jwt_in_request(...)` at `base.py:1425` —
`flask_jwt_extended`, validating against the cluster's own
secret. An IdP-issued token fails there, before any Shaken Fist
code runs. And everything downstream of it —
`parse_jwt_identity()`, `request_namespace()`, `caller_scopes()`,
`_enforce_scope` — reads `flask_jwt_extended`'s request context
through `get_jwt()` and `get_jwt_identity()`.

So the master plan's claim that discriminating between token
shapes is "a change to one function rather than to a decorator
stack" is true, but narrower than it sounds: the change is a
split *ahead of* the `flask_jwt_extended` call, and whether
direct-bearer can live "inside the single existing enforcement
path" reduces to one concrete question — can an
externally-validated token populate that request context, or
must every downstream reader grow a second path? That is the
whole of what step 0b has to find out, which is why the probe is
cheap and tightly scoped (D4).

### 5. Discovery is genuinely absent

Confirmed: nothing outside `docs/` fetches
`.well-known/openid-configuration`, and the only
`.well-known` strings in the tree are JWKS URLs in tests and
documentation examples. Phase 3 remains entirely unbuilt, as the
master plan says.

### 6. The `_service_key` nonce bypass is still live, and now bounded

`base.py:1449-1456` skips the nonce check entirely for a token
whose key name is exactly `_service_key`, with a comment
deferring its retirement to
`PLAN-auth-federation-phase-02-key-objects.md` — a phase which
is `Complete`. It is bounded rather than open: `_validate_key_name`
(`auth.py:117-133`) and `credentials.is_reserved_key_name()` now
reject that name on both the namespace-create and key-create
paths, so no new key can occupy it and only a token minted years
ago could exercise the bypass. Retirable, and out of scope here.
Filed by step 0f.

### 7. Issue #3672 is still live and is a phase 6 prerequisite

`AuthNamespaceKeysEndpoint.get` (`auth.py:591-595`) returns a
list of key *names*, read from `Namespace.keys`'s legacy
`nonced_keys` compatibility view. `NamespaceKey.external_view()`
(`namespace_key.py:404-418`) exists and already exposes
`expiry`, `scopes` and `provenance` — nothing serves it. So a
key's expiry, scopes and provenance remain unreadable through
any API, which matters here because phase 6's whole subject is
presenting keys to operators as service-account credentials.
Recorded on the master plan as a phase 6 prerequisite by step 0d.

### 8. "Claim" now means two things in this codebase

`docs/glossary.md:61-67` gained **capacity claim** — a
`NamespaceClaim` (`namespace_claim.py:95`) reserving cluster
capacity for a namespace — and explicitly warns against
confusing it with a mapping rule's bound claims. The glossary
has done the disambiguation work; what remains is discipline.
Every answer this phase writes says "bound claims" or "identity
claims" and never a bare "claims".

Relatedly, `app.py:456-466` advertises `trusted-issuers`,
`mapping-rules` and `federated-exchange` as client-visible
capabilities. Whatever human-login surface the master plan
settles on will need one too — a constraint on later phases,
recorded so nobody rediscovers it.

### 9. The population open question 13 wants to sample does not
exist — and the multi-namespace problem is already live anyway

Open question 13 asks for "the real multi-namespace distribution
— how many namespaces the people at a handful of real
deployments are actually in", and says that if the answer is
usually one then exchange wins outright. That measurement was
taken at planning time against sfcbr, the only real deployment
there is. Sampled on 2026-11-07, every namespace's non-service
key names:

| Namespace | Keys (excluding `_service_key_*`) |
|---|---|
| `system` | `roberto`, `deploy` |
| `ci-images` | `conductor` |
| `sfcbr-*` × 12 | `conductor` in each; one also holds `conductor-teardown` |
| `static-ci`, `static-runners` | none |

Sixteen namespaces when sampled, twelve of them ephemeral
per-CI-job `sfcbr-*` whose count moves with CI load — two
listings minutes apart gave seventeen and sixteen. Step 0c
resampled an hour later and found eleven namespaces, seven of
them `sfcbr-*`, plus a new `docker-workers-*` holding no keys,
with `conductor-teardown` gone along with its job namespace. The
shape was identical every time; the counts are a snapshot of CI
load, so it is the per-namespace invariant below that carries
the argument rather than any number.

**On the question as asked, the answer is that the population
does not exist.** Exactly one key in the cluster is held by a
person — `roberto`, in `system` — so the distribution is a
sample of size one, drawn from a cluster that has no staff
because it is a CI cloud. That does not answer open question 13.
It removes a piece of evidence the master plan expected to be
decisive, which is a different and more awkward result. D2 and
D3 say what replaces it.

**But the shape the question was trying to detect is already in
production, for a machine.** The CI conductor is one actor
holding a separate credential in every namespace it works in,
all of them named `conductor`, because a namespace key can name
exactly one namespace — fourteen of them in one sample and eight
an hour later, so it is the invariant and not the count that
carries the argument. That is precisely the cost open
question 13 attributes to exchange — "five exchanges and a
client that knows which credential to present for which
request" — being paid today, once per live job namespace. And `ci-images` takes
the other route for the same need: it lists every live job
namespace in its trust list so that jobs can read shared images
without holding a second credential at all.

So both mechanisms open question 14 says have never been
reconciled are live side by side in the one deployment we have,
serving one need, chosen differently in two places by the same
operator. That is not a hypothetical boundary. D7 makes drawing
it a required part of answering question 14, and it means the
audit in step 0a has a worked example to check its conclusions
against rather than only the code.

One caveat on reading this as support for exchange: a machine
that mints a credential per namespace at provisioning time is
not the same problem as a person who must hold one per namespace
in a credential cache and choose between them per request. The
conductor's keys are created by automation that already knows
which namespace it is working in. A human's would not be.

Corrections already applied at source, so no later step repeats
them: the master plan's Prompt section (finding 2), its
Execution section's own file references (finding 1), and the
`docs/plans/index.md` row.

## Decisions

**D1 — This phase ships no production code.** The deliverable is
the master plan's committed answers plus this plan's appendices.
This follows the repository's established shape for a decisions
phase; `PLAN-api-input-validation-phase-00-decisions.md` is the
worked precedent, including the convention that measurements
live in the phase plan and decisions live on the master plan.

**D2 — Open question 13's first piece of evidence is
unobtainable, and is replaced rather than faked.** The survey
measured it and found a population of one human in one
namespace, in the only deployment that exists. The honest
reading is that sfcbr cannot discriminate between the two
options, not that it votes for exchange.

A reviewer could reasonably object that this is the wrong
population: Shaken Fist's intended deployment is a corporate
cloud with staff, and the absence of humans from a CI cluster is
an artefact of what sfcbr is for. That objection is correct, and
it is exactly why the measurement is recorded as a *removed*
piece of evidence rather than as an answer. What it rules out is
the master plan's hope that a survey would settle the question
cheaply. What it does not do is license guessing at a
distribution we have never observed.

**D3 — The decision therefore rests on the two things that *are*
obtainable: the call-site audit and the feasibility probe.** The
audit (0a) tells us what multi-namespace authorisation would
cost, and the probe (0b) tells us whether direct-bearer can be
expressed inside the single enforcement path. Both are
architectural facts about our own code rather than facts about
other people's deployments, and both are decisive in the sense
that a bad answer to either disqualifies an option. If the probe
shows direct-bearer cannot live inside the one path,
"authorisation lives in one place" — which the master plan's
Mission says it will not trade away — settles question 13
without needing the distribution at all.

**D4 — The probe is narrowly scoped and is not committed.**
Survey finding 4 reduced it to one question: can an
externally-validated token populate `flask_jwt_extended`'s
request context such that `parse_jwt_identity()`,
`caller_scopes()` and `_enforce_scope` read it unchanged? The
probe answers that and the three-site question from survey
finding 3, and nothing else. It runs in a discarded worktree.

Not committed, because a decisions phase that lands production
code has to either maintain it or revert it, and a spike branch
pushed to `origin` becomes something the hygiene scan complains
about for months. The cost of throwing it away is that the
finding must be falsifiable without the diff, so step 0b's brief
requires it to report the exact files and functions it changed
and a diffstat, in enough detail that the probe can be
re-created from the write-up.

**D5 — The two defects the survey found are filed, not fixed.**
The `_service_key` nonce bypass (finding 6) and the unreadable
key metadata of #3672 (finding 7) are both real, and neither is
this phase's subject. Fixing them here would make a decisions
phase into a code phase and would bury the decisions in a diff.
#3672 additionally gets recorded on the master plan as a phase 6
prerequisite, because phase 6 cannot present keys as
service-account credentials through an API that does not serve
their properties.

**D6 — The audit's enumeration is a recorded command; its
classification is recorded judgement.** Enumerating the call
sites is a one-line `grep` (quoted above), so committing a tool
to `tools/` would be heavier than the thing it does, and the
`grep` doubles as the drift check phase 4 will want. The
classification into "the caller's own namespace" / "any
namespace the caller holds" / "the namespace this object is in"
is a judgement per site and cannot be scripted, so every site
gets a row and a one-line justification in this plan's
appendix. The classification *is* the evidence, which is the
master plan's own framing, so it has to be reviewable rather
than summarised.

**D7 — Answering open question 14 requires drawing the line
against trust, not just counting call sites.** Survey finding 9
shows trust already serving the multi-namespace need in
production. An answer to 14 that does not say when an operator
should reach for trust and when for a multi-namespace credential
leaves two ways to express one intent and no guidance, which is
the failure open question 14 itself names. So the line is part
of the deliverable, and step 0d's brief requires it.

**D8 — Stale references are swept in a step, not in the
planning commit.** The planning commit corrects the Prompt
section's dead `CLAUDE.md` pointer, the Execution section's own
references, and the index row — the things a reader of this
phase plan will follow. The remaining ~30 wrong references in
the Open Questions section are a mechanical sweep of about 70
occurrences, which belongs in a sub-agent step rather than
inflating a planning diff. Step 0e does it and pins the result
to a named commit, so the next reader knows what the numbers
were true at.

## Step plan

Steps 0a, 0b and 0c are independent and can run concurrently.
Step 0d is gated by the back brief (see below). Steps 0e and 0f
are independent of all of it.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 0a | high | opus | none | **Classify every `request_namespace()` call site.** Enumerate with `grep -rn 'request_namespace()' --include='*.py' . \| grep -v '/tests/'` — 58 lines at `32de6cec7f`, of which one is the definition in `shakenfist/util/access_tokens.py:83`, leaving 57 sites. Distribution: `external_api/artifact.py` 17, `external_api/instance.py` 9, `external_api/base.py` 7, `external_api/network.py` 6, `external_api/label.py` 5, `external_api/auth.py` 4, `external_api/clusteroperation.py` 3, `external_api/util.py` 2, and one each in `external_api/blob.py`, `external_api/agentoperation.py`, `schema/object_reference.py`, `blob.py`. For each site, read enough of the enclosing function to decide which of exactly three things the comparison means: **(A)** the caller's own namespace, where the check is "is this mine"; **(B)** any namespace the caller holds, where the check would want to be a set membership test if a credential could name several; **(C)** the namespace the object being touched is in, where the string is being used as a value rather than as an identity. Add a fourth bucket **(D) other**, and for every row placed in it say what it actually is — a bucket that absorbs the awkward cases silently is worse than no bucket. Write the result as a markdown table appended to `docs/plans/PLAN-oidc-authentication-phase-00-decisions.md` under `## Execution notes

**2026-11-07 (AEDT).** Back brief taken. Michael authorised the
read-only sfcbr access step 0c needs, authorised filing both of
step 0f's issues, held step 0d at its gate as the plan requires,
and granted this session permission to push and open the pull
request.

Two departures from the step table, both to stop agents
clobbering each other in one worktree rather than for any reason
of substance:

* **Steps 0a, 0b, 0c, 0e and 0f ran concurrently.** The plan says
  0a, 0b and 0c are independent, which is true of their work but
  not of their output: 0a's and 0c's briefs both say to append to
  this file's `## Results`, and two agents appending to one file
  in one worktree lose each other's writes. So 0a, 0b and 0c
  wrote their sections to scratch files and the management
  session pasted them in. 0e was the only step permitted to edit
  the worktree; 0f edits nothing.
* **No sub-agent ran `pre-commit run --all-files`.** Its stash is
  shared across every worktree of this repository, so two
  concurrent runs can silently revert each other's work. The
  management session runs it once instead. The two fast plan
  checks were left with 0e.

**Three corrections the management session made to step output.**

1. Step 0e's brief was wrong where it said
   `shakenfist/federation.py:347-363` had "moved to around line
   403" -- a misread grep hit on an error-message string. The
   exact claim matching is `claim_matches()` at
   `shakenfist/federation.py:359-376`. Corrected in the brief as
   well as in the master plan, so the error does not outlive the
   step that found it.
2. Step 0e left `shakenfist/namespace.py:197-205` alone, judging
   that it still landed in the right docstring. It does, but the
   prose cites it for the code which *synthesises* the
   `{'nonced_keys': ...}` shape, and that is at 206-214; 197-205
   is the unrelated `SecretStr` paragraph. Both occurrences
   corrected.
3. Survey finding 9's count of conductor credentials was a
   snapshot that had already moved by the time 0c resampled --
   fourteen, then eight. The finding now leads with the
   per-namespace invariant and treats the number as a sample,
   because the invariant is what the argument rests on.

**The Definition of done's call-site count was wrong as written,
and step 0a caught it.** The enumeration command omitted
`.tox`, and `.tox/py3` and `.tox/shared` each hold an installed
copy of the package, so a plain `grep(1)` returned 171 rather
than 57. It only ever looked right because an interactive shell
here wraps `grep` in a `.gitignore`-aware helper. Both the
survey's command and the Definition of done's now exclude
`.tox`, and both were re-run under `/bin/grep` as well as the
wrapped one: 57 either way. This is the second check in this
plan that was wrong until it was run.

**Two defects found beyond the two step 0f was authorised to
file, neither filed.** Step 0c found a stale trust entry on
sfcbr -- `ci-images` trusts `sfcbr-VoFFMlcdPMTKjk34`, which does
not exist -- and the management session confirmed it across
three samples spanning an hour, during which every other
`sfcbr-*` trust entry churned completely while that one stayed.
`revoke_inbound_trust()`'s own docstring
(`shakenfist/namespace.py:407-435`) calls this condition a
latent privilege escalation. The fix which created that
function, `09e6dcdb56`, is forward-only with no backfill, and
revocation is wired into two delete paths only, so residue from
before 2026-09-12 survives. #2074 is closed and covered the
forward fix; no open issue covers the residue. Separately, step
0a observed that `shakenfist/external_api/instance.py:903` and
`:1021` build a label URL from `request_namespace()` while
resolving the artifact with `namespace=namespace`, so a `system`
caller creating an instance in a tenant namespace with
`base: label:foo` looks for `sf://label/system/foo` scoped to
the tenant. Both were verified against the tree by the
management session. Filing either is a decision for Michael,
since the authorisation covered two named issues.

## Results`, one row per site, and begin every row with the site in backticks so the Definition of done's row count can be taken mechanically: `` | `shakenfist/external_api/artifact.py:123` | ArtifactEndpoint.get | A | ...justification... | ``. The four columns are the site, the enclosing function or class, the bucket letter, and a one-line justification. Then write a summary: counts per bucket, and a plain statement of whether a multi-namespace credential is a mechanical change with hand-written exceptions or a redesign. Two sites need particular care and must be called out individually: `external_api/auth.py:101`, where `requires_namespace_ownership` passes `request_namespace()` to `namespace_is_trusted()` so the check is already "owner or trusted" rather than "owner", and `external_api/base.py:282` in `caller_is_admin`, where it is compared to the literal `'system'`. One thing is already checked so you do not have to: the JWT identity is minted as `f'{ns.uuid}:{keyname}'` (`access_tokens.py:54`), which *looks* like a uuid but is not — `Namespace.uuid` returns the namespace's name (`namespace.py:64-70`, created by `cls._db_create(name, {'uuid': name, ...})` at `namespace.py:164`), so the master plan's prose that the identity is `<namespace>:<keyname>` is correct and every one of these comparisons really is against a namespace name. Change no code. |
| 0b | xhigh | opus | worktree | **Probe whether direct-bearer can live inside the single enforcement path.** Answer one question: can a token validated by `shakenfist/federation.py:validate_token()` be made to flow through the existing authorisation path unchanged? The obstacle is that `verify_token` (`shakenfist/external_api/base.py:1422`) calls `verify_jwt_in_request(False, False, False, ['headers'], True)` at line 1425, which validates against the cluster's own secret via `flask_jwt_extended`, so a foreign token is rejected before any Shaken Fist code runs; and everything downstream — `parse_jwt_identity()` and `request_namespace()` (`shakenfist/util/access_tokens.py:74-84`), `caller_scopes()` (`base.py:302`), `_enforce_scope` (`base.py:2458`), `caller_is_admin` (`base.py:279`) — reads that library's request context through `get_jwt()` / `get_jwt_identity()`. Build the smallest thing that works: peek at the unverified `iss` the way `federation.py:236` already does, look it up against `TrustedIssuer`, validate through `federation.validate_token()`, and then try to populate the `flask_jwt_extended` request context so the downstream readers need no change. Report, with specifics: whether that population is possible at all with the installed `flask_jwt_extended`; if it is, how ugly it is and whether it depends on private API; if it is not, how many downstream readers would need a second path. **Then test the three missing-`scopes` sites specifically** — an IdP token carries no `scopes` claim, and `api_scopes.satisfies(None, anything)` returns `True` (`shakenfist/external_api/scopes.py:137-142`). Demonstrate empirically what such a token *gets* at `base.py:292` (`caller_is_admin`), `base.py:2464` (`_enforce_scope`) and `auth.py:1109-1118` (the cap on what scopes a mapping rule may grant). The third is a narrowing check where `None` means "legacy operator, unrestricted", so an issuer-dependent default of "deny for foreign tokens" has a non-obvious consequence there — say what it is. Do not fix anything, do not file an issue (the master plan says in as many words that the wildcard default is not a bug and must not be filed as one), and **do not commit**: the worktree is discarded. The write-up is the whole deliverable, so it must name every file and function touched and include a diffstat, in enough detail that the probe can be re-created from the text alone. Append it to the phase plan under `## Results`. |
| 0c | medium | sonnet | none | **Characterise the deployments we can actually see, and state plainly what cannot be measured.** The planning pass already measured sfcbr on 2026-11-07; survey finding 9 above has the table. Headlines: sixteen namespaces, twelve of them ephemeral per-CI-job `sfcbr-*` whose count moves with CI load; exactly one key in the cluster held by a person (`roberto`, in `system`); `deploy` in `system`; and a `conductor` key in `ci-images` and in every job namespace — one actor, fourteen credentials, because a key names one namespace. Confirm that with `sf-client namespace list` and `sf-client --json namespace list`, and `sf-client namespace show <ns>` per namespace (all read-only; credentials are already present in the environment) and report any difference, but do not re-derive the reading — that is written. What to add: how `ci-images`' trust list relates to the live job namespaces at the moment you sample (the planning pass saw a trusted namespace that no longer had a row, which is either churn or a stale trust entry, and which of those it is matters to open question 14's answer). Then add what the planning pass did not do: how many namespaces the repository's own deployment and CI tooling provisions a *person* into (search `shakenfist/deploy/`, `.github/workflows/`, and `shakenfist/deploy/shakenfist_ci/`), and what `docs/operator_guide/authentication.md` and `docs/user_guide/authentication.md` currently tell an operator to do about human credentials. Finish with one short honest paragraph: what open question 13's distribution evidence would need to come from, and why we do not have it. Do not extrapolate a distribution from a sample of one, and do not argue for either option — that is step 0d's job. Append to the phase plan under `## Results`. Change no code. |
| 0d | high | opus | none | **Write the committed answers onto the master plan.** Gated by the back brief; do not start before the management session has released it. Read steps 0a, 0b and 0c's results in `docs/plans/PLAN-oidc-authentication-phase-00-decisions.md`, then write answers into `docs/plans/PLAN-oidc-authentication.md` under open questions 13, 14, 5, 7 and 11, in the same style the already-answered questions use: a bold lead naming the resolution and the date, then the reasoning, then what it constrains downstream. Each answer commits to one option — no "likely", no "lean", no "to be decided in a later phase". Question 14's answer must include the line between namespace trust and a multi-namespace credential: when an operator reaches for each, and why (D7). Question 11's answer decides whether phase 6 exists; if it is "no", say so and strike the row rather than shrinking it, exactly as the master plan's phase 6 note instructs. Also record #3672 as a phase 6 prerequisite in that row's note (survey finding 7). Then update the Execution table: any row whose content the answers have changed, plus phase 0's own `Status`. Keep the `Status` cell to a single vocabulary term. Do not touch `docs/plans/order.yml` — phase files are not registered there. The index row's status and `N of M` are derived by `tools/check-plan-status.py`; run it. |
| 0e | low | sonnet | none | **Sweep the master plan's stale file references.** `docs/plans/PLAN-oidc-authentication.md` carries 39 distinct `` `file:line` `` references cited about 70 times, and roughly three quarters are wrong — `shakenfist/external_api/base.py` has grown by over a thousand lines since they were written. Re-resolve every one and correct it. Start by running the **reference verifier** from the "What the survey found" section above, which prints what each reference currently lands on; then find each symbol by name and write its real line numbers. Known-good and needing no change: `shakenfist/federation.py:39-51`, `:68` and `:120-210`; `shakenfist/trusted_issuer.py:41`; `shakenfist/mapping_rule.py:244`; and the three `shakenfist/schema/trusted_issuer_attributes.py` references. `shakenfist/federation.py:347-363` *is* wrong — the bound-claim matching it cites is `claim_matches()`, now at `shakenfist/federation.py:359-376`. Do not change the plan's referencing *style* to symbol names; this repository's plans cite `file:line`, and restyling a master plan is not this step's job. Add one sentence to the Execution section saying the references were re-resolved, naming the commit you did it at, so a later reader knows what they were true at. The planning commit already fixed the Prompt section's dead `CLAUDE.md` pointer and every reference cited in the Prompt or Execution sections — including, because the same strings recur, their copies in Open questions. Do not redo those; what is left is the references that appear only in Open questions. Change nothing outside `docs/plans/`. |
| 0f | low | sonnet | none | **File the two deferred-work issues.** One: the `_service_key` nonce-check bypass at `shakenfist/external_api/base.py:1449-1456`, which skips the nonce check entirely for a token whose key name is exactly `_service_key`. Its own comment defers retirement to `PLAN-auth-federation-phase-02-key-objects.md`, which is `Complete`. It is bounded, not open — `_validate_key_name` (`shakenfist/external_api/auth.py:117-133`) and `credentials.is_reserved_key_name()` reject that name on both the namespace-create and key-create paths, so no new key can occupy it and only a token minted before nonced keys existed could exercise it. Say that in the issue: the ask is to retire the special case, not to patch a live hole. Two: check whether issue #3672 ("Namespace key expiry, scopes and provenance are not readable through any API") needs a comment noting that it now blocks `PLAN-oidc-authentication.md` phase 6, and that `NamespaceKey.external_view()` (`shakenfist/namespace_key.py:404-418`) already produces the right shape while `AuthNamespaceKeysEndpoint.get` (`shakenfist/external_api/auth.py:591-595`) still returns bare names out of the legacy `nonced_keys` compatibility view. Do not open a duplicate of #3672. Before filing the first, search open and closed issues for `_service_key` — #3521 is closed and is about expired keys never being purged, which is a different thing. Add the `automated-fix-attempted` label to nothing; these are not being fixed here. Record both issue numbers in this plan's `## Results`. |

## Risks and mitigations

**The classification in 0a comes back inconsistent.** Three
buckets with fuzzy edges across 57 sites is exactly the shape
that produces a table nobody can act on. The brief fixes the
bucket definitions and demands a justification per row, and
names the two sites that are genuinely subtle. The management
session checks it by re-reading a sample of at least ten rows by
hand, including every row in `auth.py` and `base.py`, before
accepting the step — those are the authorisation-critical ones
and a wrong bucket there changes the answer to question 14.

**The probe in 0b "succeeds" and is over-read.** A probe that
gets a request authenticated proves the easy half. The expensive
half is what the token is then *allowed to do*, and with no
`scopes` claim the answer today is everything. The brief
therefore makes the three `satisfies()` sites a required part of
the deliverable rather than a follow-up, and the management
session rejects a write-up that reports authentication without
reporting authorisation at all three. If the probe reports
success at the first and silence at the second, that is a failed
step, not a positive result.

**0d writes an answer the evidence does not support.** The
temptation with question 13 is to pick exchange because three of
the four considerations favour it and the fourth —
multi-namespace — now has no evidence behind it. That would be
reasoning from an absence. The back brief gate exists for this:
the management session reads 0a, 0b and 0c and agrees the
*conclusion* with Michael before 0d writes a word of it.

**Phase 0 drifts into planning phases 3 to 9.** It touches their
Execution rows, which is one edit away from writing their
content. Mitigation is in the Definition of done: a row may
change only where an answer made it wrong, and each such change
is named in the commit message, so the diff is reviewable
against a stated list rather than scanned for surprises.

**0e introduces new wrong line numbers.** A mechanical sweep of
70 references across a 1,400-line document will have errors if
nothing checks it. The reference verifier re-resolves every
reference and prints the line it lands on, which turns the check
from a careful re-read into a scan of 39 lines of output.

## Definition of done

Each of these is checkable, and the commands are given where
there is one.

* [ ] Open questions 13, 14, 5, 7 and 11 each carry a dated
      answer on the master plan that names one option, and none
      of them hedges. Checked against a pinned baseline rather
      than by reading, because the hedge words occur
      legitimately elsewhere —
      `grep -ciE 'likely|\blean\b|probably|to be decided' docs/plans/PLAN-oidc-authentication.md`
      returns **8** at `32de6cec7f`, all of them in open
      questions 7, 8 and 9, which are deliberately left open, or
      in prose about a documentation reference. The count must
      not rise: a ninth hit means one of the five answers
      hedged.
* [ ] Question 14's answer states when an operator reaches for
      namespace trust and when for a multi-namespace credential.
* [ ] Every one of the 57 call sites appears exactly once in the
      Results appendix with a bucket and a justification. Both
      sides of that are counted, not eyeballed:

      ```sh
      # The .tox exclusion is not optional: .tox/py3 and .tox/shared
      # each hold an installed copy of the package, so without it a
      # plain grep(1) returns 171 rather than 57. Step 0a found this;
      # the figure of 58 in its own brief came from an interactive
      # shell whose grep is gitignore-aware, which is exactly the kind
      # of difference a check written and never run does not notice.
      sites=$(grep -rn 'request_namespace()' --include='*.py' . |
              grep -v '/tests/' | grep -v '/\.tox/' |
              grep -vc 'def request_namespace')
      rows=$(grep -cE '^\| `[a-z_/]+\.py:[0-9]+` \|' \
              docs/plans/PLAN-oidc-authentication-phase-00-decisions.md)
      echo "$sites sites, $rows rows"   # 57 and 57 at 32de6cec7f
      ```
* [ ] Every site in bucket **(D) other** has a sentence saying
      what it actually is.
* [ ] 0b's write-up names the files and functions it touched and
      includes a diffstat, and reports an outcome for all three
      `satisfies()` sites — `base.py:292`, `base.py:2464` and
      `auth.py:1109-1118`.
* [ ] Nothing from 0b is committed:
      `git diff --stat develop... -- ':!docs/plans'` is empty,
      and so is `git status --short` outside `docs/plans/`.
* [ ] Every `file:line` reference in the master plan resolves to
      something plausible, checked with the reference verifier
      above, and the Execution section names the commit they
      were re-resolved at.
* [ ] `grep -c 'CLAUDE.md' docs/plans/PLAN-oidc-authentication.md`
      returns 0.
* [ ] Phase 0's `Status` is a single vocabulary term, and no
      other phase's row changed except where an answer made it
      wrong — each such change named in the commit message.
* [ ] `docs/plans/order.yml` is unmodified:
      `git diff --exit-code develop... -- docs/plans/order.yml`.
* [ ] `python3 tools/check-plan-status.py` passes, and the index
      row's `N of M` still reads `0 of 11` (a `Superseded` phase
      does not count as done, and phase 0 is not `Complete`
      until this phase lands) — or the arithmetic the tool
      derives, if 0d struck a row.
* [ ] Two issue numbers recorded in Results, or a sentence
      saying why one was not needed.
* [ ] `pre-commit run --all-files` passes.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan and how the intended work aligns with
it.

**There is one gate, and it is before step 0d.** Steps 0a, 0b
and 0c gather evidence; step 0d turns it into answers that
determine what six later phases of the master plan *are*. That
is the definition of cheap to propose and expensive to redo: an
answer to question 13 written and committed, then reversed,
invalidates phase 3's premise, phase 7's entire repository
choice, and phase 8's worked examples. So when 0a, 0b and 0c are
complete, the management session states the conclusion it intends
0d to write — one paragraph per question, naming the option and
the reason — and waits for Michael before releasing 0d.

Steps 0e and 0f need no gate and may run at any time.

## Results

Filled in as the steps complete.

### Step 0a — classification of every `request_namespace()` call site

Audited at `6b0ab81b2a` (phase 0 planning commit, whose parent is
`32de6cec7f`); no file under `shakenfist/` differs from
`32de6cec7f`, so the line numbers below are the ones the plan's
brief cites.

**Enumeration.** The brief's command returns 58 lines, of which
one is the definition at
`shakenfist/util/access_tokens.py:83`, leaving 57 rows. Two of
those 57 are not calls at all — they are the string
`request_namespace()` appearing inside an explanatory comment —
and both are recorded in bucket **(D)** rather than quietly
dropped, so the table's 57 rows still reconcile with the
Definition of done's count. There are therefore **55 executable
call sites**.

**Counting caveat, worth knowing before the Definition of done
is run.** The brief's `grep` returns 58 only because the shell
this session runs in wraps `grep` in a `.gitignore`-aware
helper. Run with `/bin/grep`, the same command in this worktree
returns **174** lines, because `.tox/py3` and `.tox/shared`
each contain an installed copy of the package. The Definition of
done's `sites=` command is subject to the same inflation. Add
`| grep -v '/\.tox/'` to it, or run it in a tree with no `.tox`.

**Bucket definitions as applied.** The discriminator used
throughout is what the *other side* of the expression is:

* **(A)** the other side is a namespace **named by the request**
  (a body or path parameter), so the check is literally "is the
  namespace you asked for mine".
* **(B)** the other side is an **object's namespace, a trust
  relation, or the privileged literal `'system'`** — an
  entitlement question which becomes a set-membership test the
  moment a credential can name several namespaces.
* **(C)** there is **no comparison**: the string is consumed as
  a namespace-valued datum — the default target namespace of a
  create or a delete-all, a component of a label or upload URL,
  or the namespace a proxy token is minted into.
* **(D)** none of the above.

#### Bucket counts

| Bucket | Count | Of the 55 real call sites |
|---|---|---|
| A — the namespace the request named, tested against the caller's | 3 | 5% |
| B — an entitlement test that becomes set membership | 36 | 65% |
| C — the string used as a namespace *value* | 16 | 29% |
| D — not a call site | 2 | — |
| **Total rows** | **57** | |

#### Summary: mechanical, with a concentrated redesign in one corner

**It is a mechanical change, and the 57 sites do come out
lopsided — but the lopsidedness is not where open question 14
expected it, and the 16 sites in bucket (C) are not exceptions
that can be hand-written one at a time. They are one design
decision applied sixteen times.**

Taking the three parts in turn:

**A and B together are 39 of the 55 real sites (71%), and they
are the same edit.** Every one of them is an entitlement test
whose single-string comparison becomes a set-membership test,
and they are not 39 distinct edits: they are five families, four
of which are already funnelled through a shared helper or a
shared idiom.

| Family | Sites | What changes |
|---|---|---|
| `request_namespace() not in [obj.namespace, 'system']` | 11 | one idiom, repeated verbatim; mostly inside `requires_*_ownership` decorators |
| `namespace_is_trusted(x, request_namespace())` | 9 | the set test moves *inside* `namespace_is_trusted()`; call sites unchanged |
| listing filters (`namespace_filter`, `namespace_or_shared_filter`) | 4 | the set test moves inside the two filter functions; call sites unchanged |
| comparison against the literal `'system'` only | 8 | becomes "does the caller hold `system`" |
| `clusteroperation.py`'s `caller_namespace` + `_why_hidden` | 3 | one local helper |
| request-named namespace tested against the caller's (bucket A) | 3 | `namespace != request_namespace()` becomes `namespace not in held` |
| visibility-scoped `Artifact.from_url(namespace=...)` | 1 | follows the filter change |

So the authorisation *tests* really are a rename plus three
helper signatures. The master plan's framing — "a change to the
shape of every authorisation check in the codebase" — overstates
it, because the checks are not 39 independent shapes.

**Bucket C is where the work is, and it is a design decision, not
a sweep.** At these 16 sites `request_namespace()` is not asking
a question; it is *answering* one — "which namespace does this
object go in", "whose label is this", "which namespace do I mint
a proxy token for". A credential naming several namespaces has
no answer, so every one of these sites needs the namespace to
arrive from somewhere else. They decompose into exactly three
decisions:

1. **Eight sites default a target namespace** when the request
   did not name one (`if not namespace: namespace =
   request_namespace()`, and the `else:` arms of the three
   delete-all routes, plus `base.py:330`). One decision: either
   a multi-namespace caller must name its target namespace
   explicitly on these routes, or a credential carries a
   designated default. This is open question 14's own
   sub-question "does a request name its target namespace
   explicitly" — and these eight sites are its entire blast
   radius.
2. **Four sites build a URL** out of the caller's namespace
   (`label.py:45`, `instance.py:903`, `instance.py:1021` and
   `artifact.py:624`). These follow decision 1: once the target
   namespace is known, the URL is built from it.
3. **Four sites mint a node-to-node proxy token**
   (`base.py:1765`, `base.py:1994`,
   `external_api/blob.py:62`, `artifact.py:605`), all via
   `get_api_token(..., namespace=request_namespace())`, which
   creates a transient `_service_key_*` **in a single
   namespace** (`namespace.py:494-519`). **This is the one
   thing in the audit that genuinely does not have a mechanical
   answer**, and the plan does not mention it: a multi-namespace
   request that crosses a proxy hop is re-authenticated as a
   single namespace on the far side, so it silently loses
   entitlements mid-request. Either token minting grows
   multi-namespace support, or the proxy must resolve the
   request to one namespace before forwarding. See "Not
   anticipated" below.

**Verdict.** Phase 4 is a mechanical change — three helper
signatures, one repeated idiom, and three request-shape
decisions applied to sixteen value sites — not a redesign of the
authorisation model. The redesign risk is concentrated in one
place the plan had not identified: the four proxy-token sites,
where the single-namespace credential is re-minted rather than
re-used.

#### The two sites the brief singles out

**`shakenfist/external_api/auth.py:101`
(`requires_namespace_ownership`) — bucket B, and the brief's
reading is right: the decorator's name is wrong.** It calls
`namespace_is_trusted(ns, request_namespace())`, which returns
`True` when `namespace == requestor` *or* when the requestor
appears in the target's `trust` list
(`shakenfist/namespace.py:521-532`), so the gate is already "owner or
trusted", not "owner". Every route it guards —
including `AuthNamespaceTrustsEndpoint.post` and
`AuthNamespaceTrustEndpoint.delete`, which re-run the identical
check at `auth.py:803` and `:832` — is therefore reachable by a
trusted namespace. That matters to open question 14 beyond this
row: **trust already composes transitively into the trust
administration surface itself**, so a namespace granted trust
can grant trust onward. This is the sharp end of D7's "where
does the line sit", and it is an argument that trust is already
doing more than the "visibility mechanism" the artifact
decorators' comments describe it as (`artifact.py:150-173`
says in as many words that trust must not reach past reading).
Mechanically the row is cheap — the set test goes inside
`namespace_is_trusted()` and all nine of its call sites stay as
they are — so it is not phase 4 work. It is phase 0 evidence.

**`shakenfist/external_api/base.py:282` (`caller_is_admin`) —
bucket B, and the comparison against the literal `'system'` is
the easy half; the hard half is a new escalation it creates.**
Today `request_namespace() != 'system'` is unambiguous: a
credential names one namespace and either it is `system` or it
is not. Under a multi-namespace credential the test becomes
"does the held set contain `system`", and a person who holds
both `system` and a tenant namespace then satisfies
`caller_is_admin` **on every request, including requests that
only touch the tenant namespace**. Combined with the `scopes`
wildcard that step 0b is probing, that is administrative
authority acquired as a side effect of being in two namespaces
at once — a shape that cannot exist today. Eleven other sites
share it: the seven other pure-`'system'` comparisons
(`blob.py:299`, `network.py:388`, `instance.py:1323`,
`artifact.py:497`, `:533`, `:666`,
`schema/object_reference.py:182`), the three `clusteroperation`
`is_admin` derivations, and `base.py:330`. Two of those eight —
`blob.py:299` and `object_reference.py:182` — are field-level
disclosure inside `external_view()`, so they would start leaking
blob locations and node references into *every* object a
dual-namespace caller rendered. The conclusion for 0d is that a
multi-namespace credential cannot keep "holds `system`" as the
spelling of "is an administrator"; the privilege has to come
from the `cluster-admin` scope alone, which is precisely open
question 5's subject. **Open question 14 cannot be answered
"yes" without also answering 5 "drop the namespace half".**

#### Not anticipated by the plan

1. **The proxy-token path re-mints a single-namespace
   credential** (bucket C, decision 3 above). Four sites. The
   plan's premise that `request_namespace()` is read-only on the
   request is not quite true: at these four it is *written back*
   into a freshly minted token for the next hop.
2. **A multi-namespace credential turns `caller_is_admin` into
   an escalation**, which makes question 5 a prerequisite of
   question 14 rather than a companion to it (above).
3. **`namespace_is_trusted()` already gates trust
   administration**, so trust is transitive in a way the
   artifact code's comments say it must not be (above).
4. **`instance.py:903` and `instance.py:1021` look wrong
   today**, independent of OIDC. Both build a label URL from
   `request_namespace()` while resolving it with
   `namespace=namespace`, where `namespace` may be a *different*
   namespace the caller was authorised against at
   `instance.py:742`. A `system` caller creating an instance in
   a tenant namespace with `base: label:foo` therefore looks for
   `sf://label/system/foo` *inside* the tenant namespace and
   finds nothing. Recorded as an observation only — no issue
   filed, per this step's brief.
5. **`base.py:330` (`resolve_lookup_namespace`) is the one
   genuinely mixed site**: the same local is used as a
   `'system'` privilege test *and* returned as the lookup
   namespace, so it splits across B and C under multi-namespace.
   Filed as C because the returned value is its product.

#### The table

57 rows, one per line of the brief's enumeration other than the
definition.

| Site | Enclosing function or class | Bucket | Justification |
|---|---|---|---|
| `shakenfist/blob.py:299` | `Blob.external_view` | B | `== 'system'` gates disclosure of blob `locations`; becomes "holds system", and a dual-namespace caller would leak locations on every blob. |
| `shakenfist/schema/object_reference.py:182` | `references_to_grouped_dict` | B | `!= 'system'` suppresses Node-typed references; same privilege test, same field-level disclosure risk. |
| `shakenfist/external_api/auth.py:101` | `requires_namespace_ownership.wrapper` | B | Passes the caller into `namespace_is_trusted()`, so the gate is "owner or trusted"; set test moves inside that helper. Called out above. |
| `shakenfist/external_api/auth.py:357` | `AuthNamespacesEndpoint.get` | B | Per-namespace trust test driving a listing; a held set unions the visible namespaces. |
| `shakenfist/external_api/auth.py:803` | `AuthNamespaceTrustsEndpoint.post` | B | Same "owner or trusted" gate, on adding a trust; set test moves into the helper. |
| `shakenfist/external_api/auth.py:832` | `AuthNamespaceTrustEndpoint.delete` | B | Same gate, on removing a trust. |
| `shakenfist/external_api/base.py:282` | `caller_is_admin.wrapper` | B | `!= 'system'` is the administrative gate; becomes "holds system", which is an escalation. Called out above. |
| `shakenfist/external_api/base.py:321` | `resolve_lookup_namespace` | D | Not a call site: the string appears inside a comment explaining what the decorators used to pass in. |
| `shakenfist/external_api/base.py:330` | `resolve_lookup_namespace` | C | Produces the lookup namespace returned to the caller; also does a `!= 'system'` test, so this is the one site that splits across B and C. |
| `shakenfist/external_api/base.py:1765` | `redirect_instance_request.wrapper` | C | Minted into a proxy token via `get_api_token()`; a single-namespace `_service_key` cannot carry a held set. |
| `shakenfist/external_api/base.py:1783` | `requires_instance_ownership.wrapper` | B | `not in [i.namespace, 'system']` — the repeated ownership idiom. |
| `shakenfist/external_api/base.py:1872` | `requires_network_ownership.wrapper` | B | Same ownership idiom against the network's namespace. |
| `shakenfist/external_api/base.py:1994` | `redirect_upload_request.wrapper` | C | Proxy token for the upload's node; same minting problem as `base.py:1765`. |
| `shakenfist/external_api/blob.py:62` | `_read_remote` | C | Proxy token to read a blob from a remote node; same minting problem. |
| `shakenfist/external_api/util.py:66` | `safe_get_network_interface` | B | `not in [n.namespace, 'system']` against the interface's network. |
| `shakenfist/external_api/util.py:71` | `safe_get_network_interface` | B | Same idiom against the attached instance's namespace. |
| `shakenfist/external_api/agentoperation.py:46` | `requires_operation_ownership.wrapper` | B | Same ownership idiom against the agent operation's namespace. |
| `shakenfist/external_api/network.py:223` | `NetworksEndpoint.get` | B | `partial(namespace_filter, ...)` listing filter; the set test moves inside `baseobject.namespace_filter`. |
| `shakenfist/external_api/network.py:295` | `NetworksEndpoint.post` | C | Defaults the namespace the new network is created in when the body named none. |
| `shakenfist/external_api/network.py:298` | `NetworksEndpoint.post` | B | `not in [namespace, 'system']` authorises creating into a body-named namespace. |
| `shakenfist/external_api/network.py:388` | `NetworksEndpoint.delete` | B | `== 'system'` selects the admin branch of delete-all. |
| `shakenfist/external_api/network.py:396` | `NetworksEndpoint.delete` | A | `namespace != request_namespace()` — "is the namespace you named mine", on a request-supplied value. |
| `shakenfist/external_api/network.py:398` | `NetworksEndpoint.delete` | C | Defaults the namespace the delete-all loop iterates over. |
| `shakenfist/external_api/instance.py:328` | `_artifact_unusable_reason` | B | `namespace_is_trusted(a.namespace, ...)` decides artifact usability; set test moves into the helper. |
| `shakenfist/external_api/instance.py:617` | `InstancesEndpoint.get` | B | `partial(namespace_filter, ...)` listing filter. |
| `shakenfist/external_api/instance.py:739` | `InstancesEndpoint.post` | C | Defaults the namespace the new instance is created in. |
| `shakenfist/external_api/instance.py:742` | `InstancesEndpoint.post` | B | `namespace_is_trusted()` authorises creating into a body-named namespace. |
| `shakenfist/external_api/instance.py:903` | `InstancesEndpoint.post` | C | URL component for a `label:` disk base; also looks wrong today — see observation 4. |
| `shakenfist/external_api/instance.py:1021` | `InstancesEndpoint.post` | C | URL component for a `label:` NVRAM template; same wart as `:903`. |
| `shakenfist/external_api/instance.py:1323` | `InstancesEndpoint.delete` | B | `== 'system'` selects the admin branch of delete-all. |
| `shakenfist/external_api/instance.py:1331` | `InstancesEndpoint.delete` | A | `namespace != request_namespace()` against a body-named namespace. |
| `shakenfist/external_api/instance.py:1333` | `InstancesEndpoint.delete` | C | Defaults the namespace the delete-all loop iterates over. |
| `shakenfist/external_api/clusteroperation.py:175` | `ClusterOperationEndpoint.get` | B | Local `caller_namespace` used for a `!= 'system'` test and then compared against every target namespace in `_why_hidden()`. |
| `shakenfist/external_api/clusteroperation.py:226` | `ClusterOperationChainEndpoint.get` | B | Same local: derives `is_admin` and compares chain members' namespaces to it. |
| `shakenfist/external_api/clusteroperation.py:403` | `ClusterOperationsEndpoint.get` | B | Same local: derives `is_admin` and compares the target object's namespace to it. |
| `shakenfist/external_api/label.py:45` | `_label_url` | C | Supplies the namespace half of an unqualified label name, and goes into the label URL. |
| `shakenfist/external_api/label.py:145` | `LabelEndpoint.post` | B | `not in [a.namespace, 'system']` authorises pushing a version into an existing label. |
| `shakenfist/external_api/label.py:148` | `LabelEndpoint.post` | B | `namespace_is_trusted()` authorises creating a brand new label in a named namespace. |
| `shakenfist/external_api/label.py:185` | `LabelEndpoint.get` | B | `Artifact.from_url(namespace=...)` resolves by *visibility* through `namespace_or_shared_filter`, so this is an entitlement test wearing a parameter's clothes. |
| `shakenfist/external_api/label.py:213` | `LabelEndpoint.delete` | B | `not in [a.namespace, 'system']` on the mutation path. |
| `shakenfist/external_api/artifact.py:162` | `requires_artifact_ownership` | D | Not a call site: the string appears inside the comment arguing that trust must not reach past reading. |
| `shakenfist/external_api/artifact.py:177` | `requires_artifact_ownership.wrapper` | B | `not in [a.namespace, 'system']` — the ownership idiom. |
| `shakenfist/external_api/artifact.py:220` | `requires_artifact_access.wrapper` | B | `namespace_or_shared_filter()` — the same predicate the listing uses; set test moves inside it. |
| `shakenfist/external_api/artifact.py:419` | `ArtifactsEndpoint.get` | B | `partial(namespace_or_shared_filter, ...)` listing filter. |
| `shakenfist/external_api/artifact.py:466` | `ArtifactsEndpoint.post` | C | Defaults the namespace the fetched image artifact is created in. |
| `shakenfist/external_api/artifact.py:468` | `ArtifactsEndpoint.post` | B | `namespace_is_trusted()` authorises acting on a body-named namespace. |
| `shakenfist/external_api/artifact.py:488` | `ArtifactsEndpoint.post` | B | `not in [a.namespace, 'system']` for replacing what an existing artifact resolves to. |
| `shakenfist/external_api/artifact.py:497` | `ArtifactsEndpoint.post` | B | `!= 'system'` restricts creating a `shared` artifact; privilege test against the literal. |
| `shakenfist/external_api/artifact.py:533` | `ArtifactsEndpoint.delete` | B | `== 'system'` selects the admin branch of delete-all. |
| `shakenfist/external_api/artifact.py:541` | `ArtifactsEndpoint.delete` | A | `namespace != request_namespace()` against a body-named namespace. |
| `shakenfist/external_api/artifact.py:543` | `ArtifactsEndpoint.delete` | C | Defaults the namespace the delete-all loop iterates over. |
| `shakenfist/external_api/artifact.py:605` | `ArtifactUploadEndpoint.post` | C | Proxy token to the node holding the upload; same minting problem as `base.py:1765`. |
| `shakenfist/external_api/artifact.py:624` | `ArtifactUploadEndpoint.post` | C | Namespace component of the synthesised `sf://upload/...` source URL. |
| `shakenfist/external_api/artifact.py:627` | `ArtifactUploadEndpoint.post` | C | Defaults the namespace the uploaded artifact is created in. |
| `shakenfist/external_api/artifact.py:651` | `ArtifactUploadEndpoint.post` | B | `not in [a.namespace, 'system']` for pushing a version into an owned artifact. |
| `shakenfist/external_api/artifact.py:654` | `ArtifactUploadEndpoint.post` | B | `namespace_is_trusted()` authorises creating a new artifact in a named namespace. |
| `shakenfist/external_api/artifact.py:666` | `ArtifactUploadEndpoint.post` | B | `!= 'system'` restricts creating a `shared` artifact. |

One thing checked so later readers need not: the JWT identity is
minted as `f'{ns.uuid}:{keyname}'`
(`shakenfist/util/access_tokens.py:54`) and `Namespace.uuid`
returns the namespace's *name*
(`shakenfist/namespace.py:64-70`, created by
`cls._db_create(name, {'uuid': name, ...})` at
`shakenfist/namespace.py:164`), so every comparison above really
is against a namespace name. Re-confirmed; the master plan's
prose is correct.

### Step 0b — Feasibility probe: can direct-bearer live inside the single enforcement path?

Probed at `32de6cec7f` in a discarded worktree. Nothing committed; the
diff below is the whole of what was changed and is thrown away. Every
claim here is from a passing test in that worktree, not from reading.

**Answer in one line.** Yes, the request context can be populated so
that `parse_jwt_identity()`, `request_namespace()`, `caller_scopes()`,
`_enforce_scope` and `caller_is_admin` read a foreign token with *no
change to any of them* — but only because the probe forges the two
claims Shaken Fist's own minting creates, and the two things
direct-bearer was supposed to buy do not survive that forgery.

#### 1. The mechanism, and whether it is private API

Three mechanisms were built and measured.

| | What it is | Private API? | Verdict |
|---|---|---|---|
| **A** | Validate with `federation.validate_token()`, then assign `flask.g._jwt_extended_jwt`, `_jwt_extended_jwt_header`, `_jwt_extended_jwt_user`, `_jwt_extended_jwt_location` | **Yes** — four undocumented attribute names, copied from `flask_jwt_extended/view_decorators.py:113-116` | Works. Four lines. Keeps every one of `validate_token()`'s checks |
| **B** | `@jwt.decode_key_loader`, returning the issuer's JWKS key for a foreign `iss` | **No** — a documented `JWTManager` callback | Authenticates, and `flask_jwt_extended` populates its own context. But it silently drops three checks (below) |
| **C** | Anything that calls `verify_jwt_in_request()`, then mutates the `jwt_data` dict it returns | No name, but an undocumented *aliasing* guarantee | Works: `assertIs(jwt_data, get_jwt())` passes, so mutating the documented return value rewrites the identity |

There is no public "set the verified token for this request" API in
`flask_jwt_extended` 4.7.4 (confirmed by reading `utils.py` and
`view_decorators.py` in the installed package). So the honest answer
is: **the mechanism needs private API, or an undocumented aliasing
guarantee.** Mechanism A is the one worth costing, because it is the
only one that keeps `federation.validate_token()` in the path.

What mechanism B costs, measured rather than argued:

* `JWT_DECODE_ALGORITHMS` is global, so it must be widened to
  `['HS256'] + federation.ALLOWED_ALGORITHMS` for the whole
  application. The classic consequence — forge an HS256 token using
  the issuer's public key as the HMAC secret — does not land, but only
  because PyJWT refuses an asymmetric key as an HMAC secret
  (`jwt/algorithms.py:357`), at *both* encode and decode. The forgery
  had to be assembled by hand to test the decode side at all.
* `JWT_DECODE_AUDIENCE` and `JWT_DECODE_ISSUER` are global, so the
  **per-issuer audience binding is lost**: a token minted for
  `somebody-else` verified happily through `verify_jwt_in_request()`
  while `federation.validate_token()` refused it.
* `flask_jwt_extended`'s `_decode_jwt()` passes no `require` list, so a
  **token with no `exp` verified**. `validate_token()` requires `exp`,
  `iss` and `aud`.

#### 2. What the context has to be forged with

An IdP `sub` is the IdP's subject. `parse_jwt_identity()`
(`shakenfist/util/access_tokens.py:74-80`) splits on `:` and demands
exactly two parts, so `alice@example.com` raises
`CannotParseJWTIdentity` and GitHub's `repo:x:ref:y` raises it too.
Populating the context with the IdP's claims untouched therefore
authenticates and then dies: under mechanism B a good foreign token
answered **500**, because `CannotParseJWTIdentity` is neither caught by
`verify_token`'s `except (TypeError, ValueError)` nor by
`handle_authorization_exceptions` (`base.py:2304`).

So the probe derives a namespace by evaluating mapping rules against
the token's claims, per request, and writes `sub` as
`'<namespace>:<key_name_prefix>'`. With that one forgery everything
downstream works unchanged — asserted directly inside a patched
endpoint handler: `get_jwt_identity() == 'staff:alice'`,
`parse_jwt_identity() == ['staff', 'alice']`,
`request_namespace() == 'staff'`, `caller_scopes() is None`, and the
IdP's own claims (`email`, `iss`, `groups`) all still present. The
cost is in the same assertion: `sub` no longer holds
`alice@example.com`. **The IdP's identity is destroyed to make the
identity readable.**

Two further consequences fell out and are measured, not inferred:

* **The audit trail records a key that does not exist.**
  `log_token_use` → `_token_use_event` recorded
  `('staff', 'alice', 'token used to authenticate request')`. There is
  no key named `alice`; the IdP identity appears in no event.
* **The context can name exactly one namespace.** With the same
  identity matching rules in two namespaces, `request_namespace()`
  still returns one string. Multi-namespace — the one consideration
  open question 13 says favours direct-bearer — is not delivered by
  fitting inside this path. Delivering it means changing
  `request_namespace()`'s contract, which is step 0a's 57 sites.

**If it could not be done**, the second path would be *four* functions,
not 57 call sites: `base.py:291` (`caller_is_admin`), `base.py:310`
(`caller_scopes`), `base.py:2462` (`_enforce_scope`) and
`access_tokens.py:76` (`parse_jwt_identity`) are the only non-test
readers of `get_jwt()` / `get_jwt_identity()`. A fifth, incidental one
is the request-log identity lookup in `log_request`
(`base.py:2104-2112`), which calls `decode_token()` inside a bare
`except Exception: pass`, so a foreign token raises there and the
request is logged with no identity. (That site reads a claim named
`identity`, which this cluster does not mint — the identity claim key
is `sub` — so it already yields `None` for cluster tokens too.
Pre-existing, out of this step's scope, not filed.) The number that
matters is small, and it is worth saying plainly: "every downstream
authorisation reader" is four functions.

An error-translation layer is also required either way.
`handle_authorization_exceptions` catches only `flask_jwt_extended`
and PyJWT exception classes, so `federation.TokenValidationFailed`
escaped as **500** until the probe mapped it onto
`NoAuthorizationError`. Three tests failed exactly that way before the
mapping was added.

#### 3. The three missing-`scopes` sites

`api_scopes.satisfies(None, anything)` is `True`;
`satisfies([], anything)` is `False`, including `satisfies([], None)`
(asserted directly). The three live sites were then driven through a
real request with three shapes of synthetic context: `scopes` absent
(what an IdP actually sends), `scopes: []` (the normalised form of
"deny for tokens we did not mint"), and `scopes` copied from the
matched mapping rule.

| Site | Function | `scopes` absent | `scopes: []` | from the rule |
|---|---|---|---|---|
| `base.py:292` | `caller_is_admin` | `GET /admin/locks` → **200** | **403** | rule-dependent |
| `base.py:2464` | `_enforce_scope` | `GET /artifacts` → **200**; `POST /artifacts` → not 403 | **403** on everything | 200 on read, **403** on write under a read-only rule |
| `auth.py:1109-1118` | `_rule_arguments` | `POST /auth/namespaces/system/rules` with `scopes: ['*']` → **200**, rule created granting `*` | never reached — refused at site 2 | reached, and caps: `['*']` → **403** with the cap's own message, `['rule.write']` → **200** |

Controls, so that none of those readings is a refusal for some other
reason: a cluster-minted token scoped `['artifact.read']` into
`system` gets **403** at `/admin/locks`; a cluster-minted token scoped
`['rule.write']` authoring `scopes: ['*']` gets **403** from the cap;
a cluster-minted unscoped token is unaffected by the probe's split.

#### 4. The consequence at site 3, which is not "safer"

An issuer-dependent default of "deny for tokens we did not mint" has
two possible implementations and both go wrong at site 3, in opposite
directions.

*Normalise the claim to `[]` at the boundary.* Then site 3 **is never
reached**: the request is refused one layer earlier by `_enforce_scope`
for `rule.write`. An IdP-authenticated administrator cannot author any
mapping rule at all — not a wildcard one, not a narrow one, not even
one granting `[]` — and the refusal reads `token is not scoped for
this operation`, which says nothing about the issuer-dependent default
that caused it. OIDC login would be unable to administer federation,
which is close to the main thing an operator logs in to do, and it
would present as an inexplicable 403.

*Change `satisfies()` instead, leaving the claim absent.* Then site 3
goes the other way and **fails open**. `caller_scopes()`
(`base.py:302-312`) tests `held is None` itself, before it ever
consults `satisfies()`, and site 3 reads `caller_scopes()`. So the cap
would be lifted — the IdP-authenticated caller may author a rule
granting `*`, which is a standing instruction to mint wildcard keys in
`system`, exactly the escalation the comment at `auth.py:1093-1105`
exists to prevent — while sites 1 and 2 denied. One change, fail-closed
at two sites and fail-open at the third.

The reason is that `None` is read in two directions. At sites 1 and 2
it is a *grant* ("holds everything"), where deny is the safe default.
At site 3 it is a *cap* ("restricted by nothing"), where deny means
"holds nothing" and the safe-looking default refuses every rule. A
single issuer-dependent default cannot satisfy both.

The only default that makes all three sites coherent is to give the
foreign session a **real scope set, synthesised from the mapping rule
that authenticated it**. Measured: with that, site 2 enforces per verb
and site 3 caps at what the login granted. But two things follow. The
cap is then only as strong as the login rule — a rule granting `['*']`
reproduces the uncapped case exactly (measured). And computing it is
the exchange's own computation, run per request, against no stored key:
direct-bearer collapses into *exchange without persistence*, minus the
nonce and minus an audit row that names a real credential.

#### 5. Verdict

Direct-bearer can live inside the single enforcement path, in the
narrow sense that no downstream reader needs to change. It needs
private `flask_jwt_extended` API (four `g` attributes) plus an
exception-translation layer, and it fits only by forging the claims
Shaken Fist's own minting produces. The fit destroys the IdP's
identity, attributes every audit event to a key that does not exist,
delivers exactly one namespace per request, and leaves a
missing-`scopes` default that cannot be made issuer-dependent
coherently across the three sites without synthesising scopes from a
mapping rule — at which point what has been built is exchange, enforced
identically, with revocation and auditability given up. On this
evidence "authorisation lives in one place" is preserved by making
direct-bearer indistinguishable from exchange at the point of
enforcement.

Per the master plan, the missing-`scopes` wildcard is **not a bug**;
nothing was filed and nothing was fixed.

#### 6. The diff, and how to re-create it

```
 shakenfist/external_api/app.py                     |  39 ++
 shakenfist/external_api/base.py                    | 112 ++++
 .../tests/external_api/test_probe_direct_bearer.py | 589 +++++++++++++++++++++
 3 files changed, 740 insertions(+)
```

Nothing else was touched; `git status --short` showed exactly those two
modifications and that one untracked file.

**`shakenfist/external_api/base.py`** (+112), all of it inserted
immediately above `def verify_token(func):`:

* `PROBE_SCOPES_MODE` — module global, one of `'absent'`, `'empty'`,
  `'rule'`, selecting which `scopes` claim the synthetic context
  carries. `'absent'` is the default and is what an IdP sends.
* `PROBE_LAST_RULE` — module global, for test inspection.
* `_probe_matching_rule(issuer, claims)` — iterates `MappingRules([])`,
  skips deleted rules and rules for another issuer, and returns the
  first whose `bound_claims` `federation.match_claims()` accepts.
* `_probe_foreign_token()` — reads the `Authorization` header; returns
  `False` (so the ordinary path runs) when there is no bearer token,
  when `federation.unverified_issuer()` cannot read an `iss`, when the
  `iss` equals `config.ZONE`, or when `federation.issuer_for_token()`
  raises `UntrustedIssuer`. Otherwise calls
  `federation.validate_token()`, translates
  `exceptions.TokenValidationFailed` into
  `flask_jwt_extended.exceptions.NoAuthorizationError` (without this
  the answer is 500), derives a rule, builds `synthetic = dict(claims)`
  with `synthetic['sub'] = '<rule.namespace>:<rule.key_name_prefix>'`
  plus the `scopes` claim the mode asks for, and assigns the four
  `flask.g._jwt_extended_*` attributes. Returns `True`.
* `jwt_get_unverified_header(token)` — a two-line shim around
  `jwt.get_unverified_header`.
* `verify_token`'s wrapper gained three lines at the top:
  `if _probe_foreign_token(): return func(*args, **kwargs)`, ahead of
  the existing `verify_jwt_in_request(False, False, False, ['headers'],
  True)` — which is the split survey finding 4 predicted, in the place
  it predicted.

**`shakenfist/external_api/app.py`** (+39), inserted immediately after
`jwt = JWTManager(app)`:

* `app.config['JWT_DECODE_ALGORITHMS'] = ['HS256'] +
  federation.ALLOWED_ALGORITHMS`.
* `PROBE_DECODE_KEY_LOADER` — module global, default `False`, so
  mechanism A can be measured without mechanism B silently
  authenticating the same tokens.
* `_probe_decode_key(jwt_header, jwt_data)`, registered with
  `@jwt.decode_key_loader`: for a foreign `iss` with a live
  `TrustedIssuer`, returns
  `federation.JWKS_CACHE.signing_key(issuer, token, jwks_uri).key`;
  otherwise `config.AUTH_SECRET_SEED`.

**`shakenfist/tests/external_api/test_probe_direct_bearer.py`** (+589,
34 tests, all passing). Harness lifted from
`test_federated_exchange.py`: `MockMariaDB` with namespaces `system`,
`staff` (and `research` in one test), a locally generated RSA key
served through `shakenfist/tests/fake_jwks.py`, a `TrustedIssuer`
named `idp`, and three `MappingRule`s — `staff/engineers`
(`groups=engineering` → `['artifact.read']`), `system/admins`
(`groups=platform-admins` → `['*']`) and `system/authors`
(`groups=rule-authors` → `['rule.write']`). `_idp_token()` mints the
foreign token; `_sf_token()` mints a cluster token through `/auth` for
the controls. `_hand_rolled_hs256()` builds the HMAC forgery PyJWT
refuses to mint. Classes: `ContextPopulationTestCase` (12),
`ThreeSiteTestCase` (13), `DecodeKeyLoaderTestCase` (7),
`SatisfiesDirectTestCase` (2).

**Re-creating the environment.** `flask_jwt_extended` is not installed
on the host, and the system Python enforces PEP 668, so:

```sh
python3 -m venv <scratch>/venv
<scratch>/venv/bin/pip install -e '.[test]'      # from the worktree
```

That gives `Flask-JWT-Extended 4.7.4`, `PyJWT 2.15.1`, `Flask 3.1.3`,
matching the pins in `pyproject.toml`. There is no `pytest` in the test
extra, so the probe was run with `unittest`:

```sh
SHAKENFIST_ETCD_HOST=127.0.0.1 SHAKENFIST_EVENTLOG_SUPPRESS_GRPC=1 \
    timeout 600 <scratch>/venv/bin/python -B -m unittest \
    shakenfist.tests.external_api.test_probe_direct_bearer
# Ran 34 tests ... OK
```

**Regression check.** With both probe mechanisms in place (and, at the
time of the run, the `decode_key_loader` ungated and therefore active
for every request), the existing authorisation suites still passed in
full: `test_federated_exchange`, `test_scopes`, `test_auth`,
`test_scope_trust_composition`, `test_rules`, `test_admin` — 281 tests,
`OK`. So nothing in this probe's findings rests on having broken the
ordinary path, and the widened `JWT_DECODE_ALGORITHMS` list did not
disturb any existing test.

**Disposition.** The probe worktree was removed by the management
session after this write-up was taken, with nothing committed on its
branch (`git log develop..HEAD` empty) and the diffstat above as the
only record. D4 asks for exactly that: the finding has to be
falsifiable without the diff, so the mechanism is described above in
enough detail to re-create it.

### Step 0c — Deployments we can see, and what cannot be measured

Sampled read-only against sfcbr at 2026-11-06 23:46 UTC (2026-11-07
10:46 +1000), with `sf-client namespace list`, `sf-client --json
namespace list` and `sf-client namespace show <ns>`. Key names only;
no secret, token, nonce or hash was read or saved.

#### 1. Survey finding 9 table: shape confirmed

| Namespace | Non-service key names now | Finding 9 said |
|---|---|---|
| `system` | `roberto`, `deploy` (plus 19 `_service_key_*`) | same |
| `ci-images` | `conductor` | same |
| `sfcbr-*` x 7 | `conductor` in each; none holds `conductor-teardown` | 12, one with `conductor-teardown` |
| `static-ci`, `static-runners` | none | same |
| `docker-workers-1794006789` | none | not in finding 9 |

Eleven namespaces rather than sixteen: this is CI churn (the
`sfcbr-*` count was seventeen, then sixteen, and is now seven), not
a discrepancy. Two further observations, neither a change in shape:

* `docker-workers-1794006789` is new relative to finding 9. It holds
  no keys and trusts only `system`. It looks like another ephemeral
  CI namespace, but I did not establish that.
* `conductor-teardown` is absent now, which is consistent with it
  living in a job namespace that has since been deleted.

Shape checks: the only human-held key is still `roberto`, in
`system`; every `sfcbr-*` and `ci-images` has a `conductor` key;
`static-ci` and `static-runners` hold no keys. `static-ci` carries
`orchestrated_k3s_*` metadata, which finding 9 did not mention but
which is not a key. Every namespace's trust list contains `system`;
only `ci-images` trusts anything else. The conductor credential
count is now eight (seven jobs plus `ci-images`), not fourteen; the
one-actor-many-credentials shape is unchanged.

#### 2. The `ci-images` trust entry: a stale entry, not churn

`ci-images` trusts nine namespaces: `system`, seven live `sfcbr-*`,
and **`sfcbr-VoFFMlcdPMTKjk34`, which has no namespace row**.
`sf-client namespace show sfcbr-VoFFMlcdPMTKjk34` returns `namespace
not found`. It is the same name the planning pass saw as a trust
entry with no row, and it is still there at the time of this sample,
a period of at least tens of minutes. A churn explanation would
require the entry to have been removed by now; the other seven sfcbr
trusts all resolve to live rows.

This is the second of the two possibilities the brief named: a trust
that outlives the namespace it names. Commit `09e6dcdb56`
(2026-09-12, "Revoke trusts naming a deleted namespace") is on
`develop` and revokes inbound trust in the REST delete handler and
again in `hard_delete()`. I could not tell from read-only commands
which of these explains the survivor:

* sfcbr may not be running a build containing that commit (not
  checked; no command in the authorised list reports the server
  version);
* the namespace may have been removed by a path that does not call either hook;
* the trust may have been (re)added after the delete.

The sample is also one entry, so this establishes that the sweep is
not fully effective in the one deployment we have, not how often it
fails. Open question 14's answer may rely on trust being cleaned up
with its target only if this is understood first. Worth a separate
look at the events for `ci-images` and for `sfcbr-VoFFMlcdPMTKjk34`
(not run here).

#### 3. Repo-side: what this repository provisions a person into

**Answer: zero namespaces.** Searched `shakenfist/deploy/`,
`.github/workflows/` and `shakenfist/deploy/shakenfist_ci/`.

* `shakenfist/deploy/collection/plugins/modules/sf_namespace.py`
  creates and deletes namespaces (name and state only); it creates
  no key, so it provisions nobody.
* The node role (`shakenfist/deploy/collection/roles/node/`)
  provisions one credential, `system_key`, from `defaults/main.yml`
  or Vault (`vault_system_key_path`), written to the node `sfrc` as
  `SHAKENFIST_NAMESPACE="system"`. That is a single operator
  credential in `system`, and `kerbside_credentials.yml` registers a
  further `kerbside` key via `sf-ctl bootstrap-system-key`. Both are
  cluster-operator or service credentials, not per-person.
* `.github/workflows/` sets `SHAKENFIST_NAMESPACE=$(hostname)` in
  `code-formatting.yml`, `sync-external-docs.yml` and
  `pin-indirect-dependencies.yml`. These are runner-scoped
  namespaces, not people.
* `shakenfist_ci` (`base.py`, `test_auth.py`, `test_namespace.py`
  and others) creates throwaway namespaces and keys through
  `system_client` as test fixtures.
  `shakenfist/deploy/nodelifecycletests.sh` passes a key via
  `SHAKENFIST_KEY`. None provisions a person.

The `roberto` key in `system` is therefore created by hand outside
anything in this repository. The repository contains no tooling that
creates a per-person key in any namespace.

#### 4. What the docs tell an operator about human credentials

* `docs/user_guide/authentication.md` is the only place that
  addresses humans. It says: "Normally your administrator will
  create a key per user, but it's also possible to create a key per
  system -- there are no real rules imposed". It then tells the user
  to put a `.shakenfist` file in their home directory with
  namespace, key and apiurl. It also says keys may carry expiry and
  scopes, and that those cannot be read back through any API (the
  #3672 gap).
* `docs/operator_guide/authentication.md` says nothing about human
  credentials as such. Its "Federated identity" section describes
  trading an external identity for a namespace key, and its worked
  example is GitHub Actions; it names "a service account in an
  Authentik instance" as the other case. Both are workloads. Its
  human-adjacent content is the generated-key format (`sfk_...`),
  key expiry and reaping, and the note that an administrator must
  add keys by hand (`sf-client namespace add-key`, or `POST
  /auth/namespaces/<ns>/keys`).

So the guidance is: an administrator hand-creates a long-lived key
per person in that person's one namespace, and the person keeps it
in a file. No document describes a person belonging to several
namespaces, nor any login flow for people. The user guide's model is
one namespace per user ("the administrator ... will have created a
_namespace_ to contain the resources you create").

#### 5. Where open question 13's distribution evidence would have to come from

It would have to come from real deployments with staff: how many
namespaces each person at a handful of operators' clusters is
actually in. We do not have it. sfcbr is a CI cluster with no staff,
so it holds one human-held key (`roberto`) in one namespace, which
is a sample of size one and says nothing about a distribution. This
repository's own tooling provisions no person anywhere, so it cannot
stand in for that evidence either. The conductor's many credentials
show the multi-namespace shape for a machine, but a machine whose
provisioning automation knows each namespace is not a person
choosing between credentials. We have not surveyed any other Shaken
Fist deployment, and I have no data that would let me say whether
the usual answer is one namespace or several.

### Step 0f — deferred work filed

Filed [#4823](https://github.com/shakenfist/shakenfist/issues/4823),
to retire `verify_token`'s exact-name `_service_key` nonce-check
bypass. Framed as bounded rather than exploitable, which it is:
`is_reserved_key_name()` (`shakenfist/util/credentials.py:109`)
rejects that name on both the namespace-create and key-create
paths, so only a validly signed pre-nonce token could take the
branch. #3521 is closed and concerns expired `_service_key` rows
never being purged, which is a different thing; no open issue
covered the bypass.

Commented on [#3672](https://github.com/shakenfist/shakenfist/issues/3672)
recording that `NamespaceKey.external_view()` already produces the
shape the issue asks for and that `AuthNamespaceKeysEndpoint.get`
still serves bare names from the legacy `nonced_keys` view, so the
fix is plumbing rather than modelling — and that it blocks the
service-account credential work. No labels were added to either.
