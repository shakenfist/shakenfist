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
# 58 lines; one of them is the definition itself.
grep -rn 'request_namespace()' --include='*.py' . | grep -v '/tests/'
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
listings minutes apart gave seventeen and sixteen.

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
holding fourteen separate credentials, all named `conductor`,
one in each namespace it works in, because a namespace key can
name exactly one namespace. That is precisely the cost open
question 13 attributes to exchange — "five exchanges and a
client that knows which credential to present for which
request" — being paid today, at thirteen. And `ci-images` takes
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
conductor's fourteen keys are created by automation that already
knows which namespace it is working in. A human's would not be.

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
| 0a | high | opus | none | **Classify every `request_namespace()` call site.** Enumerate with `grep -rn 'request_namespace()' --include='*.py' . \| grep -v '/tests/'` — 58 lines at `32de6cec7f`, of which one is the definition in `shakenfist/util/access_tokens.py:83`, leaving 57 sites. Distribution: `external_api/artifact.py` 17, `external_api/instance.py` 9, `external_api/base.py` 7, `external_api/network.py` 6, `external_api/label.py` 5, `external_api/auth.py` 4, `external_api/clusteroperation.py` 3, `external_api/util.py` 2, and one each in `external_api/blob.py`, `external_api/agentoperation.py`, `schema/object_reference.py`, `blob.py`. For each site, read enough of the enclosing function to decide which of exactly three things the comparison means: **(A)** the caller's own namespace, where the check is "is this mine"; **(B)** any namespace the caller holds, where the check would want to be a set membership test if a credential could name several; **(C)** the namespace the object being touched is in, where the string is being used as a value rather than as an identity. Add a fourth bucket **(D) other**, and for every row placed in it say what it actually is — a bucket that absorbs the awkward cases silently is worse than no bucket. Write the result as a markdown table appended to `docs/plans/PLAN-oidc-authentication-phase-00-decisions.md` under `## Results`, one row per site, and begin every row with the site in backticks so the Definition of done's row count can be taken mechanically: `` | `shakenfist/external_api/artifact.py:123` | ArtifactEndpoint.get | A | ...justification... | ``. The four columns are the site, the enclosing function or class, the bucket letter, and a one-line justification. Then write a summary: counts per bucket, and a plain statement of whether a multi-namespace credential is a mechanical change with hand-written exceptions or a redesign. Two sites need particular care and must be called out individually: `external_api/auth.py:101`, where `requires_namespace_ownership` passes `request_namespace()` to `namespace_is_trusted()` so the check is already "owner or trusted" rather than "owner", and `external_api/base.py:282` in `caller_is_admin`, where it is compared to the literal `'system'`. One thing is already checked so you do not have to: the JWT identity is minted as `f'{ns.uuid}:{keyname}'` (`access_tokens.py:54`), which *looks* like a uuid but is not — `Namespace.uuid` returns the namespace's name (`namespace.py:64-70`, created by `cls._db_create(name, {'uuid': name, ...})` at `namespace.py:164`), so the master plan's prose that the identity is `<namespace>:<keyname>` is correct and every one of these comparisons really is against a namespace name. Change no code. |
| 0b | xhigh | opus | worktree | **Probe whether direct-bearer can live inside the single enforcement path.** Answer one question: can a token validated by `shakenfist/federation.py:validate_token()` be made to flow through the existing authorisation path unchanged? The obstacle is that `verify_token` (`shakenfist/external_api/base.py:1422`) calls `verify_jwt_in_request(False, False, False, ['headers'], True)` at line 1425, which validates against the cluster's own secret via `flask_jwt_extended`, so a foreign token is rejected before any Shaken Fist code runs; and everything downstream — `parse_jwt_identity()` and `request_namespace()` (`shakenfist/util/access_tokens.py:74-84`), `caller_scopes()` (`base.py:302`), `_enforce_scope` (`base.py:2458`), `caller_is_admin` (`base.py:279`) — reads that library's request context through `get_jwt()` / `get_jwt_identity()`. Build the smallest thing that works: peek at the unverified `iss` the way `federation.py:236` already does, look it up against `TrustedIssuer`, validate through `federation.validate_token()`, and then try to populate the `flask_jwt_extended` request context so the downstream readers need no change. Report, with specifics: whether that population is possible at all with the installed `flask_jwt_extended`; if it is, how ugly it is and whether it depends on private API; if it is not, how many downstream readers would need a second path. **Then test the three missing-`scopes` sites specifically** — an IdP token carries no `scopes` claim, and `api_scopes.satisfies(None, anything)` returns `True` (`shakenfist/external_api/scopes.py:137-142`). Demonstrate empirically what such a token *gets* at `base.py:292` (`caller_is_admin`), `base.py:2464` (`_enforce_scope`) and `auth.py:1109-1118` (the cap on what scopes a mapping rule may grant). The third is a narrowing check where `None` means "legacy operator, unrestricted", so an issuer-dependent default of "deny for foreign tokens" has a non-obvious consequence there — say what it is. Do not fix anything, do not file an issue (the master plan says in as many words that the wildcard default is not a bug and must not be filed as one), and **do not commit**: the worktree is discarded. The write-up is the whole deliverable, so it must name every file and function touched and include a diffstat, in enough detail that the probe can be re-created from the text alone. Append it to the phase plan under `## Results`. |
| 0c | medium | sonnet | none | **Characterise the deployments we can actually see, and state plainly what cannot be measured.** The planning pass already measured sfcbr on 2026-11-07; survey finding 9 above has the table. Headlines: sixteen namespaces, twelve of them ephemeral per-CI-job `sfcbr-*` whose count moves with CI load; exactly one key in the cluster held by a person (`roberto`, in `system`); `deploy` in `system`; and a `conductor` key in `ci-images` and in every job namespace — one actor, fourteen credentials, because a key names one namespace. Confirm that with `sf-client namespace list` and `sf-client --json namespace list`, and `sf-client namespace show <ns>` per namespace (all read-only; credentials are already present in the environment) and report any difference, but do not re-derive the reading — that is written. What to add: how `ci-images`' trust list relates to the live job namespaces at the moment you sample (the planning pass saw a trusted namespace that no longer had a row, which is either churn or a stale trust entry, and which of those it is matters to open question 14's answer). Then add what the planning pass did not do: how many namespaces the repository's own deployment and CI tooling provisions a *person* into (search `shakenfist/deploy/`, `.github/workflows/`, and `shakenfist/deploy/shakenfist_ci/`), and what `docs/operator_guide/authentication.md` and `docs/user_guide/authentication.md` currently tell an operator to do about human credentials. Finish with one short honest paragraph: what open question 13's distribution evidence would need to come from, and why we do not have it. Do not extrapolate a distribution from a sample of one, and do not argue for either option — that is step 0d's job. Append to the phase plan under `## Results`. Change no code. |
| 0d | high | opus | none | **Write the committed answers onto the master plan.** Gated by the back brief; do not start before the management session has released it. Read steps 0a, 0b and 0c's results in `docs/plans/PLAN-oidc-authentication-phase-00-decisions.md`, then write answers into `docs/plans/PLAN-oidc-authentication.md` under open questions 13, 14, 5, 7 and 11, in the same style the already-answered questions use: a bold lead naming the resolution and the date, then the reasoning, then what it constrains downstream. Each answer commits to one option — no "likely", no "lean", no "to be decided in a later phase". Question 14's answer must include the line between namespace trust and a multi-namespace credential: when an operator reaches for each, and why (D7). Question 11's answer decides whether phase 6 exists; if it is "no", say so and strike the row rather than shrinking it, exactly as the master plan's phase 6 note instructs. Also record #3672 as a phase 6 prerequisite in that row's note (survey finding 7). Then update the Execution table: any row whose content the answers have changed, plus phase 0's own `Status`. Keep the `Status` cell to a single vocabulary term. Do not touch `docs/plans/order.yml` — phase files are not registered there. The index row's status and `N of M` are derived by `tools/check-plan-status.py`; run it. |
| 0e | low | sonnet | none | **Sweep the master plan's stale file references.** `docs/plans/PLAN-oidc-authentication.md` carries 39 distinct `` `file:line` `` references cited about 70 times, and roughly three quarters are wrong — `shakenfist/external_api/base.py` has grown by over a thousand lines since they were written. Re-resolve every one and correct it. Start by running the **reference verifier** from the "What the survey found" section above, which prints what each reference currently lands on; then find each symbol by name and write its real line numbers. Known-good and needing no change: `shakenfist/federation.py:39-51`, `:68` and `:120-210`; `shakenfist/trusted_issuer.py:41`; `shakenfist/mapping_rule.py:244`; and the three `shakenfist/schema/trusted_issuer_attributes.py` references. `shakenfist/federation.py:347-363` *is* wrong — the bound-claim matching it cites has moved to around line 403. Do not change the plan's referencing *style* to symbol names; this repository's plans cite `file:line`, and restyling a master plan is not this step's job. Add one sentence to the Execution section saying the references were re-resolved, naming the commit you did it at, so a later reader knows what they were true at. The planning commit already fixed the Prompt section's dead `CLAUDE.md` pointer and every reference cited in the Prompt or Execution sections — including, because the same strings recur, their copies in Open questions. Do not redo those; what is left is the references that appear only in Open questions. Change nothing outside `docs/plans/`. |
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
      sites=$(grep -rn 'request_namespace()' --include='*.py' . |
              grep -v '/tests/' | grep -vc 'def request_namespace')
      rows=$(grep -cE '^\| `[a-z_/]+\.py:[0-9]+` \|' \
              docs/plans/PLAN-oidc-authentication-phase-00-decisions.md)
      echo "$sites sites, $rows rows"   # 57 sites at 32de6cec7f
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
