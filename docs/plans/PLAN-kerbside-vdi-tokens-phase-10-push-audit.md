# PLAN: Kerbside VDI console tokens phase 10 -- push audit

Planning effort: high. Review effort: high.

## Why this phase exists

[PLAN-kerbside-vdi-tokens.md](PLAN-kerbside-vdi-tokens.md) ran across
**four repositories** -- shakenfist, client-python, kerbside and ryll --
and shipped a security feature: a cluster-signed JWT that a user
exchanges, at a service Shaken Fist does not run, for a console session
on their own instance. Each pull request was reviewed on its own, inside
its own repository. Nobody has looked at the four sides as one system.

That matters more here than in most plans, because this plan's defects
have all lived precisely in the gaps between repositories, and none of
them was found by review. Three shipped and were found by hand:
kerbside#201 (the SF console source keyed its node map by fqdn and
looked it up by UUID, so no console was ever scraped), shakenfist#4009
(the direct `.vv` carried a node UUID as `host` and an internal enum as
`type`), and shakenfist#4003 (the `vdi-console-proxy` capability was
advertised unconditionally, so a client on a Kerbside-less cluster never
reached the fallback path). All three are now fixed. Each of the three
survived review because the test double on one side of a seam made the
value the other side actually sends indistinguishable from the value it
expects.

`PUSH-AUDIT.md` is the repository's audit template, normally a pre-push
gate run against `develop...HEAD`. Here it runs retrospectively over
merges in four repositories, which changes the baseline but not the
questions -- and adds one question the template does not have a heading
for. Decision 4 is that question.

The direct precedents are
[PLAN-database-load-reduction-phase-08-push-audit.md](PLAN-database-load-reduction-phase-08-push-audit.md)
(PR #3928) and
[PLAN-queue-performance-phase-08-push-audit.md](PLAN-queue-performance-phase-08-push-audit.md)
(PR #3880). Both are single-repository audits; this plan borrows their
structure and their retrospectives' lesson -- an audit finds what its
briefs point at, and a heading with no "what I examined" list is a
rubber stamp -- and adds a cross-repository baseline they did not need.

## Scope

**In scope.** The code this plan added or changed across the eleven
merge ranges in decision 1, in all four repositories, audited under the
`PUSH-AUDIT.md` headings: wave 1 mechanical checks, and wave 2's code
quality, test coverage, documentation and security reviews, plus the
fifth lens in decision 4. Phase 11's three issue-fix merges are inside
that baseline (decision 3), so the audit reads the tree as it stands
today rather than as it stood before the defects were found.

**Out of scope.** Fixing anything the audit finds, unless it is blocking
or trivial. This plan's convention, like both precedents', is that a
review phase records and files rather than expanding into the work it
discovers.

**Out of scope.** The deferred deployer phase (shakenfist#4004's larger
half: a `kerbside` infrastructure group, `sources.yaml` rendering,
kerbside's own TLS material). The plan deliberately deferred it and
Future work records it. The audit may find that its absence makes
something else in the shipped code untestable, and should say so, but it
does not re-open the deferral.

**Out of scope.** Re-litigating the reviews already recorded on each PR.
shakenfist#3491 carries "Address PR #3491 review: docs and mint guards"
and kerbside#167 carries "Address PR #167 review: token exchange
hardening"; the audit starts by reading those commits rather than
rediscovering what they fixed. It may disagree with a disposition, and
should say so explicitly if it does.

**Out of scope.** Adjacent changes to the same files that belong to
other plans. Named exclusions in decision 2, so this is a decision and
not an omission.

**Out of scope.** Kerbside's own release. Phases 5 and 6 shipped in
kerbside v0.5.0 and that release has happened; the audit does not
re-cut it. A blocking finding against kerbside lands on `develop` and
rides the next release like any other fix.

## What the survey found

The master plan's phase 10 section is four sentences long and every
factual claim in it holds: `PUSH-AUDIT.md` exists at this repository's
root, running it over the accumulated diff rather than the last phase's
diff is the right instruction, findings can land as their own pull
request, and "if the audit finds nothing, that is recorded in one
sentence" is a workable exit. Six things it did not anticipate:

1. **Phases do not map onto merges, and the mapping is much coarser
   than one-per-phase.** The section says "every phase in this plan",
   which reads as one range per phase. In fact five phases share a
   single shakenfist PR (#3491 carries phases 1, 2, the SF half of 6,
   the SF half of 7 and the SF half of 8) and four phases share a
   single kerbside PR (#167 carries phases 5, 6, 7 and the kerbside
   half of 8). An audit keyed on "one range per phase" would look for
   ten ranges, find them missing, and either give up or invent them.
   Decision 1 replaces it with the eleven ranges that actually exist.

2. **The master plan records no PR or merge references at all.** Its
   Execution table names plan files and repositories, never a PR
   number, so the ranges in decision 1 had to be reconstructed from
   `git log` in each of the four working copies. That reconstruction is
   itself a deliverable of this plan: decision 1's table is the record
   the master plan should have carried, and step 10a writes a pointer
   to it into the master plan's Execution section. This is also what
   consistency-audit issue #4063 (*Push audit phase in master plans*)
   is about; this plan does not fix that audit finding, but it stops
   this plan from being another instance of it.

3. **Phase 11 is substantively complete and was recorded as "Not
   started".** All three issues it tracks are closed with fixes on
   `develop`: shakenfist#4009 by PR #4016 (`d36298a43`, "Emit a valid
   direct-to-hypervisor .vv file"), shakenfist#4003 by PR #4018
   (`046198908`, "Gate vdi-console-proxy capability on config") and
   shakenfist#4004 by PR #4024 (`9fdc1d04e`, "Add a kerbside_url
   variable to the node role"). Spot-checked against the tree, not
   taken from the issue state: `external_api/app.py:355-360` now
   registers `vdi-console-proxy` as a conditional token gated on
   `config.KERBSIDE_URL`; `external_api/instance.py:1688-1727` resolves
   the placement node to `n.ip`, collapses any `spice*` VDI type to
   `spice`, and emits `host-subject` from
   `node.spice_server_cert_subject`; and
   `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_vdi_console_file.py`
   parses a real `.vv` with `configparser` and asserts the host is not
   a UUID -- the coverage whose absence let all three rot. Step 10a
   corrects the master plan's Execution table, the success criteria's
   two stale "NOT MET"/"PARTIALLY MET" annotations, and
   `docs/plans/index.md`'s "10 of 12" at source, so the next reader
   does not re-derive this. Note that this makes **phase 10 the only
   phase outstanding**, and running it last -- after the phase 11 fixes
   are in the baseline -- is the correct order rather than an accident
   of numbering.

4. **The proto-freshness check applies, unlike in the precedent.**
   shakenfist#3491 touches `protos/database.proto` (+5) and regenerates
   `shakenfist/protos/database_pb2.py` (656 lines changed) and
   `database_pb2.pyi`, alongside +10 in
   `shakenfist/daemons/database/main.py` and 37 changed lines in
   `shakenfist/mariadb.py`. So `tox -e genprotos` followed by
   `git diff --exit-code shakenfist/protos` is in wave 1 here.

   What those changes are **not** is a new database call. Nothing in R1
   adds a top-level `mariadb.py` function, a new gRPC method, or a new
   Prometheus counter. The five proto lines add two fields
   (`spice_server_cert_subject` and its `has_` companion) to the
   existing `NodeAttributesProto`; the +10 in
   `daemons/database/main.py` is the marshalling of those two fields
   inside the existing `GetNodeAttributes` and `UpdateNodeAttributes`
   handlers, whose `get_node_attributes` and `update_node_attributes`
   counters already existed and already cover the extended messages.
   So the three-layer direct/gRPC/public review in 2a has something
   real to look at, but what it is looking at is one field threaded
   through an existing trio -- `NodeAttributesProto` /
   `get_node_attributes` / `update_node_attributes` -- and the
   question is whether the field is carried consistently across all
   three layers and both the direct and gRPC paths, not whether a new
   call was built correctly.

5. **The same node-identity confusion shipped twice, on opposite sides
   of the seam, and a test fixture hid it both times.** kerbside#201's
   own commit message says it plainly: "The bug stayed latent because
   the unit-test fixture set both the instance's node and the node's
   name to `n1`, making them indistinguishable." shakenfist#4009 is the
   same class -- `placement['node']` became a node UUID and the `.vv`
   generator kept substituting it as a hostname -- and its cause was
   that no test in any repository parsed a `.vv` file. That is two
   independent instances of one defect shape, found by hand a month
   apart, in a plan whose whole substance is values crossing between
   four codebases. Decision 4 turns it into the fifth wave-2 lens.

6. **ryll really is published, so the phase-3 contract is checkable end
   to end.** `https://pypi.org/pypi/ryll/json` returns 0.1.7 with
   `ryll-0.1.7-py3-none-manylinux_2_28_x86_64.whl` and the aarch64
   companion, and `client-python/pyproject.toml:51-53` declares the
   `vdi` extra as `ryll ; sys_platform == 'linux'`. Phase 3's stated
   goal -- "`pip install` puts a working `ryll` binary on the venv's
   PATH" -- can therefore be verified by running it rather than by
   reading the workflow that builds it. Step 10c does exactly that.

Verified as part of the survey and reported as results in their own
right: all eleven merge commits are ancestors of their repository's
`develop`; nothing from this plan is unmerged in any of the four
repositories, so there is no open-branch complication of the kind the
database-load-reduction precedent's decision 2 had to handle; and the
one leftover worktree and branch (`kerbside-wt-vdi-plan` on
`kerbside-vdi-tokens-plan-update`, from kerbside PR #389) was clean,
merged and has been removed. Deferred work was filed rather than
dropped: #4004 carries the deployer gap and the master plan's Future
work section carries the four remaining items.

## Decisions

1. **The audit baseline is this plan's merge ranges across four
   repositories, not `develop...HEAD`.** Everything is merged, so
   `git diff develop...HEAD` on this branch contains only this plan
   document and every command in the template would report success
   against nothing.

   | # | Repo | Phases | PR | Merge | Size |
   |---|------|--------|----|-------|------|
   | R1 | shakenfist | 1, 2, 6 (SF half), 7 (SF half), 8 (SF half) | #3491 | `9d41a1716` | 37 files, +3256/-338 |
   | R2 | shakenfist | 9 closeout (docs only) | #3580 | `07d7081b7` | 2 files, +4/-3 |
   | R3 | shakenfist | plan update (docs only) | #4011 | `c3e76ff8a` | 2 files, +108/-8 |
   | R4 | shakenfist | 11 (#4009) | #4016 | `5ef83c065` | |
   | R5 | shakenfist | 11 (#4003) | #4018 | `913411586` | |
   | R6 | shakenfist | 11 (#4004) | #4024 | `f2df423d8` | |
   | R7 | client-python | 4 | #350 | `b426e1f` | 10 files, +654/-17 |
   | R8 | kerbside | 5, 6, 7, 8 (kerbside half) | #167 | `f50ea59` | 25 files, +2144/-18 |
   | R9 | kerbside | 9 (SF end-to-end lane) | #194 | `115416c` | 15 files, +1634/-2 |
   | R10 | kerbside | post-phase-9 scrape fix | #201 | `7803368` | 4 files, +72/-21 |
   | R11 | ryll | 3 | #190 | `fa7ee21` | 13 files, +636/-2 |

   Each range is `<merge>^1...<merge>`, which is the diff the pull
   request added and excludes anything merged in from the target branch.
   The per-range diffs are what each agent reads. Where a later range
   changed an earlier range's code -- R4/R5 both edit files R1 created,
   and R10 edits a file R8 created -- the **net** state is what matters
   for a correctness finding: an agent that finds something in an early
   range must check the file as it stands on `develop` today before
   reporting it. The range shows what changed; the working tree shows
   what shipped.

2. **Three adjacent merges are excluded by name, with reasons.**
   ryll #207 (`067321a`, "Publish ryll straight to PyPI, drop
   TestPyPI") is 17 lines of release-workflow change three days after
   R11 and *is* part of phase 3's publish story -- it is excluded from
   the code-review ranges because it is a workflow simplification with
   no artefact consequence, but step 10c's install probe tests the
   result of it directly, which is stronger coverage than reading the
   diff. kerbside #292 (`0509c81`, which carries "Remove plan phase
   references from the source tree") is the consistency audit's
   plan-phase-reference work, not this plan's. ryll's August 2026
   `release.yml` changes (`cd5419e` and its follow-ups, "Isolate the
   cargo build from the network") belong to ryll's own supply-chain
   work. Naming them here means a later reader can see the boundary was
   drawn deliberately.

3. **Phase 11's fixes are inside the baseline, not audited against a
   pre-fix tree.** R4-R6 are in decision 1's table. The audit's job is
   not to re-find #4003 and #4009 -- it is to ask whether their fixes
   are complete, whether the class of defect they represent has other
   instances, and whether the coverage added with them actually holds
   the fixes in place. The database-load-reduction precedent found a
   functional test whose fake made the tested call free
   (`test_reaper_read_count_does_not_grow_with_address_count`); the
   same question applies to `test_vdi_console_file.py` and to
   kerbside#201's revised fixture, and 10e's brief asks it directly.

4. **A fifth wave-2 lens: the seams between the four repositories.**
   `PUSH-AUDIT.md`'s four headings each look at one body of code and
   ask whether it is correct. Every defect this plan shipped was
   correct on both sides of a seam and wrong across it, and survey
   finding 5 says why that keeps happening: each repository's tests
   mock the other repository, and a mock is written from the same
   understanding that wrote the code, so it agrees with the code rather
   than with the peer.

   The fifth agent enumerates the values that cross a repository
   boundary in this feature -- every JWT claim, every field of the
   scraped console record, the public-key document's shape, the
   capability token string, the `.vv` file's keys, the exchange URL's
   query parameter, the `KERBSIDE_URL` join -- and for each asks four
   questions: which repository writes it, which reads it, which side's
   tests fix its shape, and what would a change on the writing side do
   to the reading side's tests. A value whose shape is asserted only by
   fixtures on both sides, with nothing that reads a real message, is a
   finding whether or not it is currently wrong. This is the lens most
   likely to find something and the one no template heading covers.

5. **ryll's range is audited under wave 1 and security only, and this
   is deliberate.** R11 is a packaging change: `pyproject.toml`, a
   build script, and release-workflow jobs. It adds no application
   logic, so 2a (code quality) and 2b (test coverage) have nothing to
   read that is not better covered by actually installing the published
   wheel. Its real risks are supply-chain ones -- how the wheel is
   built, signed and published, and whether the `manylinux_2_28` tag is
   honest about a GUI binary's dlopen'd dependencies -- and those go to
   the security agent, which is the right heading for them. Recording
   this as a decision means a later reader can see that ryll was scoped
   out of two headings rather than forgotten by them.

6. **Wave 1's exit condition is relaxed in one specific way.** The
   template says stop if `pre-commit` or `tox` fails. Those run against
   the working tree, which here is each repository's `develop` plus, in
   this one, this plan document -- so a failure is a pre-existing
   failure on `develop`, not something this plan introduced. If wave 1
   fails, record it, check whether any of the eleven ranges is
   implicated, and continue to wave 2 rather than stopping. Stopping
   would only be correct if this branch were about to be pushed as
   code. Same reasoning as both precedents.

7. **A blocking finding is fixed in the repository it lives in, as its
   own pull request.** This phase's PR is in shakenfist and can carry
   shakenfist fixes directly. A blocking finding against client-python,
   kerbside or ryll gets a pull request in that repository, and this
   plan's Findings section records the PR number and its state. The
   plan does not reach Complete while a blocking finding is open in any
   of the four. Advisory findings are filed as issues in the owning
   repository and listed here with their numbers.

8. **A clean heading is a result, but only alongside a list of what was
   examined.** If a heading finds nothing it says so in one sentence,
   followed by what it actually read. An audit reporting nothing under
   every heading is recorded as such rather than padded with
   manufactured findings. The precedents' shared risk -- that a
   retrospective audit rubber-stamps code which already shipped and
   passed CI -- is guarded by requiring the "what I examined" list, not
   by requiring findings.

9. **Security is the heading that gets the most, and it runs at high
   effort on opus.** This is the only plan in the current set that
   mints a bearer credential, publishes a public key, and hands
   authorisation to a service outside the cluster. The master plan's
   own management-session checklist already names four security
   questions; 10g's brief carries all four plus the ones the checklist
   does not have.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 10a | medium | opus | none | *(Management session, this document.)* Write this phase plan; add it to the master plan's Execution table with status In progress; correct the master plan at source per survey finding 3 -- phase 11's Execution row becomes Complete with its three PR references, and the success criteria's "**NOT MET as at 2026-09-01**" and "**PARTIALLY MET**" annotations are rewritten to say what is true now; add decision 1's range table (or a pointer to it) to the master plan's Execution section so the PR references stop being unrecorded; update `docs/plans/index.md`'s row to "11 of 12" with the phase-10 link. Do **not** touch `docs/plans/order.yml` -- it carries master plans only. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. |
| 10b | medium | sonnet | none | **Wave 1, shakenfist.** In this worktree run `pre-commit run --all-files` and `tox`, recording actual output; per decision 6 a failure is reported, not a stop. Then, because R1 touches `protos/database.proto`, run `tox -e genprotos` followed by `git diff --exit-code shakenfist/protos` and record the result -- this check *does* apply here, unlike in the database-load-reduction audit, so do not skip it. Then run `PUSH-AUDIT.md`'s four style greps against **each** of ranges R1-R6 individually rather than `develop...HEAD`: lines over 120 characters, stray `print(`, new `etcd` references, and new `mariadb.get_all_*(` without a `# nopushdown:` tag. Then the style-conformance judgment brief from `PUSH-AUDIT.md` over R1-R6: import ordering, the `shakenfist_utilities.logs` pattern, single quotes for strings and double for docstrings, 120-character lines, event logging with the right `EVENT_TYPE_*` constant, and the three-layer direct/gRPC/public pattern. Note that R1 adds **no** new `mariadb.py` function, no new gRPC method and no new counter, so do not go looking for them: it threads one new field (`spice_server_cert_subject`, plus its `has_` companion) through the existing `NodeAttributesProto` / `get_node_attributes` / `update_node_attributes` trio, and the counters those two handlers already register cover it unchanged. The three-layer question here is whether that one field is carried consistently through the direct path, the gRPC path and the public wrapper, and whether the optional-field encoding round-trips `None`. `shakenfist/util/vdi_tokens.py` (268 lines, new) gets the closest read. Report the greps' actual output, not a summary of it. |
| 10c | medium | sonnet | none | **Wave 1, the other three repositories, plus the install probe.** Working copies are at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/{client-python,kerbside,ryll}`, all on `develop`. In client-python and kerbside run each repository's own pre-commit/tox equivalent (read their `.pre-commit-config.yaml` and `tox.ini`; kerbside's runs its tox envs, client-python's runs flake8 and its unit tests) and record actual output. ryll has no Python test suite to run for R11; instead **verify the phase-3 contract by running it**: create a throwaway venv under the scratchpad, `pip install ryll`, and confirm (a) a `ryll` executable lands on the venv's `bin/`, (b) it is the embedded binary rather than a launcher stub, and (c) `ryll --version` (or `--help` if there is no version flag) runs and exits zero. Then `pip install 'shakenfist-client[vdi]'` in a second venv and confirm the extra pulls ryll in. Record the wheel filename pip actually chose. Do **not** install anything into the system Python; PEP 668 will refuse and a venv is the supported path. Also run the four style greps against R7-R11, adapting them to each language (the `etcd` and `get_all_*` greps are shakenfist-specific and do not apply; say so rather than reporting them clean). |
| 10d | high | opus | none | **2a, code quality.** Take 10b and 10c's mechanical output as input. Review ranges R1, R4-R10 (per decision 5, R11 is out of this heading; R2, R3 are documentation-only) for duplicated logic and missed abstractions, and apply the two blocking rules from `PUSH-AUDIT.md`: SQL pushdown (any new `mariadb.get_all_*(` that should be a `find_*`, including callers that reach a scan through a helper) and the cached-FK-list pattern (any new `list[str]`/`list[UUID4]` on a `shakenfist/schema/*_attributes.py` model -- R1 adds to `shakenfist/schema/node_attributes.py`, so check what it added and why). Specific things to read as they stand on `develop`, not only as the ranges changed them: `shakenfist/util/vdi_tokens.py`, the minting endpoint and the `.vv` generator in `shakenfist/external_api/instance.py`, the conditional-capability machinery in `shakenfist/external_api/app.py:290-400`, `kerbside/sf_token.py` and the console source in `kerbside/sources/shakenfist.py`. Apply the comment-proportion shared block: R1 and R8 both carry long explanatory comments and some are load-bearing while others restate the code. Triage every TODO / `# noqa` / `# type: ignore` the sweeps flagged as blocking or advisory. |
| 10e | medium | sonnet | none | **2b, test coverage.** Review R1, R4-R10 for coverage. The question is not quantity -- this plan added roughly a thousand lines of tests -- but whether the *risky* paths are covered and whether the fixtures are faithful. Specifically: (i) does the minting endpoint have adversarial coverage (namespace A minting for namespace B's instance, an instance not in `created`, a non-VDI instance, `KERBSIDE_URL` unset), and is any of it functional rather than unit-only? (ii) Do the token tests cover replay, expiry and a signature from the wrong key, on the kerbside side where the verification actually lives? (iii) Per decision 3, does `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_vdi_console_file.py` actually hold #4009's fix in place, or does it assert against a value its own setup supplies? Read the test, not its docstring. (iv) Same question for the fixture kerbside#201 revised -- it previously set the instance's node and the node's name to the same string, which is what hid the bug; check what it sets now. (v) Shaken Fist prefers functional to unit coverage, so for each behaviour name which `cluster_ci_tests` module exercises it and which have unit coverage only. Flag assertions that test implementation details rather than behaviour. |
| 10f | medium | sonnet | none | **2c, documentation.** Check documentation against code across all eleven ranges, applying the README, LLM-doc and plan-phase-reference shared blocks from `PUSH-AUDIT.md`. The specific risks: R1 added 15 lines to `AGENTS.md` and 38 to `ARCHITECTURE.md`, and the shared blocks say growth in those files is itself a finding -- check whether that content belongs in `docs/operator_guide/vdi_console_tokens.md` (216 lines, also added by R1) instead. The feature is documented in at least eight places across the four repositories (`docs/operator_guide/vdi_console_tokens.md`, `docs/user_guide/consoles.md`, `docs/developer_guide/api_reference/{admin,instances,nodes}.md`, `docs/developer_guide/security_model.md`, `docs/release_notes/v07-v08.md`, plus kerbside's `docs/components/kerbside/*` mirror in this repository and kerbside's own docs); the falsifiable question is whether any fact about the token -- its TTL, its claim set, its algorithm, the audience string, the error responses -- is stated differently on two pages. Check it and say so either way. Also confirm the phase-11 fixes reached the documentation: does anything still describe the `.vv` `host` as a node name, or the capability as unconditional? Confirm the master plan's Execution table and `docs/plans/index.md` agree after 10a, and that kerbside's `docs/plans/index.md` cross-reference entry is current. The plan **did** change database schema, in two of the four repositories, so migration guidance applies and the check is that it is documented rather than that it is unnecessary: R1 added `spice_server_cert_subject` to `NodeAttributesData`, backed by an `ALTER TABLE node_attributes ADD COLUMN IF NOT EXISTS` and a `NODE_ATTRIBUTES_VERSION` bump to 4 in `shakenfist/mariadb.py`, and R8 added `kerbside/migrations/versions/cdb5c3529858_sf_token_tables.py` creating `sf_token_jtis` and `sf_token_keys`. Each repository carries its migration in-tree in its own idiom -- Shaken Fist's additive `_ensure_node_attributes_schema` step run by `sf-ctl ensure-mariadb-schema`, kerbside's alembic revision run by `kerbside db upgrade` -- and each documents it, kerbside's tables in `docs/schema.md` and its upgrade runbook in `docs/installation.md`. So the requirement is met; check that those documents still match the code. |
| 10g | high | opus | none | **2d, security.** Security review of R1, R4-R11. This is the heading that matters most here: the plan mints a bearer credential and delegates authorisation to a service outside the cluster. Work through, in order: **(1) key custody.** `shakenfist/util/vdi_tokens.py` generates and stores an EdDSA signing key in `cluster_config`. Can private material reach an API response, a log line, or an event? Is the lazy-creation race actually atomic, and what happens if two API nodes create simultaneously -- do both keys get used, and can a token signed by the loser be verified? Is there a rotation path and does it leave old tokens verifiable for their TTL? **(2) The mint gate.** Confirm the decorator stack on the minting endpoint is `@verify_token` / `@arg_is_instance_ref` / `@requires_instance_ownership` / `@log_token_use` and that `@redirect_instance_request` is deliberately absent; confirm a caller in namespace A cannot mint for namespace B's instance by any route including metadata or a stale reference. **(3) The claim set.** Read the claims the SF side signs and the claims the kerbside side trusts. Does kerbside read any backend address, port or hostname from the JWT (the master plan's checklist says it must not)? Is `aud` checked, is `exp` checked, is `jti` replayed-checked, and is the replay cache bounded? **(4) The public key endpoint.** `/admin/vditokenpubkey` is readable by any authenticated namespace; confirm nothing private is in the response and that the endpoint cannot be used to enumerate or to force key generation. **(5) host_subject.** SF publishes each node's SPICE certificate subject (R1's `shakenfist/node.py` change) and kerbside pins it (R8/R10). Every hypervisor's certificate is signed by the same cluster CA, so without the pin any node impersonates any other -- verify the pin is actually enforced on the proxy path and on the direct `.vv` path, and that R10's change to synthesize it from the fqdn cannot produce a subject that matches nothing (which fails open or closed -- say which). **(6) Supply chain, ryll (R11, per decision 5).** Is the wheel published via OIDC Trusted Publishers rather than a long-lived token? Is build provenance attested? Is the `manylinux_2_28` tag honest for a GUI binary -- `ryll/pyproject.toml` claims the GUI and audio libraries are dlopen'd rather than `DT_NEEDED`; verify that against the installed wheel from 10c rather than against the comment. **(7) The usual sweep** across the Python ranges: f-string or `text()` SQL, `shell=True`, unvalidated input reaching a subprocess or a template, and secrets in events. Report findings with severity; critical and high must be resolved before this phase closes. |
| 10h | high | opus | none | **The cross-repository seam lens (decision 4).** This is the plan-specific brief and it has no template heading. Build the inventory of every value that crosses a repository boundary in this feature and, for each, name the writer, the reader, and which side's tests fix its shape. Start from -- and do not stop at -- these: every claim in the minted JWT (shakenfist writes, kerbside reads); every field of a scraped console record, especially `host`, `hypervisor` and `host_subject` (shakenfist's API writes, `kerbside/sources/shakenfist.py` reads -- this is where kerbside#201 lived); the `/admin/vditokenpubkey` document's shape (shakenfist writes, kerbside and client-python read); the `vdi-console-proxy` capability token, which the client detects by substring match against the root page (shakenfist writes, client-python reads -- this is where #4003 lived); the `.vv` file's keys and values (shakenfist writes, virt-viewer and ryll read -- this is where #4009 lived); the exchange URL and its `token=` parameter (shakenfist composes, kerbside parses); `KERBSIDE_URL` itself (the ansible collection renders, `config.py` reads, two endpoints branch on). For each, answer: what would happen if the writing side changed this value's shape tomorrow -- which test in which repository fails, and if the honest answer is "none", say so. A value whose shape is asserted only by hand-written fixtures on both sides, with nothing anywhere that reads a real message from the peer, is a finding regardless of whether it is currently correct. Grade each seam **covered** (name the test that would catch a change), **fixture-only**, or **uncovered**. "Covered" requires naming the mechanism, not the absence of a bug report. Where the answer is fixture-only or uncovered, propose the cheapest thing that would change it -- a contract test, a recorded real response, or a CI lane -- and note that kerbside's phase-9 end-to-end lane (R9) may already be that mechanism for some seams; check whether it is and which ones it covers. |
| 10i | high | opus | none | *(Management session.)* Grade every finding blocking or advisory. Fix blocking findings in the repository they live in, per decision 7: shakenfist fixes in this phase's PR, others as their own PR in their own repository. File advisory findings as issues in the owning repository. Spot-check two findings per agent against the tree before accepting the report -- an agent that reasoned from a diffstat produces plausible findings that are not about this code. Check every heading names what it examined, per decision 8. Write the results into this plan's Findings section, mirror the summary into the master plan, set both statuses, and run `tools/check-plan-status.py` and `pre-commit run --all-files`. |

## Risks and mitigations

* **The audit rubber-stamps code that already shipped and passed CI in
  four repositories.** Mitigation: decision 8 -- every heading names
  what it examined, and "nothing found" is only acceptable alongside
  that list. Step 10i checks this before writing results, and spot-
  checks two findings per agent against the tree.
* **Four repositories is enough surface to skim.** Roughly 8,400
  insertions across eleven ranges in three languages. An agent that
  reads the diffstat and reasons from file names will produce plausible
  findings that are not about this code. Mitigation: every brief names
  specific files and specific questions rather than a range and a
  heading, and 10i's spot-check is the backstop.
* **Agents working outside this repository get lost or edit the wrong
  tree.** The other three working copies are on `develop` and are not
  this phase's worktree. Mitigation: 10c and 10h are read-only in those
  repositories; any fix lands through decision 7's own-PR route, made
  by the management session, not by an audit agent.
* **The seam lens finds something expensive.** An uncovered seam is not
  a bug, it is an absent test -- and the honest fix for several of them
  could be a new CI lane, which is a plan of its own. Mitigation: 10h
  proposes the cheapest mechanism per seam and 10i grades; a finding
  too large for this phase is filed at high priority and named here,
  not downgraded to advisory to keep the phase small. The master plan's
  Future work section is the right home for anything that becomes its
  own plan.
* **The install probe fails for an environmental reason and is read as
  a defect.** `pip install ryll` on a host without the dlopen'd GUI
  libraries will install fine and fail to run, which is the correct
  behaviour for a manylinux wheel and not a finding. Mitigation: 10c
  reports what it observed and 10g decides what it means; the two are
  deliberately different steps.
* **Phase 11's status correction is wrong.** Survey finding 3 asserts
  phase 11 is complete on the strength of three closed issues and three
  spot-checks. Mitigation: the spot-checks are named with file and line
  so a reviewer can falsify them in a minute, and 10e's brief (iii) and
  (iv) re-examine the coverage those fixes added rather than accepting
  it.

## Definition of done

* Every one of the five wave-2 headings (2a, 2b, 2c, 2d, and the
  decision-4 seam lens) has a written result naming what was examined.
* Wave 1 has been run in all four repositories with output recorded --
  not asserted -- and the style greps run against the individual ranges
  in decision 1 rather than against `develop...HEAD`.
* The proto-freshness check has been run (`tox -e genprotos` then
  `git diff --exit-code shakenfist/protos`) and its result recorded.
  Unlike the database-load-reduction audit, this check applies here;
  recording it as not-applicable is a failure of this criterion.
* `pip install ryll` into a fresh venv has been run, and the result
  records the wheel filename pip chose, whether a `ryll` executable
  landed on PATH, and whether it ran. `pip install
  'shakenfist-client[vdi]'` has been run and the result records whether
  the extra pulled ryll in.
* The seam lens has produced an explicit inventory of the values that
  cross a repository boundary, each graded covered, fixture-only or
  uncovered, with "covered" naming the test that would catch a change
  on the writing side.
* No fact about the token -- TTL, claim set, algorithm, audience
  string, error responses -- is stated differently on two documentation
  pages across the four repositories, or every divergence found is
  listed with its disposition.
* The two database schema changes this plan shipped -- Shaken Fist's
  `node_attributes` version 4 column and kerbside's
  `cdb5c3529858_sf_token_tables` revision -- have each been checked
  against their in-tree migration code and their documentation.
  Recording migration guidance as not applicable is a failure of this
  criterion: the plan changed schema in two repositories.
* Every finding carries a grade (blocking or advisory) and a
  disposition (fixed in this PR, fixed as <repo>#NNNN, filed as
  <repo>#NNNN, or declined with a reason in writing here).
* No blocking finding is left unresolved in any of the four
  repositories.
* Two findings per agent have been spot-checked against the tree by the
  management session, and the spot-check is recorded.
* The master plan's Execution table records a PR or merge reference for
  every phase, so decision 1's reconstruction does not have to happen
  again.
* `tools/check-plan-status.py` passes, and the master plan's Execution
  table and `docs/plans/index.md` agree with each other.
* The master plan's status becomes Complete only if no blocking finding
  remains open, and phase 11's status correction has been reviewed
  rather than assumed.
* If the audit finds nothing, that is recorded in one sentence, per the
  master plan's own instruction for this phase.

## Back brief

Before executing any step of this plan, back brief the operator on your
understanding of the plan and how the work you intend to do aligns with
it.

Two gates, for steps that are cheap to propose and expensive to redo:

* **Before 10a's corrections to the master plan land**, confirm the
  operator agrees that phase 11 is complete on the evidence in survey
  finding 3. If it is not, the range table in decision 1 still stands
  but this phase is not the last one and the master plan's status
  arithmetic changes.
* **Before 10i files or fixes anything outside this repository**,
  confirm the shape of the split with the operator: which findings land
  as pull requests in client-python, kerbside or ryll, and which are
  filed as issues. Three extra pull requests across three repositories
  is a different-sized commitment from one, and the master plan's
  Execution table has no row for them.

## Findings

*(To be written by step 10i. Every heading names what it examined, per
decision 8.)*
