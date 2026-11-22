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
does not work. This phase documents it: in the Authentik
walkthrough as a step the operator must not skip, and in the
discovery section as a caveat about what the endpoint list
proves. It does not add a probe, a health check or a
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
| 8b | xhigh | opus | none | The same for Authentik (`ghcr.io/goauthentik/server`, pinned; it needs a postgres and a redis, which the compose file in 8g will carry). It needs TLS for the same reason step 8a did, which that step found the hard way: a capture whose issuer or endpoints are `http` is not a fixture the discovery code would accept. Create an OAuth2/OpenID provider and an application, note the issuer URL **including its trailing slash**, and arrange a `groups` claim in the ID token (the default OpenID `profile` mapping, or a custom scope mapping if that is what it takes -- record which). Configure a device-code flow at the **brand**, which is not the default, and verify both that the discovery document advertises `device_authorization_endpoint` and that a device login actually completes; if it does not complete without the brand flow, capture that failure's symptom too, because finding 5 says that is the default experience and the walkthrough must name it. Capture the same artefacts under `shakenfist/tests/files/idp/authentik-*.json`, same placeholder rules. Notes, not prose. |
| 8c | high | opus | none | Implement D1, D2 and D3 in `shakenfist/federation.py`. `claim_matches(value, matcher)` keeps its signature and meaning; add `matched_claim_values(value, matcher) -> list[str]` returning the values which matched (`[value]` for a scalar hit, the matching elements for a list, `[]` for no match) and make `claim_matches()` return `bool()` of it. `match_claims()` (:432) records `matched_claim_values()` for a list claim and the token's value for a scalar, so provenance keeps its current shape where nothing changed. Amend `test_non_string_claim_values_never_match` rather than deleting it: 1, True and None must still never match, and move the `['a']` assertion into a new test which asserts membership works -- the comment on the old test argues coercion and does not cover lists, which finding 1 explains. Add tests for: a matching element among several, no matching element, a non-string element skipped alongside a matching string, a nested list refused, an empty list refused, a list against a matcher list (both the hit and the miss), and provenance recording only the matched elements. Then add an end-to-end rule-matching test for each provider, reading the real claim sets 8a and 8b captured, asserting a `{"groups": "<the group>"}` rule matches and a rule naming a group the person is not in does not. Update `docs/developer_guide/authentication.md`'s `### Claim matching` section (:525) and the `bound_claims` swagger description in `shakenfist/external_api/auth.py:1917-1921`, both of which state the old rule. Do not touch `validate_bound_claims()`: matchers are unchanged. |
| 8d | high | opus | none | Mutation-test 8c, following the `mutation-check` skill. At minimum: make the list case match on `in` rather than equality (prefix matching) and confirm a test fails; make it stringify a non-string element; make it recurse into a nested list; make `matched_claim_values()` return the whole list rather than the matched elements and confirm the provenance test fails; make the scalar path return `[value]` unconditionally. Run each mutant under `timeout` and with `-B`, restore from pristine copies rather than `git checkout`, and record the table in this plan's Results section. A surviving mutant is a missing test, not a note. |
| 8e | high | opus | none | Write the two walkthroughs into `docs/operator_guide/authentication.md`, as `### A worked Keycloak example` and `### A worked Authentik example`, after the GitHub Actions example at :582 and before `### Abuse resistance`. Each one: what to create at the provider (from 8a/8b's notes, naming the version it was produced against), the `curl` that creates the issuer including `client_id` and `oidc_scopes`, the `curl` that writes a group-bound mapping rule in a namespace, and what the person then runs -- `sf-client login`. Reuse the existing one-sentence explanation of why these are `curl` rather than `sf-client`. State the trailing-slash difference once, where it bites, and link rather than restate the existing warning about `client_id` versus `audience`. The Authentik section carries D8's device-flow caveat as a step that cannot be skipped. Add the same caveat to `### Discovery` in two sentences. Nothing in these sections may name a plan, a phase, a decision or a step: a pre-commit hook enforces that, and the fact that a walkthrough exists is not evidence of a plan. |
| 8f | medium | sonnet | none | D7: a short `## Logging in as a person` section in `docs/user_guide/authentication.md`, after `## Keys that came from somewhere else` (:97), saying a person runs `sf-client login`, that the cluster mints one key per namespace their identity entitles them to, that the keys are cached locally and `sf-client logout` discards them, and linking `https://github.com/shakenfist/client-python/blob/develop/docs/human-login.md` for the rest. No cache paths, no file modes, no precedence rules: those are the client's to state and a copy here is a copy to drift. Check `docs/glossary.md` needs no new term (it has `identity token` and the federation vocabulary already) and say so either way. |
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
