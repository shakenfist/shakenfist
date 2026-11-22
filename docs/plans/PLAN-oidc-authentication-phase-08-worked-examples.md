# OIDC authentication phase 8: worked operator examples

## Prompt

This phase was planned by `/next-phase oidc-authentication` on
2026-11-21, immediately after phase 7 merged as
shakenfist/client-python#456. The master plan is
[PLAN-oidc-authentication.md](PLAN-oidc-authentication.md); read
its phase 8 paragraph and open question 12 before this file.

The master plan sets the bar for this phase and it is the reason
most of the step table is agentic rather than editorial:

> the examples must be produced by actually configuring the two
> providers, not written from memory of how they work. A
> plausible-looking walkthrough that does not run is worse than
> no walkthrough.

## Planning effort

**Medium**, reviewed at **high**, as the master plan specifies.
The planning itself was mostly verification: the operator
documentation this phase extends is already extensive, and the
work was finding out what of it is true. One survey finding
changed the phase's shape and was referred to Michael (D1).

## Scope

**In scope:**

* An end-to-end worked example of configuring Keycloak for
  *human login*, produced against a real Keycloak.
* The same for Authentik, produced against a real Authentik.
* Making a group claim able to grant a namespace at all, which
  it cannot today (see finding 1 and D1).
* The `tools/` lab that stands both providers up, so the
  walkthroughs are reproducible and phase 9 inherits a starting
  point rather than a blank page.
* Closing open question 12: the operator half is this phase, and
  the users' half became a real gap the moment phase 7 shipped a
  `login` verb this repository's user guide does not mention.

**Out of scope:**

* Driving a login end to end against a live cluster with a live
  provider. That is phase 9, and it needs CI topology this phase
  does not build. This phase proves the *provider* half against
  real providers and the *cluster* half against what they
  actually emit, captured as fixtures.
* `sf-client` verbs for issuers and mapping rules
  (client-python#451, still open). The walkthroughs call the
  REST API with `curl`, exactly as the existing GitHub Actions
  example does and for the same stated reason.
* Any change to what a rule's *matcher* may contain. The
  matchers stay exact strings or lists of exact strings; only
  what a *token's* claim value may be is widened.
* Globbing, prefix matching or regular expressions in claim
  matching, which the developer guide declines on the record and
  this phase does not reopen.

## What the survey found

The master plan's phase 8 paragraph was written in 2026-06 and
every file reference in it has since moved. More importantly, two
of its factual claims are now false and one of its deliverables
is impossible against the tree as it stands.

### 1. A group claim cannot grant a namespace today

This is the finding that reshaped the phase.
`federation.claim_matches()` refuses any token claim value that
is not a `str` (`shakenfist/federation.py:413-429`):

```python
    if not isinstance(value, str):
        return False
```

A `groups` claim in an OpenID Connect identity token is an
**array** of strings. Keycloak's group-membership mapper writes
one (it implements the multivalued group list, optionally as full
paths); Authentik's default OpenID `profile` mapping does the
same. So `bound_claims` of `{"groups": "sf-admins"}` can never
match either provider's token, and the phase's central
deliverable -- "including how a group claim reaches a namespace
grant" -- cannot be written truthfully.

The behaviour is pinned by a test, which is why it cannot be
read as an oversight (`shakenfist/tests/test_federation.py:658`):

```python
    def test_non_string_claim_values_never_match(self):
        # A claim of 1 must not satisfy a matcher of "1": a federation
        # that coerces is one that eventually accepts the wrong thing.
        self.assertFalse(federation.claim_matches(1, '1'))
        self.assertFalse(federation.claim_matches(True, 'true'))
        self.assertFalse(federation.claim_matches(None, 'None'))
        self.assertFalse(federation.claim_matches(['a'], 'a'))
```

The comment argues coercion, and coercion is a good argument for
the first three assertions. It is not an argument for the fourth:
comparing a matcher against each element of a list of strings
coerces nothing. The list case was swept in with the scalar
cases and never had a rationale of its own.

Worse, the operator guide already advises the binding that cannot
work (`docs/operator_guide/authentication.md:539`): "bind the
narrowest claim that expresses the entitlement, usually a group,
rather than an address". An operator following the guide today
writes a rule that silently never fires, and the login is refused
with the deliberately uninformative 401 that `POST /auth/login`
gives every refusal.

**This was referred to Michael**, who chose to fix the matching
in this phase. See D1.

### 2. Every line reference in the phase 8 paragraph is stale

| The paragraph says | What is there now |
|---|---|
| issuer configuration and the GitHub example at `operator_guide/authentication.md:203` | line 203 is `## Keys and the event log`; the example is at 582 and issuer configuration spans 183-394 |
| Authentik at `operator_guide/authentication.md:143-144` | `## Keys and the event log`; Authentik is now at `docs/glossary.md:131`, `developer_guide/authentication.md:23` and `developer_guide/api_reference/authentication.md:381` |
| Authentik at `developer_guide/authentication.md:429` | a closing bracket of a code block |
| Keycloak appears once, at `operator_guide/authentication.md:384` | twice in that file (277, 766), plus `docs/release_notes/v07-v08.md:183`, `docs/developer_guide/coding_rules.md:499` and `shakenfist/config.py:642` |

### 3. "Nothing in the tree exercises Keycloak" is no longer true

Phase 3 added Keycloak- and Authentik-shaped fixtures to the
discovery tests, precisely because their issuer URLs differ in
the way that breaks discovery
(`shakenfist/tests/test_oidc_discovery.py:27-30`, and
`test_a_keycloak_issuer_keeps_its_realm_path` at :146). The
trailing-slash asymmetry the walkthroughs have to explain is
already settled in code and documented at
`docs/operator_guide/authentication.md:275-283`.

### 4. The generic human-login documentation is already written

The phase 8 paragraph implies an operator guide with an issuer
section and little else. Phases 3 and 4 wrote 160 lines of
human-login operator documentation that is accurate and detailed:
`### Discovery` (240), `#### When discovery must succeed` (314),
`#### Re-running discovery` (338), `### Updating an issuer` (358),
`### Human login metadata` (395) and `### Human login mints
credentials` (431). The `client_id`-is-not-`audience` trap phase
4's survey found is already a prominent warning box (465-490).

So this phase owes **less** than the paragraph suggests on
mechanics and **all** of what it suggests on provider specifics:
nothing anywhere tells an operator which buttons to press at
Keycloak or Authentik, and that is the whole of the gap -- plus
the group-claim hole in finding 1.

### 5. Authentik advertises a device endpoint it may not honour

Authentik's discovery document carries
`device_authorization_endpoint`, pointing at the global
`/application/o/device/` path, and its discovery document is
per-application at
`/application/o/<slug>/.well-known/openid-configuration`. But the
device code flow only *works* when the active brand has a
device-code flow configured, which is not the default and which
Authentik's own documentation says must be created by hand.

Shaken Fist decides login capability from the discovery
document's endpoints (`docs/operator_guide/authentication.md:401`),
so an Authentik issuer will be advertised as device-capable while
every device login against it fails. `sf-client` prefers the
device flow wherever it is advertised, so the default experience
on a stock Authentik is a login that cannot complete. The
walkthrough has to say so; whether the cluster should do anything
about it is noted under future work rather than decided here.

### 6. The user guide never mentions logging in

`docs/user_guide/authentication.md` has six sections, none about
human login, and its single reference to a login
(:138) is about provenance. That was correct when open question
12 was last revised -- "the users' view exists except for the
login flow that does not exist yet, which is not a documentation
gap" -- and phase 7 made it a gap by shipping the flow.
`client-python` documents it at `docs/human-login.md`, which this
repository's user guide does not link.

### 7. The REST API is still the only way to configure an issuer

`client-python#451` is open, so the walkthroughs use `curl`. The
existing GitHub example already says so in one sentence and the
new sections should reuse it rather than re-explain it.

### 8. Nothing else found

The master plan's claims about what open questions 13 and 14
settled, the session shape the walkthrough documents, and the
`FEDERATION_MAX_LOGIN_CREDENTIALS` cap all check out against the
tree. `match_claims()` records the token's values rather than the
rule's matchers, as documented, and that contract is what D3 has
to decide for an array.

### Corrections made at source

Made in the same commit as this plan, so the next reader does not
re-derive them:

* The master plan's phase 8 paragraph: the four stale references
  replaced, the Keycloak claim corrected, and a sentence added
  saying what phases 3 and 4 already documented.
* The master plan's Execution table: phase 7 to `Complete` with
  its merge, phase 8 linked to this file.
* `docs/plans/index.md`: the row's arithmetic to `5 of 11`, and
  the description extended with what this survey found.

Open question 12 is **not** closed by this commit. It is closed
by step 8g, when the documents it asks for exist.

## Decisions

### D1. A list-valued claim matches by exact membership

`claim_matches()` gains one case: when the token's claim value is
a list, it matches if any element is a string equal to the
matcher, or equal to one of the matcher's alternatives.

Everything the existing tests pin stays pinned. No globbing, no
prefix matching, no regular expressions, no coercion: `1` still
does not match `"1"`, a non-string element is skipped rather than
stringified, and a nested list does not recurse. An empty list
matches nothing, as an empty matcher list already does.

This is what every other OpenID Connect relying party does with
a group claim, and the alternative is worse than it looks.
Authentik can emit a scalar entitlement claim through a custom
scope mapping, so a documentation-only phase is possible there;
Keycloak has no group-driven way to emit a scalar. Its
group-membership and role mappers are multivalued by
construction, so the Keycloak walkthrough would have to tell an
operator to set a per-user attribute on every person -- which is
not group-driven entitlement at all, and contradicts the operator
guide's own advice.

Michael chose this option over a documentation-only phase and
over splitting the matching change into its own phase.

### D2. The widening lives in `claim_matches()` and nowhere else

`match_claims()` is not touched beyond what D3 requires, and no
comparison is written a second time. This is the property
`matching_rules()`'s docstring already names: exact string
equality "stays in one function so that it cannot be loosened in
one place and not the other". A list case added in the wrong
place would be the loosening that docstring is guarding against.

### D3. Provenance records the elements that matched, not the whole claim

`match_claims()` records the claims that satisfied a rule into
the minted key's provenance, as the token's values rather than
the rule's matchers. For a scalar that is unchanged. For a list,
provenance records **only the elements which matched**.

This is the decision most likely to be argued with, so here is
the argument on both sides. Recording the whole array is the
literal reading of "the token's value", and an auditor might
reasonably want to see everything the token claimed. Against
that: a person's complete group membership is not what the grant
was made on, it is unbounded in a way a scalar claim is not, and
it would be written onto every key of every login and kept for
the life of the key. The matched subset is the grant as it was
actually made, which is what the docstring says provenance is
for, and it is the narrower thing to store about a person.

Mechanically, `claim_matches()` stays a predicate and gains a
sibling that returns the matching values; the predicate is
`bool()` of the sibling, so D2 still holds -- there is one
comparison, used two ways.

### D4. Both providers get a full walkthrough, Keycloak first

Not one walkthrough with a list of Authentik deltas. The two
differ in the three things most likely to go wrong -- the
trailing slash on the issuer URL, where the group claim comes
from, and whether the device flow works at all -- so a reader
configuring Authentik from a Keycloak-shaped walkthrough is
reading around the parts that matter. Keycloak goes first
because its issuer URL is the unsurprising one.

### D5. The providers are configured for real; the cluster half is proved against fixtures

Steps 8a and 8b stand up each provider in Docker, configure it,
complete a login by hand, and capture two artefacts per provider:
the discovery document, and the decoded claim set of a real
identity token. Those captures are committed as test fixtures
and the cluster-side tests in 8c assert against them.

What this phase does not do is drive `sf-client login` against a
live cluster trusting a live provider. That needs a cluster whose
`sf-api` can reach the provider over TLS it trusts, which is CI
topology, which is phase 9. The split is deliberate: the
expensive, rot-prone knowledge in a walkthrough is the provider
configuration, and that is what is being verified against the
real thing here.

### D6. The lab lands in the repository, under `tools/`

`tools/idp-lab/` carries a compose file with pinned image tags, a
setup script that configures both providers through their admin
APIs, and a README. Three reasons: a walkthrough nobody can
reproduce is a walkthrough that rots silently; phase 9 needs a
containerised provider and should start from something that
already works; and the captures in D5 have to be regenerable or
they become unfalsifiable. `tools/` is where this repository
keeps scripts of this kind, and the scripts are files rather than
inline YAML because CI steps must not carry large scripts.

### D7. The user guide gets a pointer, not a second walkthrough

A short section in `docs/user_guide/authentication.md` saying
that a person logs in with `sf-client login`, that the cluster
mints one key per namespace they are entitled to, and linking
`client-python`'s `docs/human-login.md` by absolute URL. The
client's documentation is the client's to maintain, and a copy
here is a copy to drift. This closes the users' half of open
question 12.

### D8. The device-flow caveat is documented in two places and fixed in neither

Finding 5 means a stock Authentik advertises a device flow that
does not work, and step 8b established that in a stronger form
than the finding claimed: the discovery document taken before a
brand device-code flow exists and the one taken after are
identical byte for byte. Nothing a relying party can read
distinguishes a working Authentik from a broken one. There are
also two switches rather than one -- the brand's device-code
flow, and the provider's own `grant_types`, whose API default is
empty -- and the document advertises the endpoint, and lists the
device grant in `grant_types_supported`, in both broken states.

This phase documents it: in the Authentik walkthrough as two
steps the operator must not skip, and in the discovery section
as a caveat about what the endpoint list proves. It does not add a probe, a health check or a
configuration flag, because the cluster cannot distinguish
"advertised and unconfigured" from "advertised and working"
without starting a flow, and a cluster that starts flows at an
identity provider to test it is the thing the discovery section
already refuses to do on a timer. Future work records the
alternative.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | xhigh | opus | none | Stand up Keycloak in Docker (pin a tag; `quay.io/keycloak/keycloak`, `start-dev` is fine, but it generates no certificate and serves http only: supply a self-signed one, because the discovery code refuses a non-https issuer and the capture would not be a usable fixture). Create a realm, a public client suitable for `sf-client` (public because the thing registering is a program on a person's laptop, and the device grant requires it), a group, a user in that group, and a group-membership protocol mapper writing a `groups` claim into the **ID token**. Enable the OAuth 2.0 device authorization grant on the client. Then complete both a device login and an authorization-code login by hand and capture under `shakenfist/tests/files/idp/keycloak-*.json`: the full `.well-known/openid-configuration`, and the decoded claim set of the identity token with every value that identifies a real person replaced by an obvious placeholder (`sub`, `email`, `name`; keep `aud`, `iss`, `groups`, `exp` shapes intact). Read the captures back the way this repository already reads test data -- `os.path.join(os.path.dirname(__file__), 'files', name)`, as `shakenfist/tests/test_image_checksums.py:41` does -- and note that no packaging change is needed, because `pyproject.toml:228` excludes `shakenfist.tests*` from the wheel and `tools/check-wheel-data.py` only guards `shakenfist/data/*.yaml`. Record the exact administrative steps as you go, in note form in this plan's Results section -- step 8e turns them into prose, so what matters here is that nothing is remembered rather than observed. Do not write any documentation prose. Do not configure Authentik. Stop the containers when you are done; the lab script in 8g is written from what you learn here, so keep the commands you ran. |
| 8b | xhigh | opus | none | The same for Authentik (`ghcr.io/goauthentik/server`, pinned; it needs a postgres and a redis, which the compose file in 8g will carry). It serves https out of the box, unlike step 8a's provider, but its built-in certificate carries no `subjectAltName`, so the discovery fetch fails on a hostname mismatch that no CA bundle setting can repair: a certificate with a SAN has to be uploaded and set as the brand's web certificate. Create an OAuth2/OpenID provider and an application, note the issuer URL **including its trailing slash**, and arrange a `groups` claim in the ID token (the default OpenID `profile` mapping, or a custom scope mapping if that is what it takes -- record which). Configure a device-code flow at the **brand**, which is not the default, and verify both that the discovery document advertises `device_authorization_endpoint` and that a device login actually completes; if it does not complete without the brand flow, capture that failure's symptom too, because finding 5 says that is the default experience and the walkthrough must name it. Capture the same artefacts under `shakenfist/tests/files/idp/authentik-*.json`, same placeholder rules. Notes, not prose. |
| 8c | high | opus | none | Implement D1, D2 and D3 in `shakenfist/federation.py`. `claim_matches(value, matcher)` keeps its signature and meaning; add `matched_claim_values(value, matcher) -> list[str]` returning the values which matched (`[value]` for a scalar hit, the matching elements for a list, `[]` for no match) and make `claim_matches()` return `bool()` of it. `match_claims()` (:432) records `matched_claim_values()` for a list claim and the token's value for a scalar, so provenance keeps its current shape where nothing changed. Amend `test_non_string_claim_values_never_match` rather than deleting it: 1, True and None must still never match, and move the `['a']` assertion into a new test which asserts membership works -- the comment on the old test argues coercion and does not cover lists, which finding 1 explains. Add tests for: a matching element among several, no matching element, a non-string element skipped alongside a matching string, a nested list refused, an empty list refused, a list against a matcher list (both the hit and the miss), and provenance recording only the matched elements. Then add an end-to-end rule-matching test for each provider, reading the real claim sets 8a and 8b captured, asserting a `{"groups": "<the group>"}` rule matches and a rule naming a group the person is not in does not. Update `docs/developer_guide/authentication.md`'s `### Claim matching` section (:525) and the `bound_claims` swagger description in `shakenfist/external_api/auth.py:1917-1921`, both of which state the old rule. Do not touch `validate_bound_claims()`: matchers are unchanged. |
| 8d | high | opus | none | Mutation-test 8c, following the `mutation-check` skill. At minimum: make the list case match on `in` rather than equality (prefix matching) and confirm a test fails; make it stringify a non-string element; make it recurse into a nested list; make `matched_claim_values()` return the whole list rather than the matched elements and confirm the provenance test fails; make the scalar path return `[value]` unconditionally. Run each mutant under `timeout` and with `-B`, restore from pristine copies rather than `git checkout`, and record the table in this plan's Results section. A surviving mutant is a missing test, not a note. |
| 8e | high | opus | none | Write the two walkthroughs into `docs/operator_guide/authentication.md`, as `### A worked Keycloak example` and `### A worked Authentik example`, after the GitHub Actions example at :582 and before `### Abuse resistance`. Each one: what to create at the provider (from 8a/8b's notes, naming the version it was produced against), the `curl` that creates the issuer including `client_id` and `oidc_scopes`, the `curl` that writes a group-bound mapping rule in a namespace, and what the person then runs -- `sf-client login`. Reuse the existing one-sentence explanation of why these are `curl` rather than `sf-client`. State the trailing-slash difference once, where it bites, and link rather than restate the existing warning about `client_id` versus `audience`. The Authentik section carries D8's device-flow caveat as two steps that cannot be skipped -- the brand's device-code flow and the provider's `grant_types` -- and says what the person sees when either is missing, which step 8b observed: a bare 404 with an empty body, after they have already typed their password, with the reason only in the server's log. Add the same caveat to `### Discovery` in two sentences. Nothing in these sections may name a plan, a phase, a decision or a step: a pre-commit hook enforces that, and the fact that a walkthrough exists is not evidence of a plan. |
| 8f | medium | sonnet | none | D7: a short `## Logging in as a person` section in `docs/user_guide/authentication.md`, after `## Keys that came from somewhere else` (:97), saying a person runs `sf-client login`, that the cluster mints one key per namespace their identity entitles them to, that the keys are cached locally and `sf-client logout` discards them, and linking `https://github.com/shakenfist/client-python/blob/develop/docs/human-login.md` for the rest. No cache paths, no file modes, no precedence rules: those are the client's to state and a copy here is a copy to drift. Also add one sentence to `docs/developer_guide/api_reference/authentication.md:599`, which describes claim matching and is silent on an array claim: still true as written, because it constrains only what a rule may bind, but a reader comparing it with the developer guide would now find one of them less complete than the other. Check `docs/glossary.md` needs no new term (it has `identity token` and the federation vocabulary already) and say so either way. |
| 8g | medium | sonnet | none | D6: `tools/idp-lab/` holding a compose file with the tags 8a and 8b pinned, a setup script that configures both providers through their admin APIs so the captures can be regenerated, and a README saying what it is for, that it is a development lab and not a deployment, and how to regenerate the two fixture sets. No credential in the repository: the admin passwords are generated by the script or read from the environment, with a documented throwaway default, and `tools/gitleaks-scan.sh` must pass. Then close open question 12 in the master plan: mark it answered, naming the five documents it asked about and what each now says. |

Steps 8a and 8b both bind host ports and both run containers, so
they do not run concurrently. 8c depends on both captures; 8e
depends on both sets of notes.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The matching widening grants more than intended | The master plan already asks for high-effort review of this phase. 8d mutation-proves each property separately, the no-globbing and no-coercion tests stay as they are, and D2 keeps one comparison function. The review checks that `validate_bound_claims()` is untouched. |
| A long group list makes matching expensive | Membership is exact comparison over a list bounded by the token, and the token is bounded by the exchange's body limit before it is decoded. Worth a sentence in the developer guide; not worth a cap. |
| The walkthroughs rot as the providers change their admin UIs | Pinned image tags in `tools/idp-lab/`, the provider version named in each section, and the captures regenerable by one script. A walkthrough that cannot be re-run is the thing this mitigates. |
| The captures leak a real identity | 8a and 8b replace `sub`, `email` and `name` with placeholders, the lab's users are invented, and gitleaks runs over the result. The reviewer reads the two fixture sets rather than trusting the summary. |
| Authentik's setup is long enough to swamp its step | It is why 8b is `xhigh` and alone in its step. If it overruns, the device-code half is the part to defer to future work, not the group claim: the group claim is the phase's deliverable and the browser flow is enough to demonstrate it. |
| A reader configures `audience` from the Keycloak example and logins fail | The existing warning box is the authority and the walkthroughs link it rather than restating it. The definition of done checks that no fact about it is stated differently in two places. |

## Definition of done

* [ ] `claim_matches(['a', 'b'], 'b')` is `True`;
      `claim_matches(['a'], 'a')` is `True`;
      `claim_matches(['a'], 'b')` is `False`.
* [ ] `claim_matches(['shakenfist/evil'], 'shakenfist/*')` is
      `False`, and `claim_matches([1, 'a'], '1')` is `False`.
* [ ] `claim_matches([['a']], 'a')` is `False` and
      `claim_matches([], 'a')` is `False`.
* [ ] `test_non_string_claim_values_never_match` still asserts
      that 1, `True` and `None` never match, and
      `test_an_exact_matcher_compares_exactly`,
      `test_an_enumerated_matcher_accepts_any_alternative` and
      `test_there_is_no_globbing` pass **unmodified**.
* [ ] A rule binding `{"groups": "<group>"}` matches the real
      Keycloak claim set committed under
      `shakenfist/tests/files/idp/`, and matches the real
      Authentik one, each in its own test; a rule naming a group
      the captured identity is not in matches neither.
* [ ] The provenance a list claim produces holds only the
      elements that matched, asserted by a test that would fail
      if the whole array were recorded.
* [ ] Both captured discovery documents are committed, and a
      test asserts the discovery code accepts each and fills
      `authorization_endpoint`, `token_endpoint` and
      `device_authorization_endpoint` from it.
* [ ] Each captured discovery document carries a top-level
      `device_authorization_endpoint`, read with `json` rather
      than counted with `grep`, so finding 5 is pinned by the
      fixtures rather than by a sentence in this file. Step 8a
      proved the counting form wrong: Keycloak publishes the
      name twice, the second time inside
      `mtls_endpoint_aliases`.
* [ ] `docs/operator_guide/authentication.md` has a Keycloak
      section and an Authentik section, each naming the provider
      version it was produced against, and each containing the
      provider-side steps, the issuer `curl` with `client_id` and
      `oidc_scopes`, the rule `curl` binding a group claim, and
      the `sf-client login` the person runs.
* [ ] No fact about the trailing slash, `client_id` versus
      `audience`, or Authentik's device-flow prerequisite is
      stated differently in two places. Checked by reading all
      three, not asserted.
* [ ] `docs/user_guide/authentication.md` tells a person how to
      log in and links the client's own document.
* [ ] `tools/idp-lab/` brings both providers up on a host with
      Docker and nothing else, its README says how to regenerate
      both fixture sets, and it contains no credential:
      `tools/gitleaks-scan.sh` passes.
* [ ] Open question 12 in the master plan is marked answered,
      naming what each of its three audiences now reads.
* [ ] Every mutation in 8d is killed by a named test, recorded in
      this file's Results section. A surviving mutation is a
      failed definition of done, not a note.
* [ ] `pre-commit run --all-files` passes, which includes
      flake8, the unit tests, mypy, `check-doc-anchors`,
      `check-plan-phase-references` and `check-plan-status`.
* [ ] `git diff --stat develop... -- ':!docs/plans'` touches only
      `shakenfist/federation.py`,
      `shakenfist/external_api/auth.py`, files under
      `shakenfist/tests/`, the three guide documents under
      `docs/`, and files under `tools/idp-lab/`. No change to any
      daemon, to storage, or to the API's behaviour beyond what
      D1 widens.

## Checks run at planning time

Writing a check into a plan is not running it, so these were run
against the tree at `0b0271ef64`:

* `claim_matches()` was called with every input the definition of
  done names, through the module in this worktree. Every list
  case is `False` today, including `claim_matches(['a'], 'a')`;
  every scalar case already behaves as the definition of done
  requires it to keep behaving. So the list items discriminate,
  and the scalar items are a regression guard rather than new
  work.
* The four test names the definition of done refers to exist with
  those exact spellings, at `test_federation.py:640`, `:646`,
  `:650` and `:658`.
* `tools/check-plan-status.py`, `tools/check-doc-anchors.py` and
  `tools/check-plan-phase-references.py` pass with this file and
  the master plan's corrections in place. The last reports 396
  pre-existing advisory citations elsewhere in the tree and fails
  nothing.
* `tools/gitleaks-scan.sh` exists, so the credential item in the
  definition of done names a real check.
* `shakenfist/tests/files/` is where this repository keeps test
  data; there is no `fixtures` directory, and an earlier draft of
  this plan invented one.

## Back brief

Before executing any step, the implementing sub-agent back briefs
its understanding of this plan.

Two gates, both cheap now and expensive later:

1. **D3, before 8c starts.** Provenance recording the matched
   elements rather than the whole claim is a judgement about what
   an audit trail is for, and it is the one a reviewer is most
   likely to want argued. Changing it after 8c means changing a
   test, a docstring and the developer guide.
2. **D6, before 8g starts.** If the lab is not wanted in the
   repository, the captures still have to be regenerable
   somehow, and the walkthroughs lose their reproducibility
   claim. Better to know before the files are written.

Nothing here touches a live cluster. The providers run in
containers on the development host and are stopped when each step
finishes.

## Future work

* Phase 9's functional coverage, which is the only end-to-end
  test of these walkthroughs, and which should start from
  `tools/idp-lab/`.
* Whether a cluster should be able to tell that an advertised
  device endpoint is unusable (finding 5 and D8). The honest
  answers are a probe the discovery section's reasoning refuses,
  or an operator-settable override saying "advertise this issuer
  without the device flow". Neither is this phase's.
* `sf-client` verbs for issuers and mapping rules, so these
  walkthroughs can stop being `curl`: client-python#451.
* Patterns in claim matchers, with anchoring enforced at rule
  creation. Declined on the record in the developer guide and
  untouched by D1, which widens the claim side only.

## Execution notes

Execution began on 2026-11-22 (AEDT). The back brief was
answered before step 8a started:

* **D3 stands.** Provenance records only the elements of an
  array claim which matched, not the whole claim.
* **D6 stands.** The container lab lands in the repository, at
  `tools/idp-lab/`.
* **The containers were approved.** Both providers are
  configured for real on the development host, loopback only,
  and removed when each step finishes. No cluster is touched.
* **This session may push the branch and open the pull request**
  once the definition of done has run.

Departures from the plan are recorded here as they happen.

* **Step 8a corrected three things this plan had wrong**, at
  source rather than only here: `start-dev` serves http and
  generates no certificate, so the lab needs TLS before a
  capture is worth anything; a command line client wants a
  public registration rather than a confidential one; and the
  two flows produce the same claim set, so there is one capture
  rather than one per flow. The same TLS constraint was added
  to step 8b's row, which had not stated it.
* **Step 8b corrected the correction.** The TLS constraint this
  session added to its row after step 8a was wrong in shape for
  Authentik, which serves https out of the box -- but with a
  certificate carrying no `subjectAltName`, which fails the
  discovery fetch in a way no CA bundle setting repairs. The row
  now says that. Step 8b also found a second device-flow switch
  the plan did not know about, the provider's own `grant_types`,
  and established finding 5 in a stronger form than the survey
  claimed: the discovery document is byte for byte identical
  before and after the brand flow exists. D8 and step 8e's row
  were updated to match what was observed.
* **The credential scan runs locally after all.** The plan's
  definition of done names `tools/gitleaks-scan.sh`, and both
  provider steps reported that gitleaks is not installed on this
  host. It does not have to be: the scan was run with the same
  pinned binary the workflow downloads, passed to the script's
  own `--gitleaks` argument, and its positive control fired
  before it reported 7,244 commits clean. An item that can be
  run is worth more than an item deferred to CI.
* **The step table had a gap, and the step that found it closed
  it.** The definition of done requires a test proving the
  discovery code accepts both captured documents; no row
  assigned that work to anybody. Step 8c reported it rather than
  quietly leaving it, and was asked to do it. It landed in
  `shakenfist/tests/test_oidc_discovery.py` rather than beside
  the matching tests, which is where this session had pointed
  it: the only entry point is `oidc_discovery.fetch()`, whose
  fake session lives in that module, and importing one test
  module into another to reach it would have been worse. The
  departure is the right call and was kept.
* **A definition-of-done item was replaced rather than ticked.**
  It counted occurrences of `device_authorization_endpoint`
  with `grep -c` and expected 1. Keycloak publishes the name
  twice, the second time inside `mtls_endpoint_aliases`, so the
  check now reads the top-level key with `json`. Counting was
  the wrong instrument, and it would have failed on a correct
  fixture.

## Results

Filled in as the steps complete.

### Step 8a — Keycloak, observed

Produced against a real Keycloak run in Docker on the
development host, loopback only, and removed afterwards. Every
claim below was read off a token or a document, not remembered.
Nothing was committed; the two artefacts are left uncommitted
for the management session.

**Image and version.** `quay.io/keycloak/keycloak:26.8.2`,
digest
`sha256:7e66a96e4739239070aeaf72e4c5decf0f7f682cfc725b9f7ea2b336eaba38b3`,
image created 2026-11-19. The version string the server reports
on startup is:

```
Keycloak 26.8.2 on JVM (powered by Quarkus 3.40.1.1) started in 7.6s
```

and `GET /admin/serverinfo` gives `systemInfo.version` as
`26.8.2`. That is the string the walkthrough should name.

#### Two things the container needed that are easy to get wrong

**1. The admin bootstrap variables have been renamed.** The old
spellings still work on 26.8.2 but the server says so:

```
KC-SERVICES0110: Environment variable 'KEYCLOAK_ADMIN' is deprecated, use 'KC_BOOTSTRAP_ADMIN_USERNAME' instead
KC-SERVICES0110: Environment variable 'KEYCLOAK_ADMIN_PASSWORD' is deprecated, use 'KC_BOOTSTRAP_ADMIN_PASSWORD' instead
```

Observed by starting the image once with the old pair. Use
`KC_BOOTSTRAP_ADMIN_USERNAME` and `KC_BOOTSTRAP_ADMIN_PASSWORD`.
The log also calls the account it creates a *temporary* admin
(`KC-SERVICES0077: Created temporary admin user with username
admin`), which is 26.x behaviour and worth knowing before
somebody treats it as a permanent account.

**2. `start-dev` does not generate a self-signed certificate.**
A first attempt with `KC_HOSTNAME=https://localhost:8443` and
`--https-port=8443` but no certificate started and listened on
**http only**:

```
Listening on: http://0.0.0.0:8080
```

Shaken Fist refuses a non-`https` `issuer_url` and refuses a
document whose `jwks_uri` or endpoints are not `https`, so a
plaintext lab cannot produce a usable capture. A certificate has
to be supplied. The lab generated one:

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout tls.key -out tls.crt -days 2 -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'
```

With it mounted, the server listens on both:

```
Listening on: http://0.0.0.0:8080 and https://0.0.0.0:8443
```

Note that `KC_HTTP_ENABLED=false` was set and `start-dev` served
http anyway; only 8443 was published to the host, so nothing
reached the plaintext listener.

#### The container

Throwaway credentials were generated with `openssl rand -hex 16`
into an environment file outside the repository and are not
recorded here or anywhere in the tree. `${KC_ADMIN_USER}` was
`admin`.

```bash
docker run -d --name sf-idp-keycloak \
  -p 127.0.0.1:8443:8443 \
  -v "$PWD/tls.crt:/opt/keycloak/conf/tls.crt:ro" \
  -v "$PWD/tls.key:/opt/keycloak/conf/tls.key:ro" \
  -e KC_BOOTSTRAP_ADMIN_USERNAME="${KC_ADMIN_USER}" \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD="${KC_ADMIN_PASS}" \
  -e KC_HOSTNAME=https://localhost:8443 \
  -e KC_HTTP_ENABLED=false \
  -e KC_HTTPS_CERTIFICATE_FILE=/opt/keycloak/conf/tls.crt \
  -e KC_HTTPS_CERTIFICATE_KEY_FILE=/opt/keycloak/conf/tls.key \
  quay.io/keycloak/keycloak:26.8.2 start-dev
```

`KC_HOSTNAME` is what decides the issuer URL and every endpoint
in the discovery document, so it is the one setting that has to
be right before anything is captured.

#### The administrative steps, in order

Every step was done through the admin REST API so that it is
reproducible; the admin console path is given beside each one,
because that is what the walkthrough will describe. The token
below is a `master` realm `admin-cli` password grant:

```bash
curl -sS -k -X POST https://localhost:8443/realms/master/protocol/openid-connect/token \
  --data-urlencode 'client_id=admin-cli' \
  --data-urlencode 'grant_type=password' \
  --data-urlencode "username=${KC_ADMIN_USER}" \
  --data-urlencode "password=${KC_ADMIN_PASS}"
```

1. **Create the realm** `shakenfist`.
   Console: *Manage realms* -> *Create realm*.
   `POST /admin/realms` with `{"realm": "shakenfist",
   "enabled": true}` -> 201.
   The realm name is the last path element of the issuer URL,
   which is `https://<host>/realms/shakenfist`.

2. **Create the group** `sf-admins`.
   Console: *Groups* -> *Create group*.
   `POST /admin/realms/shakenfist/groups` with
   `{"name": "sf-admins"}` -> 201.

3. **Create the client** `shakenfist`, **public**.
   Console: *Clients* -> *Create client*, then *Client
   authentication* off.
   `POST /admin/realms/shakenfist/clients` -> 201 with:

   ```json
   {
     "clientId": "shakenfist",
     "name": "Shaken Fist command line client",
     "enabled": true,
     "publicClient": true,
     "standardFlowEnabled": true,
     "directAccessGrantsEnabled": false,
     "serviceAccountsEnabled": false,
     "redirectUris": ["http://127.0.0.1:*"],
     "webOrigins": [],
     "attributes": {"oauth2.device.authorization.grant.enabled": "true"}
   }
   ```

   **Public, not confidential**, because the thing registering is
   a command line program on a person's laptop. A confidential
   client's secret would have to be distributed to every person
   who logs in, at which point it is not a secret; the public
   client plus PKCE is what the loopback-redirect shape is for,
   and the device grant requires a public client anyway. There is
   also nothing for a secret to protect here: `POST /auth/login`
   verifies the token's signature and `client_id`, not a client
   secret.

   `redirectUris` is `http://127.0.0.1:*`. The command line
   client binds an **ephemeral** loopback port
   (`_RedirectServer((address, 0), ...)` in `client-python`'s
   `shakenfist_client/commandline/auth.py`), so the redirect URI
   it sends is `http://127.0.0.1:<random>/` and cannot be
   registered exactly. Keycloak's trailing `*` matched
   `http://127.0.0.1:45678/` in the real flow below. Plaintext
   `http` on a loopback literal is correct here and is what the
   device grant exists to avoid needing at all.

   `oauth2.device.authorization.grant.enabled` is the OAuth 2.0
   Device Authorization Grant. Console: the *OAuth 2.0 Device
   Authorization Grant* checkbox under *Authentication flow*.

4. **Add the group membership mapper**, which is the step this
   whole exercise turns on; see the subsection below.

5. **Create the person** and give them a password.
   Console: *Users* -> *Add user*, then *Credentials* -> *Set
   password* with *Temporary* off.
   `POST /admin/realms/shakenfist/users` -> 201 with username,
   `enabled`, `emailVerified`, `email`, `firstName`,
   `lastName` and a `"temporary": false` password credential.
   `emailVerified` and the non-temporary password are both
   deliberate: each of the alternatives puts a required action
   on the account, and the login then stops on an extra screen
   before any token is issued. That consequence was not
   exercised here -- the lab user was created this way from the
   start -- so if the walkthrough states it, state it as a
   reason to set the two fields rather than as an observation.

6. **Put the person in the group.**
   Console: *Users* -> the user -> *Groups* -> *Join Group*.
   `PUT /admin/realms/shakenfist/users/<user uuid>/groups/<group uuid>`
   -> 204. The two uuids come from
   `GET .../users?username=<name>&exact=true` and
   `GET .../groups?search=<name>`.

#### Getting a `groups` claim into the ID token

This is the most valuable note in this section, because three
separate things can each leave the claim out of the **identity**
token while it is present in the access token or at
`/userinfo`, and Shaken Fist only ever sees the identity token.

**The mapper.** Keycloak ships no `groups` client scope and no
group claim by default. What is needed is a protocol mapper of
type `oidc-group-membership-mapper` ("Group Membership" in the
console). The least surprising home for it is the client's own
dedicated scope -- console: *Clients* -> `shakenfist` -> *Client
scopes* -> `shakenfist-dedicated` -> *Add mapper* -> *By
configuration* -> *Group Membership*:

```bash
curl -sS -k -X POST \
  "https://localhost:8443/admin/realms/shakenfist/clients/<client uuid>/protocol-mappers/models" \
  -H "Authorization: Bearer ${TOKEN}" -H 'Content-Type: application/json' \
  -d '{"name": "groups",
       "protocol": "openid-connect",
       "protocolMapper": "oidc-group-membership-mapper",
       "config": {"claim.name": "groups",
                  "full.path": "true",
                  "id.token.claim": "true",
                  "access.token.claim": "true",
                  "userinfo.token.claim": "true",
                  "introspection.token.claim": "true"}}'
```

`"id.token.claim": "true"` is the member that puts it in the
identity token, and it is the one to check when the claim is
visible at `/userinfo` but a login is still refused. Console:
the *Add to ID token* switch.

**`full.path` decides whether the value is `/sf-admins` or
`sf-admins`, and the two paths disagree about its default.**
Both were observed:

| How the mapper was created | `groups` in the ID token |
|---|---|
| `config` with `"full.path": "true"` | `["/sf-admins"]` |
| `config` with `"full.path"` **absent** | `["sf-admins"]` |

So a mapper POSTed without the key behaves as if it were false.
But the key's declared default is `true`, which is what the
admin console renders and submits --
`GET /admin/serverinfo` reports, for
`oidc-group-membership-mapper`:

```
full.path             type= boolean default= 'true'
id.token.claim        type= boolean default= 'true'
access.token.claim    type= boolean default= 'true'
userinfo.token.claim  type= boolean default= 'true'
```

**An operator clicking through the console therefore gets the
full path, with a leading slash.** The captured fixture is the
full-path form and the string to bind is **`/sf-admins`**, with
the slash. A walkthrough that says "bind `sf-admins`" is wrong
for the console path, and one that says the opposite is wrong
for anybody scripting the API without the key. Both forms are
plain list elements, so either works once it is bound to the
string the token actually carries. Note also that the leading
slash is a *path*: a group nested under another is
`/parent/child`, which is the real reason to keep full paths on.

**`groups` is not a scope Keycloak knows.** Asking for it is a
hard refusal from the device endpoint:

```json
{"error": "invalid_scope", "error_description": "Invalid scopes: openid profile groups"}
```

(HTTP 400.) `scopes_supported` in a stock realm's discovery
document is `openid, organization, roles, email, offline_access,
microprofile-jwt, address, acr, web-origins, kc-admin-api,
service_account, profile, phone, basic` -- no `groups`. So the
issuer's `oidc_scopes` for this configuration must be
**`openid profile`**, not `openid profile groups`. The claim
arrives anyway, because a dedicated-scope mapper is always in
scope.

The alternative was tried and works, and is worth a sentence in
the walkthrough but not the main path: create a realm client
scope literally named `groups`
(`POST /admin/realms/shakenfist/client-scopes`, attributes
`{"include.in.token.scope": "true"}`), put the same mapper in
it, and assign it to the client. Then `groups` appears in
`scopes_supported` and `openid profile groups` is accepted.
Assigned as an **optional** client scope it is a trap:

| Requested scope | Granted scope in the token response | `groups` claim |
|---|---|---|
| `openid profile groups` | `openid groups email profile` | `["/sf-admins"]` |
| `openid profile` | `openid email profile` | **absent** |

An operator who sets up the optional scope and then leaves
`oidc_scopes` at its `openid` default gets no claim, no rule
match, and the deliberately uninformative 401. Assigning it as a
*default* client scope avoids that, at the cost of the claim
being in every token whether asked for or not.

#### Enabling the device grant

`oauth2.device.authorization.grant.enabled` on the client is the
whole of it. For Keycloak, unlike the other provider, there is
nothing at a brand or flow level to configure.

But that setting is **not** what puts the endpoint in the
discovery document. Checked on a second, empty realm whose only
clients were Keycloak's six built-ins, none of which has the
grant enabled -- the document still carried:

```
"device_authorization_endpoint": "https://localhost:8443/realms/emptyrealm/protocol/openid-connect/auth/device"
```

So the endpoint is advertised per realm while the grant is
enabled per client, and Shaken Fist's login metadata reads the
former. A Keycloak issuer is therefore advertised as
device-capable whether or not the registered client can use the
grant: the same shape as survey finding 5 describes for the
other provider, reached by a different route. The symptom when
the attribute is missing was observed too -- a public client in
that realm without it answered the device endpoint with a 400:

```json
{"error": "unauthorized_client", "error_description": "Client is not allowed to initiate OAuth 2.0 Device Authorization Grant. The flow is disabled for the client."}
```

That is a one-line check an operator can run before blaming the
cluster, and it is clearer than anything the cluster says.

**One client setting breaks the device grant and nothing else.**
Setting *Proof Key for Code Exchange Code Challenge Method* to
`S256` on the client (`pkce.code.challenge.method`) makes
Keycloak **require** a challenge on every request, including the
device authorization request, which has nowhere to put one:

```
POST .../protocol/openid-connect/auth/device   -> 400
{"error": "invalid_request", "error_description": "Missing parameter: code_challenge_method"}
```

Confirmed by setting the attribute, observing the 400, and
clearing it; with the attribute set, the authorization endpoint
answered 200 for the same client, so the breakage is specific to
the device grant. The command line client sends `S256` on the
authorization code flow of its own accord
(`PKCE_CHALLENGE_METHOD = 'S256'`), so leaving the client's PKCE
method **unset** gives PKCE on the browser flow and a working
device flow. The walkthrough should say to leave it empty and
say why, because "require PKCE" is exactly the box a careful
operator ticks.

#### Both logins, completed

Both flows were driven by hand with `curl` against the real
server, including the HTML login form and the consent page.

**Device code.** Worked.

```bash
# 1. the client asks for a code
curl -sS -k -X POST https://localhost:8443/realms/shakenfist/protocol/openid-connect/auth/device \
  --data-urlencode 'client_id=shakenfist' \
  --data-urlencode 'scope=openid profile'
# -> 200 {"device_code": "...", "user_code": "REXP-XPKO",
#         "verification_uri": "https://localhost:8443/realms/shakenfist/device",
#         "verification_uri_complete": ".../device?user_code=REXP-XPKO",
#         "expires_in": 600, "interval": 5}

# 2. the person's browser: the complete URI skips the code entry page and
#    lands on the login form; posting the form reaches a consent page
#    ("Yes"/"No", button name="accept"), and accepting it answers
#    "Device Login Successful".

# 3. the client polls
curl -sS -k -X POST https://localhost:8443/realms/shakenfist/protocol/openid-connect/token \
  --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:device_code' \
  --data-urlencode 'client_id=shakenfist' \
  --data-urlencode "device_code=${DEVICE_CODE}"
# -> 200 with access_token, refresh_token and id_token
```

What it required, beyond the client attribute: nothing at the
realm, the brand or the flows. The **consent page is not
optional** and is not the client's `consentRequired` setting --
it is the device grant's own `OAUTH_GRANT` required action, and
it appeared with `consentRequired` unset. A walkthrough that
tells a person "sign in and you are done" is wrong; they have to
press *Yes* afterwards.

**Authorization code with PKCE.** Worked, with a loopback
redirect URI on an ephemeral port, exactly as the command line
client builds it:

```bash
# 1. the authorization request (S256 challenge, no client secret)
curl -sS -k -G https://localhost:8443/realms/shakenfist/protocol/openid-connect/auth \
  --data-urlencode 'response_type=code' \
  --data-urlencode 'client_id=shakenfist' \
  --data-urlencode 'redirect_uri=http://127.0.0.1:45678/' \
  --data-urlencode "state=${STATE}" \
  --data-urlencode "code_challenge=${CHALLENGE}" \
  --data-urlencode 'code_challenge_method=S256' \
  --data-urlencode 'scope=openid profile'
# -> the login form; posting it answers 302 to
#    http://127.0.0.1:45678/?state=...&session_state=...&iss=...&code=...

# 2. the exchange
curl -sS -k -X POST https://localhost:8443/realms/shakenfist/protocol/openid-connect/token \
  --data-urlencode 'grant_type=authorization_code' \
  --data-urlencode 'client_id=shakenfist' \
  --data-urlencode "code=${CODE}" \
  --data-urlencode 'redirect_uri=http://127.0.0.1:45678/' \
  --data-urlencode "code_verifier=${VERIFIER}"
# -> 200 with an id_token
```

No consent page on this flow. The redirect carries an `iss`
parameter, because the realm advertises
`authorization_response_iss_parameter_supported: true`.

**The two flows produce the same claim set.** Compared member by
member: identical keys, and identical values for everything
except `at_hash`, `auth_time`, `exp`, `iat`, `jti` and `sid`,
which are per-token by construction. In particular `aud`, `azp`,
`iss`, `typ` and `groups` are the same. So one fixture is
enough, and it was taken from the device flow; there is no
second capture to make and nothing for step 8e to distinguish.

#### What the artefacts say

Left uncommitted in the worktree:

* `shakenfist/tests/files/idp/keycloak-discovery.json`
* `shakenfist/tests/files/idp/keycloak-id-token-claims.json`

The discovery document is 56 members, 6685 bytes as served
(under `FEDERATION_DISCOVERY_MAX_BYTES`, 65536). The only
difference between the committed file and the bytes the server
sent is JSON whitespace: `json.dump(..., indent=2)` preserving
the served key order, so that a reviewer can read it. Facts
taken from it:

* `"issuer": "https://localhost:8443/realms/shakenfist"` --
  **no trailing slash**, and the string is character for
  character the issuer URL. `KC_HOSTNAME` is what sets it.
* `jwks_uri` and all three login endpoints are `https`, which is
  what makes it usable as a fixture at all.
* `device_authorization_endpoint` is
  `.../protocol/openid-connect/auth/device`. **`grep -c
  device_authorization_endpoint` on this file is 2, not 1**: the
  second occurrence is inside `mtls_endpoint_aliases`, which
  Keycloak publishes and the other provider does not. Any
  mechanical count over the Keycloak fixture has to expect 2.
* Adding the optional `groups` client scope changed exactly one
  member of the document -- `scopes_supported` gained `groups`
  -- and nothing else. The committed capture is from before that
  experiment, so it matches the configuration described above.

The claim set is the decoded payload of a real identity token,
with the six person-identifying members replaced:

| Member | Value in the fixture |
|---|---|
| `sub` | `00000000-0000-0000-0000-000000000000` |
| `email` | `person@example.com` |
| `preferred_username` | `person` |
| `name` | `Example Person` |
| `given_name` | `Example` |
| `family_name` | `Person` |

Everything else is as emitted, including:

```json
{"aud": "shakenfist", "azp": "shakenfist", "typ": "ID",
 "iss": "https://localhost:8443/realms/shakenfist",
 "groups": ["/sf-admins"],
 "acr": "1", "email_verified": true,
 "exp": 1795313096, "iat": 1795312796, "auth_time": 1795312796}
```

Two things in there are evidence for claims the operator guide
already makes. `aud` is the **client id**, a bare string and not
a list, and it is not anything resembling an API URL -- which is
the warning box about `client_id` versus `audience`, confirmed
against a real token. And `groups` is a **JSON array** even for
a person in exactly one group, which is survey finding 1 as the
provider actually emits it: nothing about a single membership
makes it a scalar.

No signed JWT was written anywhere in the tree. The raw tokens
stayed in the scratch directory and went with it.

#### Teardown

```bash
docker rm -f sf-idp-keycloak
```

Nothing is left running. The image remains in the host's local
cache, which costs nothing and saves a pull in 8g.

#### Things the plan's row said that did not hold

* "dev mode is fine, no TLS needed because nothing validates it
  in this step" -- half right. Nothing validates the
  *certificate*, and the lab's is self-signed with `curl -k`.
  But the captures have to be `https` or they are not fixtures
  the discovery code would accept, and `start-dev` will not
  make a certificate for you.
* "a confidential-or-public client" -- public, for the reasons
  in step 3 above. There is no reason to document the
  confidential shape for a command line client.
* "`aud`, `iss`, `groups`, `exp` shapes intact" -- `azp`, `typ`
  and `iat` were kept as well, because `azp` is what a relying
  party checks when `aud` is multi-valued and `typ: "ID"` is how
  an identity token is told from an access token. Replacing any
  of them would have cost a test the ability to assert on them.
* The row names `shakenfist/tests/files/idp/keycloak-*.json`
  with "two files per flow". It is two files in total, not four:
  the flows agree, as recorded above.

### Step 8b — Authentik, observed

Produced against a real Authentik run in Docker on the development
host, loopback only, and removed afterwards. Every claim below was
read off a token, a document or a server log line, not remembered.
Nothing was committed; the two artefacts are left uncommitted for the
management session.

**Images and version.** `ghcr.io/goauthentik/server:2026.8.6`, digest
`sha256:da89d8338a3b1889800eb033fd61f8d43f7aa9a1c5a042cb5e03d891b1f0c56c`,
image created 2026-11-20. `GET /api/v3/admin/version/` reports
`{"version_current": "2026.8.6", ...}` and the server's first log line
is:

```
"event": "authentik is starting", "version": "2026.8.6"
```

That is the string the walkthrough should name. The two supporting
containers are `docker.io/library/postgres:16.16-alpine` and
`docker.io/library/redis:7.4.12-alpine`.

Authentik is **two** containers from the same image, `server` and
`worker`, plus those two. The `server` container applies the
migrations on first boot and that boot is slow -- it was waited for by
polling `/-/health/ready/` until it answered, not by sleeping.

#### Three things the containers needed that are easy to get wrong

**1. TLS is already there, and it is the wrong certificate.** Unlike
the other provider, Authentik needs no certificate supplied to serve
https: it generates one on first boot and listens on 9443 with it.

```
subject=O=authentik, CN=authentik default certificate
issuer=O=authentik, CN=authentik default certificate
```

That is enough for a capture made with `curl -k`. It is **not** enough
for a cluster, and the failure is worth stating because it is not the
one an operator expects. The certificate carries **no
subjectAltName**, so Python's TLS refuses it by hostname even when it
is itself the trust anchor. Pointing `FEDERATION_JWKS_CA_BUNDLE` at
that very certificate and calling `oidc_discovery.fetch()` gave:

```
OIDCDiscoveryFailed: could not fetch
https://localhost:9443/application/o/sf/.well-known/openid-configuration:
SSLError: ... [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
Hostname mismatch, certificate is not valid for 'localhost'.
```

"Trusting the certificate" does not fix this; only replacing it does.
A certificate with a SAN was generated, uploaded as a keypair
(`POST /api/v3/crypto/certificatekeypairs/` with `certificate_data`
and `key_data`), and set as the brand's **Web certificate**
(`PATCH /api/v3/core/brands/<uuid>/` with `web_certificate`). The
running server picked it up within seconds with no restart. With that
done, `oidc_discovery.fetch()` on the live provider **succeeded**:

```
ACCEPTED. members: 5
  issuer = https://localhost:9443/application/o/sf/
  authorization_endpoint = https://localhost:9443/application/o/authorize/
  token_endpoint = https://localhost:9443/application/o/token/
  device_authorization_endpoint = https://localhost:9443/application/o/device/
  jwks_uri = https://localhost:9443/application/o/sf/jwks/
```

So the discovery half of this phase is proved against the live
provider and not only against the fixture. The lab's certificate was
made the same way step 8a's was, with
`-addext 'subjectAltName=DNS:localhost,IP:127.0.0.1'`.

**2. The bootstrap variables create an API token as well as a
password.** `AUTHENTIK_BOOTSTRAP_PASSWORD`,
`AUTHENTIK_BOOTSTRAP_TOKEN` and `AUTHENTIK_BOOTSTRAP_EMAIL` are read
on first boot. The token is what makes the whole configuration
scriptable -- it is a bearer token for `/api/v3/` from the first
second, with no initial-setup flow to click through. This is the
single biggest difference from the other provider's setup, where an
admin password grant has to be exchanged for a token first.

**3. The issuer URL is decided by the request's `Host` header**, not
by a configuration setting. There is no `KC_HOSTNAME` equivalent here:
the document fetched through `localhost:9443` names
`https://localhost:9443/...` as its issuer. An operator behind a
reverse proxy therefore gets the issuer their proxy presents, which is
right, but it also means a capture taken through the wrong name is
silently wrong.

#### The containers

Throwaway credentials were generated with `openssl rand -hex` into an
environment file outside the repository and are not recorded here or
anywhere in the tree.

```bash
docker network create sf-idp-net

docker run -d --name sf-idp-postgres --network sf-idp-net \
  -e POSTGRES_USER=authentik -e POSTGRES_DB=authentik \
  -e POSTGRES_PASSWORD="${PG_PASS}" \
  docker.io/library/postgres:16.16-alpine

docker run -d --name sf-idp-redis --network sf-idp-net \
  docker.io/library/redis:7.4.12-alpine

# the same environment for both authentik containers
#   AUTHENTIK_SECRET_KEY, AUTHENTIK_POSTGRESQL__{HOST,USER,NAME,PASSWORD},
#   AUTHENTIK_REDIS__HOST, AUTHENTIK_BOOTSTRAP_{PASSWORD,TOKEN,EMAIL}
docker run -d --name sf-idp-authentik-server --network sf-idp-net \
  -p 127.0.0.1:9443:9443 "${common_env[@]}" \
  ghcr.io/goauthentik/server:2026.8.6 server

docker run -d --name sf-idp-authentik-worker --network sf-idp-net \
  "${common_env[@]}" ghcr.io/goauthentik/server:2026.8.6 worker
```

Only 9443 is published, and only on loopback. Readiness was waited for
with `until curl -ksf https://127.0.0.1:9443/-/health/ready/`.

#### The administrative steps, in order

Every step was done through the API with the bootstrap token so that
it is reproducible; the admin interface path is given beside each one,
because that is what the walkthrough will describe.

1. **Create the group** `sf-admins`.
   Interface: *Directory* -> *Groups* -> *Create*.
   `POST /api/v3/core/groups/` with `{"name": "sf-admins"}` -> 201.
   The group's **name** is what reaches the token; there is no path
   and no uuid in the claim.

2. **Create the person and put them in the group in one call.**
   Interface: *Directory* -> *Users* -> *Create*.
   `POST /api/v3/core/users/` -> 201 with `username`, `name`,
   `email`, `"is_active": true`, `"type": "internal"` and
   `"groups": ["<group uuid>"]`.

3. **Set their password.**
   Interface: the user's *...* menu -> *Set password*.
   `POST /api/v3/core/users/<pk>/set_password/` -> 204.

4. **Create the OAuth2/OpenID provider.** Interface: *Applications*
   -> *Providers* -> *Create* -> *OAuth2/OpenID Provider*.
   `POST /api/v3/providers/oauth2/` -> 201 with:

   ```json
   {
     "name": "shakenfist",
     "authorization_flow": "<default-provider-authorization-implicit-consent>",
     "invalidation_flow": "<default-provider-invalidation-flow>",
     "client_type": "public",
     "client_id": "shakenfist",
     "grant_types": ["authorization_code", "refresh_token",
                     "urn:ietf:params:oauth:grant-type:device_code"],
     "include_claims_in_id_token": true,
     "signing_key": "<a certificate keypair uuid>",
     "sub_mode": "hashed_user_id",
     "issuer_mode": "per_provider",
     "redirect_uris": [{"matching_mode": "regex",
                        "url": "http://127\\.0\\.0\\.1:[0-9]+/",
                        "redirect_uri_type": "authorization"}],
     "property_mappings": ["<openid>", "<profile>", "<email>"]
   }
   ```

   `authorization_flow` and `invalidation_flow` are **required** and
   have no defaults; the two built-in flows above are what a stock
   install ships. `signing_key` has no default either, and without one
   there is nothing to sign an RS256 identity token with. The
   self-signed certificate Authentik generates on first boot is a
   perfectly good signing key even though it is a poor web
   certificate, and that is what was used.

   The three settings whose API default is **not** what a working
   configuration needs, observed by creating a second provider with
   none of them set:

   | Field | API default | What it needs to be |
   |---|---|---|
   | `grant_types` | `[]` | must list the device code grant |
   | `property_mappings` | `[]` | must include `profile` for `groups` |
   | `signing_key` | `null` | a certificate keypair |

   `include_claims_in_id_token` defaults to `true`, which is the one
   that matters most and the one that is already right. `sub_mode` and
   `issuer_mode` default to `hashed_user_id` and `per_provider`, both
   of which are what is wanted.

5. **Create the application** and bind the provider.
   Interface: *Applications* -> *Applications* -> *Create*.
   `POST /api/v3/core/applications/` with
   `{"name": "Shaken Fist", "slug": "sf", "provider": <pk>}` -> 201.
   **The slug is the issuer URL's last path element.** The slugs
   `authorize`, `token`, `device`, `userinfo`, `introspect` and
   `revoke` are reserved, because the global endpoints live at those
   paths.

#### Getting a `groups` claim into the ID token

**The default OpenID `profile` mapping carries it. No custom scope
mapping was needed.** This is the opposite of the other provider,
which ships no group claim at all.

`GET /api/v3/propertymappings/provider/scope/<profile uuid>/` returns,
verbatim, the shipped expression:

```python
avatar = request.user.avatar
return delete_none_values({
    "name": request.user.name,
    "given_name": ak_obj_attr(request.user, "given_name", "name"),
    "family_name": ak_obj_attr(request.user, "family_name"),
    "preferred_username": request.user.username,
    "nickname": request.user.username,
    "groups": [group.name for group in request.user.groups.all()],
    # Only expose URL-based avatars: generated avatars are inline base64
    # SVG data URIs, which would bloat the ID/access token for every
    # user without a real avatar URL (OIDC expects a URL here).
    "picture": avatar if avatar and not avatar.startswith("data:") else None,
})
```

`[group.name for group in request.user.groups.all()]` is the whole of
it: **group names, as a JSON array, with no path and no uuid**. A real
identity token carried `"groups": ["sf-admins"]`.

Two conditions have to hold as well as the mapping being assigned, and
both were checked by driving real logins:

**The provider's *Include claims in id_token* must be on.** It is on
by default. When it is off, the scope mappings' claims are served from
`/userinfo` and from the access token but not from the identity token,
which is the only token Shaken Fist ever sees.

**The `profile` scope must actually be granted**, which decides what
the issuer's `oidc_scopes` has to be. Four requests, same provider,
same person:

| Requested scope | Granted scope | `groups` claim |
|---|---|---|
| `openid profile email` | `profile email openid` | `["sf-admins"]` |
| `openid profile` | `profile openid` | `["sf-admins"]` |
| `openid` | `openid` | **absent** |
| *(no scope parameter at all)* | `profile email openid` | `["sf-admins"]` |

So `oidc_scopes` must be at least **`openid profile`**. The last row
is Authentik's documented behaviour -- a request naming no scopes is
treated as requesting every scope the provider has configured -- and
it is a trap of its own, because it makes a hand-rolled test pass
while the real client, which always names its scopes, fails.

Unlike the other provider, `groups` is not a scope here and asking for
one would be wrong: the claim rides on `profile`.

#### The device flow, and what survey finding 5 got right

**Finding 5 held, and the evidence is stronger than the finding
claims.** It said Authentik advertises `device_authorization_endpoint`
whether or not a brand device-code flow exists. Both states were
captured.

**Before any brand flow was configured** (`flow_device_code` is `null`
on a stock default brand, confirmed by reading
`GET /api/v3/core/brands/`), the discovery document carried:

```
"device_authorization_endpoint": "https://localhost:9443/application/o/device/"
"grant_types_supported": [..., "urn:ietf:params:oauth:grant-type:device_code"]
```

A device login was then driven by hand, and it fails in the worst
possible way -- not at the first step, but at the person's browser,
after they have already typed their password:

1. `POST /application/o/device/` answers **200** with a real
   `device_code`, `user_code`, `verification_uri` and
   `verification_uri_complete`. Nothing here hints at a problem.
2. The person opens `verification_uri_complete`. Unauthenticated, it
   **302**s to the brand's authentication flow, so they log in
   normally.
3. Back at `/device?code=<user_code>`, authenticated, it answers a
   bare **404 with an empty body**. No message, no error page content.
   The server log says what the browser does not:

   ```
   "event": "Brand has no device code flow configured",
   "logger": "authentik.providers.oauth2.views.device_init",
   "status": 404
   ```

4. The client's token poll keeps answering `authorization_pending`
   until the code expires. The default `expires_in` is **60 seconds**,
   so the whole thing is over in a minute with nothing said.

**After configuring the brand flow**, the same login completed. Two
API calls:

```bash
# a flow with the stage configuration designation, and no stages at all
POST /api/v3/flows/instances/
  {"name": "default-device-code-flow", "title": "Device code flow",
   "slug": "default-device-code-flow", "designation": "stage_configuration",
   "authentication": "require_authenticated"}

# point the default brand at it
PATCH /api/v3/core/brands/<uuid>/ {"flow_device_code": "<flow uuid>"}
```

Interface: *Flows and Stages* -> *Flows* -> *Create*, then *System* ->
*Brands* -> edit the default brand -> *Default code flow*. The flow
was created with **no stages bound to it** and that is enough; nothing
had to be added to it.

**And here is the part that makes the caveat worth documenting: the
discovery document did not change by one byte.** The capture taken
before the brand flow existed and the capture taken after are
identical -- same 2188 bytes, same members, compared as parsed JSON
and found equal. Nothing a relying party can read distinguishes a
working Authentik from a broken one. That is a stronger statement than
"the endpoint is advertised anyway", and it is what the operator guide
should say.

A third state was captured for the same reason. A provider created
with the API's default `grant_types` of `[]` -- no device grant at all
-- still produced a discovery document advertising
`device_authorization_endpoint` **and** listing the device grant in
`grant_types_supported`. So that member is static per provider too,
and cannot be used as a second opinion. The device endpoint's refusal
for such a provider is actively misleading:

```json
{"error": "invalid_client", "error_description": "Client authentication failed (e.g., unknown client, no client authentication included, or unsupported authentication method)"}
```

(HTTP 400.) Nothing in that says "the device grant is not enabled on
this provider", and it arrives at the *client*, not the person. The
equivalent message at the other provider names the actual cause.

So there are **two** independent switches, either of which breaks a
device login while discovery looks perfect: the provider's
`grant_types`, and the brand's `flow_device_code`. The walkthrough has
to name both.

#### Both logins, completed

Both flows were driven end to end against the real server, through
Authentik's flow executor API (`/api/v3/flows/executor/<slug>/`), which
is what the browser interface itself posts to.

**Device code.** Worked, once the brand flow existed. From a browser
with no session, which is the real case:

```
POST /application/o/device/                       -> 200, user_code + URIs
GET  /device?code=<user_code>                     -> 302 /flows/-/default/authentication/?next=...
     (the person logs in: identification, then password)
GET  /device?code=<user_code>                     -> 302 /if/flow/<authorization flow>/?code=<user_code>
     stage: ak-provider-oauth2-device-code-finish  (one button)
POST /application/o/token/ grant_type=...device_code -> 200 with an id_token
```

Two things to tell a person. The code is **pre-filled** when they use
`verification_uri_complete`, so the code-entry stage never appears --
but the brand flow is still what makes the route exist, which is why
it cannot be skipped. And there is a **confirmation stage they must
press**, `ak-provider-oauth2-device-code-finish`, even though the
provider's authorization flow was the *implicit consent* one. It is
the device grant's own step, not the consent setting, which is exactly
the same shape the other provider has.

**Authorization code with PKCE.** Worked, with an S256 challenge, no
client secret, and a loopback redirect on an ephemeral port:

```bash
GET /application/o/authorize/?response_type=code&client_id=shakenfist
    &redirect_uri=http://127.0.0.1:45678/&state=...
    &code_challenge=...&code_challenge_method=S256&scope=openid+profile+email
# -> 302 http://127.0.0.1:45678/?code=...&state=...
POST /application/o/token/ grant_type=authorization_code
    client_id=shakenfist code=... redirect_uri=http://127.0.0.1:45678/
    code_verifier=...
# -> 200 with an id_token
```

No consent page on this flow. The redirect carries `code` and `state`
and **no `iss` parameter**, which the other provider does send.

**What Authentik accepts for a wildcard port.** This is a real
walkthrough question and the answer is not the obvious one.
`redirect_uris` is a list of objects, not of strings:

```json
[{"matching_mode": "regex",
  "url": "http://127\\.0\\.0\\.1:[0-9]+/",
  "redirect_uri_type": "authorization"}]
```

`matching_mode` is `strict` or `regex`. A `regex` entry is what
matches an ephemeral port; it was proved against two different ports,
45678 and 51237, both of which completed.

The trap is that the other provider's form, `http://127.0.0.1:*/`,
is **accepted by the API as a `strict` entry and then matches
nothing**. With it configured, the authorization request answers
**400** with an HTML error page -- correctly not a redirect, since the
redirect URI is the thing in doubt -- and the log says:

```
"cause": "redirect_uri_no_match",
"event": "The request fails due to a missing, invalid, or mismatching redirection URI (redirect_uri)."
```

Note also that the regex is a regex: the literal dots have to be
escaped or they match any character. Authentik's own documentation
says so and it is easy to copy a Keycloak-shaped value straight past
it.

**The two flows produce the same claim set.** Compared member by
member: identical keys, and identical values for everything except
`auth_time`, `exp`, `iat`, `jti` and `sid`, which are per-token by
construction. `aud`, `acr`, `amr`, `iss` and `groups` are the same. So
one fixture is enough, and it was taken from the device flow, as step
8a's was.

#### What the artefacts say

Left uncommitted in the worktree:

* `shakenfist/tests/files/idp/authentik-discovery.json`
* `shakenfist/tests/files/idp/authentik-id-token-claims.json`

The discovery document is 26 members, 2188 bytes as served -- a third
of the other provider's, and well under
`FEDERATION_DISCOVERY_MAX_BYTES`. The only difference between the
committed file and the bytes the server sent is JSON whitespace:
`json.dump(..., indent=2)` preserving the served key order. Facts
taken from it:

* `"issuer": "https://localhost:9443/application/o/sf/"` -- **with a
  trailing slash**, and that string is what `issuer_url` must be,
  character for character. Confirmed from the committed file and
  confirmed again by `oidc_discovery.fetch()` accepting the live
  document for exactly that issuer. `discovery_url()` strips the one
  slash and fetches
  `.../application/o/sf/.well-known/openid-configuration`, which is
  the path Authentik serves.
* The issuer's path ends in the **application** slug, not the
  provider's name. The endpoints do not: `authorization_endpoint`,
  `token_endpoint`, `device_authorization_endpoint`,
  `userinfo_endpoint`, `introspection_endpoint` and
  `revocation_endpoint` are all global paths under
  `/application/o/`. Only `jwks_uri` and `end_session_endpoint` carry
  the slug. So an operator reading the document cannot tell which
  application an endpoint belongs to, and the issuer is the only thing
  that is per-application.
* `jwks_uri` and all three login endpoints are `https`.
* **`grep -c device_authorization_endpoint` on this file is 1.** There
  is no `mtls_endpoint_aliases` member, so the count differs from the
  other provider's fixture, which is 2. Neither count is a safe test;
  read the top-level key with `json`.
* `token_endpoint_auth_methods_supported` is `["client_secret_post",
  "client_secret_basic"]` and does **not** include `none`, even though
  this provider is a public client and its token requests carry no
  secret and succeed. The member is static; do not read anything into
  it.
* `scopes_supported` is `["openid", "email", "profile"]`, which *is*
  derived from the provider's assigned mappings -- the probe provider
  with no mappings published `["openid"]` alone.
* `claims_supported` lists `groups`, which is the one place in the
  document where the group claim is visible at all.

The claim set is the decoded payload of a real identity token. Only
one member identified the lab person rather than already naming an
invented placeholder, and it was replaced:

| Member | Value in the fixture | Why |
|---|---|---|
| `sub` | `00000000-0000-0000-0000-000000000000` | was a hash of the user id; mirrors step 8a |
| `email` | `person@example.com` | as emitted; the lab person was created with step 8a's placeholder |
| `preferred_username` | `person` | as emitted |
| `nickname` | `person` | as emitted; Authentik copies the username |
| `name` | `Example Person` | as emitted |
| `given_name` | `Example Person` | as emitted -- see below |

`given_name` is deliberately **not** `Example`, which is what the
other provider's fixture carries. Authentik's shipped expression is
`ak_obj_attr(request.user, "given_name", "name")`, so with no
`given_name` attribute set on the person it falls back to the full
name; and `family_name` is absent entirely, because the expression
wraps everything in `delete_none_values()`. Writing `Example` and
`Person` into the fixture would have made the two files read alike at
the cost of hiding a real difference between the providers.

Everything else is as emitted, including:

```json
{"aud": "shakenfist", "typ": absent, "azp": absent,
 "iss": "https://localhost:9443/application/o/sf/",
 "groups": ["sf-admins"],
 "acr": "goauthentik.io/providers/oauth2/default", "amr": ["pwd"],
 "email_verified": false,
 "exp": 1795318462, "iat": 1795314862, "auth_time": 1795314861}
```

Four things in there are evidence for claims the operator guide makes
or will make:

* `aud` is the **client id**, a bare string and not a list, exactly as
  the other provider emits it. The `client_id`-versus-`audience`
  warning box is confirmed against a second real provider.
* `groups` is a **JSON array** for a person in exactly one group. Both
  providers agree, which is survey finding 1 as both of them actually
  behave.
* There is **no `azp` and no `typ`**. The other provider sends both,
  and step 8a kept them in its fixture deliberately. A test written
  against the Keycloak fixture that asserts on `typ: "ID"` will not
  port to this one.
* `email_verified` is **`false`**, because nothing verified it. It is
  as emitted and was not touched.

No signed JWT was written anywhere in the tree. The raw tokens stayed
in the scratch directory and went with it.

#### Teardown

```bash
docker rm -f sf-idp-authentik-server sf-idp-authentik-worker \
             sf-idp-postgres sf-idp-redis
docker network rm sf-idp-net
```

Nothing is left running. The three images remain in the host's local
cache, which costs nothing and saves a pull in 8g.

#### Things the plan's row said that did not hold

* "It needs TLS for the same reason step 8a did." True in the end but
  not in the way the row means: Authentik serves https out of the box,
  so `curl -k` captures work immediately and no certificate has to be
  supplied for *this* step. What the certificate is needed for is a
  cluster, because the built-in one has no subjectAltName and
  `FEDERATION_JWKS_CA_BUNDLE` cannot rescue a hostname mismatch. That
  distinction is worth the walkthrough's time; the row's version is
  not.
* "the default OpenID `profile` mapping, or a custom scope mapping if
  that is what it takes -- record which". The default mapping, with no
  change. The thing that actually needs recording is not the mapping
  but the **scope**: `oidc_scopes` must include `profile` or the claim
  is absent, and that is the Authentik analogue of the other
  provider's optional-scope trap.
* "Configure a device-code flow at the **brand**, which is not the
  default". Correct, and it is only half the configuration -- the
  provider's `grant_types` must list the device grant too, and its API
  default is the empty list. The row names one switch where there are
  two.
* The row implies a `groups` claim might need work. It needs none. The
  work on this provider is in the three fields whose API defaults are
  empty, and in the redirect URI's matching mode.

### Step 8d — mutation testing

Every property D1, D2 and D3 claim was broken on purpose, one at a
time, against the matching change of step 8c (`3a36d68549`). **14
mutations: 12 behaviour-changing ones, all killed, and 2 proven
semantic no-ops which survive by construction.** Two of the twelve
survived on the first round and are the two most informative rows
below: each was a real gap in the tests, and each is now closed.

The harness is `run-mutants.sh` in this session's scratchpad, beside
`mutants.yaml` (the twelve), `mutants-noops.yaml` (the two), and
`pristine/` holding the three files step 8c touched. It drives
`~/bin/mutation-check`, which applies one mutant at a time, gives the
file an mtime it has never had so no stale `.pyc` is trusted, runs the
command in its own process group under a 300s timeout, and always puts
the file back. `./run-mutants.sh --all` re-runs the whole set in about
three minutes. The test command is both modules in full, not just the
matching tests:

```
python -B -m testtools.run shakenfist.tests.test_federation        (96 tests)
python -B -m testtools.run shakenfist.tests.test_oidc_discovery    (34 tests)
```

so a mutant that breaks `MatchingRulesTestCase`, the token exchange or
the captured discovery documents is seen rather than missed. All
`ClaimMatchingTestCase` names below are in `shakenfist/tests/
test_federation.py`.

#### The twelve that change behaviour

| # | What was changed in `shakenfist/federation.py` | Killed by |
| --- | --- | --- |
| M1 | `if candidate in alternatives` → `if any(alternative in candidate for alternative in alternatives)`, so a matcher becomes a substring match | `test_there_is_no_globbing`, `test_there_is_no_globbing_in_a_list_claim`, `test_an_exact_matcher_compares_exactly` |
| M2 | `[element for element in value if isinstance(element, str)]` → `[str(element) for element in value]` | `test_a_non_string_element_is_skipped_not_stringified` |
| M3 | the same comprehension → `[matched for element in value for matched in matched_claim_values(element, matcher)]`, so the list branch recurses | `test_a_list_inside_a_list_is_not_descended_into` |
| M4 | the final return → `if any(candidate in alternatives for candidate in candidates): return candidates`, the whole claim rather than what matched | `test_only_the_matched_elements_reach_provenance`, `test_the_matched_values_are_the_ones_that_matched` |
| M5 | `candidates = [value]` → `return [value]` in the scalar branch, so every scalar claim matches | 8 tests, including `test_an_exact_matcher_compares_exactly` and `MatchingRulesTestCase.test_a_non_matching_rule_does_not_hide_a_matching_one` |
| M6 | the final return → `return candidates`, so every bound claim matches | 15 tests, including both captured-identity tests |
| M7b | `[alternative for alternative in matcher if isinstance(alternative, str)]` → `[str(alternative) for alternative in matcher]` | `test_a_non_string_alternative_is_skipped_not_stringified` (**new**, see below) |
| M8a | the final return wrapped in `sorted(...)` | `test_the_matched_values_are_the_ones_that_matched` (**after** a new assertion, see below) |
| M8b | the final return wrapped in `list(reversed(...))` | `test_the_matched_values_are_the_ones_that_matched` |
| M9 | `satisfied[claim] = (matched if isinstance(claims[claim], list) else claims[claim])` → `satisfied[claim] = claims[claim]` | `test_only_the_matched_elements_reach_provenance` |
| M10 | the same line with the `isinstance` test inverted | `test_matching_returns_the_satisfied_claims`, `test_only_the_matched_elements_reach_provenance`, `MatchingRulesTestCase.test_the_satisfied_claims_are_the_tokens_values` |
| M11b | `else: return []` → `else: candidates = [str(value)]` in the value branch | `test_non_string_claim_values_never_match` |

Each failure named the property it was aimed at. The two worth
remarking on are M1, which is caught by the scalar no-globbing test as
well as the list one -- the right answer, because D1 widened the claim
side and must not have moved the scalar side -- and M10, which is
caught by the *scalar* provenance tests, because inverting the test
starts recording `['shakenfist/ryll']` where a string was recorded
before. D3 is pinned from both directions.

#### M7b: a missing test, found by re-aiming a no-op

The mutant the row did not ask for and should have: drop the
`isinstance` check from the matcher-side filter
(`alternatives = list(matcher)`). It **survived**, and no test could
have killed it, because it is a semantic no-op -- a candidate is
always a `str`, `in` on a list compares with `==`, and a `str` never
equals an `int`, a `bool`, `None` or a list. The filter on that side
is defence in depth against a damaged row, not a behaviour.

Re-aimed one-sidedly as M7b -- coerce with `str()` rather than skip --
it was a real behaviour change: a claim of `'1'` would satisfy a
matcher of `[1]`. That survived too, which is a missing test and not a
note: the module's "no coercion" promise was pinned on the claim side
and not on the matcher side, which step 8c rewrote. Closed with
`test_a_non_string_alternative_is_skipped_not_stringified`. Rule
creation refuses to write such a matcher
(`mapping_rule.validate_bound_claims()`), so this is the damaged-row
path, which is exactly where a coercion would be unobserved.

#### M8a: the order assertion could not see sorting

`test_the_matched_values_are_the_ones_that_matched` asserted
`['b', 'c']` from `matched_claim_values(['a', 'b', 'c'], ['c', 'b'])`.
That distinguishes the claim's order from the matcher's, which is what
it was written for, but its expected value is also the sorted one, so
`sorted()` **survived** while `reversed()` was killed. The fix is in
the test, not the code: a second assertion whose expected order is
neither sorted nor the matcher's,
`matched_claim_values(['c', 'b'], ['a', 'b', 'c']) == ['c', 'b']`.
D3's "in the order the token listed them" is now actually tested.

#### The two no-ops, and why they stay in the tree

| # | What was changed | Verdict |
| --- | --- | --- |
| M7 | the matcher-side `isinstance` filter dropped (`alternatives = list(matcher)`) | SURVIVED, semantic no-op |
| M11 | `else: return []` → `else: candidates = [value]` for a value which is neither string nor list | SURVIVED, semantic no-op |

Both survive for the same reason, and neither is a missing test: the
final equality filter already excludes a non-`str`, so neither guard
can change an outcome. They are kept because they keep the declared
`list[str]` return honest and say what the function means; the
behaviour each is standing in for is mutated one-sidedly by M7b and
M11b, and both of those are killed. They live in
`mutants-noops.yaml` rather than `mutants.yaml` so that the killing
set exits 0, and so a later round can confirm they are still no-ops
rather than rediscovering it.

This is the one place the definition of done needs reading rather than
applying: "a surviving mutation is a failed definition of done, not a
note" is right about a mutation which changes behaviour and wrong
about one which cannot. Read it as *every behaviour-changing mutation
is killed by a named test*, which is what the twelve above are. The
two no-ops are recorded rather than excused: each was re-aimed until
it did change behaviour, and the re-aimed form is in the killed set.

#### What this step did not do

* The discovery code was not mutated. 8d's row scopes it to the
  matching change, and `CapturedProviderDocumentTestCase` was run
  against all 14 mutants rather than mutated against. The
  acceptance those two tests pin is a property of
  `shakenfist/oidc_discovery.py`, which step 8c did not touch, so a
  mutant there would mostly be caught by tests written in earlier
  phases.
* Nothing was committed. `git status --short` after this step shows
  this plan file and `shakenfist/tests/test_federation.py`, whose
  diff is the one new test and the one new assertion described above;
  `shakenfist/federation.py` and
  `shakenfist/tests/test_oidc_discovery.py` are byte-identical to
  `HEAD` and to the pristine copies taken before the first mutant.
  `flake8` is clean and all 96 tests in `test_federation` and 34 in
  `test_oidc_discovery` pass.
