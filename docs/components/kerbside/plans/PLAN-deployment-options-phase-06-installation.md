# Deployment options phase 6: installation hand-off

Master plan: [PLAN-deployment-options.md](/components/kerbside/plans/PLAN-deployment-options/).

## Planning effort

The master plan asks for medium effort. This plan was written in a
low-effort session, below that. The survey read every page this phase
touches, and checked for links into them here and in the sibling
Shaken Fist clones, so it was not cut short. The review should still be
done at medium. Review effort: medium. The edits are small, and the
risk is in what they leave behind: a sentence on another page that
still sends readers to a guide by cloud.

## Scope

In scope:

- `docs/installation.md`: the "Deploying for real" section becomes a
  pointer to `docs/deployment/`, and the page's opening paragraph stops
  promising "the guide for the cloud you actually run".
- `docs/index.md`: delete the Deployment Options table's stale
  "not a link" sentence, reword the Use Cases introduction, and reword
  the Installation bullet under Operator Documentation.
- `README.md`: one new curated link, to the Deployment Options table,
  and the two link descriptions that frame deployment by cloud.
- `AGENTS.md`'s question table and `ARCHITECTURE.md`'s directory trees
  and Related Documentation list, which do not mention
  `docs/deployment/` at all (findings 4 and 5).

Out of scope:

- The Use Cases table's Proxmox row, which is stale but belongs to
  another plan (finding 6).
- The README's pitch paragraph about Kolla-Ansible (decision 4).
- The Deployment Options table's rows. Phases 2 to 5 wrote them, and
  phase 7's audit checks them.
- The push audit, which is phase 7.

## What the survey found

The master plan's phase 6 paragraph is right about `installation.md`.
It is wrong that README.md might need nothing and that ARCHITECTURE.md
and AGENTS.md probably need nothing: findings 3 to 5 correct it. The
planning commit corrected that paragraph at source.

1. **"Deploying for real" is still keyed by cloud.**
   `docs/installation.md:360-377` opens "Kerbside is deployed as a
   component of the cloud it serves", the opposite of the front-door
   default this plan argues for. Its table has rows for oVirt,
   OpenStack, Shaken Fist and static targets. The OpenStack row links
   kerbside-patches, not `deployment/kolla-ansible.md`. It has no
   Proxmox row, though #618 merged the Proxmox source. The closing
   paragraph (the pieces are the same; `configuration.md` and
   `console-sources.md` apply throughout) is still true. The page's
   opening paragraph (`installation.md:3-4`) promises "the guide for
   the cloud you actually run".

2. **Nothing links the section's anchor.** `#deploying-for-real` has
   no inbound link in this repository outside `docs/plans/`. The local
   clones of shakenfist, kerbside-patches and client-python have no
   link to `installation.md` at all. Keeping the heading is free
   anyway (decision 1).

3. **Four more places frame deployment by cloud.**
   - `docs/index.md:110`, the Use Cases introduction, says "One page
     per deployment permutation".
   - `docs/index.md:156-158`, the Installation bullet, says the page
     tells you "where to go for your cloud".
   - `README.md:44-45` says the same two things.
   - `ARCHITECTURE.md:470` and `:504` call the use cases
     "Per-deployment and topology guides".

   The README has no link to the Deployment Options table, so its
   curated links do change. The master plan says to touch README.md
   only in that case.

4. **AGENTS.md does not know `docs/deployment/` exists.** Its question
   table (`AGENTS.md:23-25`) sends "How do I install and deploy it?" to
   `installation.md` and "How is it deployed against a specific
   cloud?" to `docs/use-cases/`. This is an index entry gone stale, not
   a convention change, so it does not cost a new line of convention.

5. **ARCHITECTURE.md's tree has no `ansible/` and no
   `docs/deployment/`.** The repository tree near `ARCHITECTURE.md:412`
   lists `etc/`, `demo/` and `tools/`, but not `ansible/`, where the
   role has lived since #580. The docs tree (`:468-471`) lists
   `spice/`, `use-cases/` and `plans/`, but not `deployment/`. Its
   Related Documentation list (`:497-504`) has no deployment entry.

6. **The Use Cases table's Proxmox row is stale.** `docs/index.md`
   says "Deferred until a Proxmox source driver exists" and "No source
   yet", but #618 merged the driver. `PLAN-proxmox-source.md`'s phase 6
   owns that row and a use-case page, and its phase 5 is in progress in
   another worktree. This phase leaves the row alone.

7. **The rest of the Deployment Options section is current.** Every
   mechanism row is now a link. That leaves the sentence "Mechanisms
   whose name is not a link are planned rather than written" with no
   case; phase 5's execution notes left it for this phase.

8. **Editing `installation.md` starts the advisory `demo-compose`
   lane.** `.github/workflows/demo-compose.yml` includes it in the path
   filter on purpose, because the page walks through the demo. That is
   expected and does not gate the merge.

## Decisions

1. **Keep the heading and rewrite the body as a short pointer.**
   `## Deploying for real` stays, so that any link from outside these
   clones still lands. Under it, about three short paragraphs:
   - The default: a Kerbside deployed in front of the clouds rather
     than by one of them, as the index's Deployment Options
     introduction puts it.
   - Where to go: link the four mechanism pages by name in one
     sentence, then send the reader to
     [Deployment Options](/components/kerbside/plans/index/#deployment-options) for which
     sources each configures and what CI proves.
   - What each cloud needs from Kerbside, and Kerbside from it, is in
     its use case, linked through [Use Cases](/components/kerbside/plans/index/#use-cases).

   Then keep the existing closing paragraph about the pieces,
   `configuration.md` and `console-sources.md` as it is.

2. **No table on `installation.md`.** The index table is the plan's one
   matrix (phase 1, decision 1). A second table keyed by mechanism
   would drift from it, as this one did from the clouds. Naming the
   four pages in a sentence is a pointer, not a matrix: it carries no
   sources or CI column.

3. **Fix the cloud-keyed wording everywhere it appears, in the same
   change.** Every phrase in finding 3, and `installation.md:3-4`, is
   rewritten. The use cases become "one page per cloud or topology",
   which is what they are. The Installation bullets in the index and
   the README say the page ends by pointing at the deployment
   mechanisms. The definition of done greps for the old phrases.

4. **The README gains one link and keeps its pitch.** A "Deployment
   Options" bullet goes between Use Cases and Installation, linking
   `docs/index.md#deployment-options` with an absolute GitHub URL like
   its neighbours. The pitch paragraph at `README.md:17-20`, which
   calls OpenStack "probably the best documented integration" because
   of the Kolla-Ansible patches, stays. This is the decision a
   reviewer is most likely to argue with. That sentence is the README
   framing Kerbside by cloud, the framing this plan exists to change.
   But it is the pitch, the master plan limits README changes to the
   curated links, and the claim is still defensible: OpenStack is the
   only cloud with a deployer outside this repository. Rewriting the
   pitch is a separate change, if Mikal wants it.

5. **AGENTS.md and ARCHITECTURE.md get index fixes only.**
   - AGENTS.md: one new row, "How do I run it for real: by hand,
     Ansible, Kolla-Ansible or containers?", pointing at
     `docs/deployment/`. The use-case row becomes "How is it set up in
     front of a specific cloud?". No convention text changes.
   - ARCHITECTURE.md: `ansible/` in the repository tree, as the
     `shakenfist.kerbside` collection and its `kerbside` role;
     `deployment/` in the docs tree, as one page per deployment
     mechanism; a Deployment Options entry in Related Documentation;
     and the two "Per-deployment" descriptions become "Per-cloud". No
     prose about components changes, because no component changed.

6. **Leave the Proxmox use-case row to its plan.** Finding 6 is real,
   but `PLAN-proxmox-source.md` phase 6 owns that row and is the
   phase that writes the page it will link. Changing the cell here
   would collide with that plan's worktree for a one-line gain. The
   execution notes record that it was seen.

## Step plan

The two steps write to the same tree, so they run one after the other.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | opus | none | Rewrite `docs/installation.md`'s `## Deploying for real` section and its opening paragraph, and reconcile `docs/index.md`, per decisions 1 to 3. **Read first:** this plan, all of it; then `docs/installation.md`, `docs/index.md` (Use Cases, Deployment Options and Operator Documentation), and the four pages in `docs/deployment/`, for what each mechanism is and how each page names itself. **installation.md:** keep the heading text exactly. Replace the table and its lead-in with decision 1's three short paragraphs, linking `deployment/manual.md`, `deployment/ansible.md`, `deployment/kolla-ansible.md` and `deployment/containers.md` by their index names, then `index.md#deployment-options` and `index.md#use-cases`. Keep the existing paragraph that begins "Whichever you pick", adjusting only its first words if they no longer follow. No table in the section (decision 2). Rewrite lines 3-4 so the page promises to point at how to deploy Kerbside for real, not at a guide for your cloud. **index.md:** delete the two-line "Mechanisms whose name is not a link..." sentence under the Deployment Options table. Change the Use Cases introduction's "One page per deployment permutation" to say one page per cloud or topology. Reword the Operator Documentation Installation bullet's "where to go for your cloud" to name the deployment mechanisms. Change no table row; the Proxmox Use Cases row stays as it is (decision 6). Write plainly, matching the pages around it; no plan, phase or decision citations in the docs. **Checks:** `python3 tools/check-backend-tls-claims.py`; `timeout 900 python3 tools/mutate-backend-tls-claims.py` (12 mutations, all caught); `timeout 900 tox -epy3 -- kerbside.tests.unit.test_docs_links`; `pre-commit run --all-files`. Check every `#anchor` in the changed text by hand against the target's headings, because the link test strips anchors (#490). Commit subject: "Point installation at the deployment pages." |
| 6b | low | sonnet | none | Update `README.md`, `AGENTS.md` and `ARCHITECTURE.md` per decisions 3 to 5. **Read first:** this plan's findings 3 to 5 and decisions 3 to 5, then `docs/index.md`'s Deployment Options section and the `docs/deployment/` filenames, and `ansible/README.md`'s first two paragraphs for what the collection is. **README.md:** add a `Deployment Options` bullet between Use Cases and Installation, linking `https://github.com/shakenfist/kerbside/blob/develop/docs/index.md#deployment-options`, describing one page per mechanism (by hand, Ansible, Kolla-Ansible, containers) with the sources each configures and what CI tests. Reword the Use Cases bullet's "One page per deployment permutation" and the Installation bullet's "where to go for your cloud", as step 6a did on the index. Do not touch the pitch paragraphs above `## Installation` (decision 4). **AGENTS.md:** decision 5's new row after "How do I install and deploy it?", and the reworded use-case row. Nothing else. **ARCHITECTURE.md:** add `ansible/` to the repository tree beside `etc/` and `demo/`, with a one or two line comment in the tree's style; add `deployment/` to the docs tree between `spice/` and `use-cases/`; change both "Per-deployment and topology guides" to "Per-cloud and topology guides"; add a Deployment Options entry, linking `docs/index.md#deployment-options`, to Related Documentation after Use Cases. Keep the trees' comment columns aligned. **Checks:** `pre-commit run --all-files`, and `timeout 900 tox -epy3 -- kerbside.tests.unit.test_docs_links`. Commit subject: "Index the deployment pages from the top level." |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| A cloud-keyed phrase survives on a page this phase did not read, and the reader still gets sent to "the guide for your cloud". | Decision 3, and a definition-of-done grep over the whole tree outside `docs/plans/`, not only the changed files. |
| The pointer grows back into a table, or repeats the index's sources and CI cells, and drifts. | Decision 2. The definition of done counts table rows in the section. The management session reads the diff for any sources or CI claim. |
| Something outside these clones links `#deploying-for-real`. | The heading is kept verbatim (decision 1). |
| The README or ARCHITECTURE.md edits wander into the pitch or the component prose. | Decisions 4 and 5 name each line that changes. The management session checks that `git diff` on both files touches only those lines. |
| The Proxmox plan's phase 6 lands first and changes the Use Cases rows. | This phase changes no row, only the introduction above the table, so a rebase meets no conflict in the rows. |

## Definition of done

- [x] `grep -rn -i -E 'go for your cloud|guide for the cloud|guide for yours|deployment permutation|per-deployment and topology|deployed as a component of the cloud' --include=*.md . | grep -v 'docs/plans/'`
      prints nothing.
- [x] `docs/installation.md` still has a `## Deploying for real`
      heading, and that section has no table:
      `awk '/^## Deploying for real/,/^## Upgrading/' docs/installation.md | grep -c '^|'`
      prints 0.
- [x] That section links all four `deployment/*.md` pages,
      `index.md#deployment-options` and `index.md#use-cases`.
- [x] `docs/index.md` no longer contains "whose name is not a link".
- [x] No table row in `docs/index.md` changed:
      `git diff origin/develop -- docs/index.md | grep '^[-+]|'`
      prints nothing.
- [x] `README.md` links `docs/index.md#deployment-options`, and
      `git diff origin/develop -- README.md` touches only the
      Documentation list.
- [x] `AGENTS.md` links `docs/deployment/`. `ARCHITECTURE.md` names
      `ansible/` and `deployment/` in its trees, and links
      `docs/index.md#deployment-options`.
- [x] Every anchor in the changed files resolves. Checked by hand or
      with the anchor script, because the link test strips anchors
      (#490).
- [x] `tools/check-backend-tls-claims.py` exits 0, and
      `tools/mutate-backend-tls-claims.py` still reports every mutation
      caught.
- [x] `tox -epy3`, `tox -eflake8` and `pre-commit run --all-files`
      pass.

## Execution notes

- Back brief answered on 2026-10-29 (AEDT). Mikal kept decision 4, so
  the README's pitch paragraph stays, and gave this session permission
  to push and open the pull request once the definition of done
  passes.
- Step 6a's Installation bullet in the index was tightened in review
  to "a pointer to the deployment pages: by hand, Ansible,
  Kolla-Ansible and containers".
- Step 6b's README bullet said each mechanism's page covers "what each
  needs". Review changed it to the sources each configures and what CI
  tests, which is what the index table says.
- The Use Cases table's Proxmox row still says no source driver exists.
  Decision 6 leaves it for `PLAN-proxmox-source.md` phase 6.
- Definition of done checked on 2026-10-29. The phrase grep prints
  nothing. The section has one heading, no table lines, and links all
  four pages and both index anchors. The index diff touches no table
  row, and the README diff has one hunk, in the Documentation list.
  All 17 local anchors in the five changed files resolve. `tox -epy3`
  passed 633 tests, and flake8, pre-commit and the TLS claim guard
  passed. The mutation tool caught all 12 mutations.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of the plan and how the work you intend to
do aligns with it. One point needs Mikal's answer before 6a runs:

- **Decision 4:** keep the README's Kolla-Ansible pitch paragraph,
  or rewrite it in this phase.
