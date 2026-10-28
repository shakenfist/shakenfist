# Deployment options phase 4: containers

Master plan: [PLAN-deployment-options.md](/components/kerbside/plans/PLAN-deployment-options/).

## Planning effort

The master plan asks for medium effort. This plan was written in a
low-effort session, below that. The survey checked every claim
below against this tree, Kolla's source and the image registry on
the day, so it was not cut short, but the review should be done at
medium. Review effort: medium. The page is short and mostly says
what does not exist. The claims about Kolla's images are the ones a
reader can act on wrongly.

## Scope

In scope:

- `docs/deployment/containers.md`, the third page in the section,
  and its entry in `docs/deployment/order.yml`. Master plan question
  4 settled on one short page.
- Correcting what this repository says about Kolla's Kerbside
  images, wherever it says it (finding 1). That covers the master
  plan, `docs/deployment/kolla-ansible.md`, and the index row.
- Making the Containers row of the `docs/index.md` Deployment
  Options table a link.
- One comment on #553, the image issue, recording what the survey
  found (step 4b).

Out of scope:

- Publishing an image, a production compose file, or a Helm chart.
  These are #553 and the master plan's Future work. This phase
  documents their absence and does not fill it (decision 2).
- Changing `demo/`. It is `PLAN-demo-install.md`'s, which is
  complete. Its walkthrough in `installation.md` is linked, not
  restated.
- `installation.md`'s "Deploying for real" table. Phase 6 owns it.

## What the survey found

Checked on 2026-10-28. The master plan's claims that were wrong are
corrected at source in this phase's planning commit, as noted under
each finding. A step does not need to redo them.

1. **Kolla publishes Kerbside images, built from `develop`.**
   - quay.io's `openstack.kolla` organisation has `kerbside-api` and
     `kerbside-proxy`. Each carries twelve tags: `master` and
     `2026.2`, for `debian-trixie`, `rocky-10` and `ubuntu-noble`,
     each also as `-aarch64`. `master-debian-trixie` was last
     pushed on 2026-10-28.
   - Kolla master's `kolla/common/sources.yaml:111-114` builds
     `kerbside-base` from git at `https://github.com/shakenfist/kerbside`,
     reference `develop`. So these images carry whatever Kerbside's
     `develop` held when Kolla last built, not a release. Kerbside's
     own install advice is to take a tagged release
     (`installation.md` "Installing with pip").
   - The images
     (`kolla/docker/kerbside/kerbside-{base,api,proxy}/Dockerfile.j2`)
     are thin. Each is Kolla's `openstack-base` with a `kerbside` user
     and Kerbside pip-installed. Each runs as `kerbside`. Each starts
     through Kolla's `kolla_start`, which reads a `config.json` that
     a Kolla-Ansible role normally writes. The images hold no
     Kerbside configuration and no entrypoint of Kerbside's own.
   - The master plan's inventory says "Kolla's image is the only
     published Kerbside image, and it is built around Kolla's
     `config.json` and `kolla_start` conventions". #553's body adds
     that it "is not usable outside a Kolla deployment". The first
     is true but misses where the image is published and what it
     contains. The second is stronger than the code supports: the
     image can start with a hand-written `config.json`, but nothing
     designs it for that and nothing tests it. The master plan's
     inventory row is corrected. #553 is step 4b's.

2. **`docs/deployment/kolla-ansible.md` overstates what has to be
   built.** Its line 16 says "images built from kerbside-patches",
   and its "What it leaves to you" (around line 75) says images are
   left to the operator, with kerbside-patches holding the build
   scripts. Kolla publishes `kerbside-api` and `kerbside-proxy`
   itself (finding 1). The image the role also needs,
   `nova-libvirt-spice`, is in Kolla master
   (`kolla/docker/nova/nova-libvirt-spice/`) but is not on quay.io:
   its repository query is refused, where `nova-libvirt`'s
   succeeds. So "build your own" is true of libvirt and not of
   Kerbside. Step 4a corrects the page. The master plan needs no
   change here.

3. **`demo/` is already candid about why it is not production.**
   `installation.md`'s "What the demo is not" (line 343) lists the
   shortcuts: no real authentication, a self-signed CA, loopback
   only, two processes in one container, a plaintext backend leg,
   and a static source. `demo/README.md` "Things worth knowing"
   and the comments in `demo/Dockerfile`, `demo/docker-compose.yml`
   and `demo/entrypoint.sh` give the reasons. What none of them
   says is what a production-shaped container deployment would do
   instead. The page adds that, and links the list rather than
   restating it (decision 3). The things a production compose would
   have to settle that the demo does not are:
   - **The image.** The demo builds one at `docker compose build`,
     from PyPI, with build dependencies left in. Its comments give
     the reason: `mysqlclient` has no wheel.
   - **The split.** The daemon and the Rust proxy it launches share
     `API_SOCKET_PATH`, so they share a container. The API reaches
     the daemon only through the database, so it can run anywhere.
   - **Configuration and secrets.** These arrive as `KERBSIDE_*`
     environment variables, as `kerbside.ini` and as `sources.yaml`.
   - **The certificate lifecycle.** The demo's certificates last 30
     days, and the demo regenerates them inside the container.

4. **Nothing exists for Kubernetes.** There is no chart, manifest
   or operator anywhere in the tree, and no mention of Kubernetes or
   Helm in `docs/` outside the plans and the index row.

5. **The `demo-compose` lane is as the index says:** advisory,
   on push and pull requests, path-filtered
   (`.github/workflows/demo-compose.yml`; `docs/testing.md:97`).
   Its run builds with `KERBSIDE_SOURCE=/src`. The PyPI default the
   demo documents is not run by any lane, and `docker-compose.yml`
   says so itself.

6. **Scaffolding is ready.** `docs/deployment/order.yml` has two
   entries, and the TLS claim guard scans `docs/deployment/*.md`.

## Decisions

1. **One short page with the standard four headings.** "Who this is
   for" answers the Kubernetes and compose question directly in its
   first paragraph: there is no first-party image (#553), no
   production compose file, and nothing for Kubernetes.
   "What it deploys" covers the two things that do exist, the demo
   stack and Kolla's published images, and says plainly what each
   is for.

2. **No production compose file, even as an example.** A compose
   file needs an image to name. The only candidates are the demo's
   locally built one, which its own comments say is not for
   production, and Kolla's, which are built from `develop` and
   started by `kolla_start`. An example built on either would be a
   second, untested deployment mechanism with a page vouching for
   it. The page says what such a file would have to settle (finding
   3), and #553 is where the file belongs once an image exists. This
   is the decision a reviewer is most likely to dispute, because a
   sketch is cheap to write and operators ask for one.

3. **Link the demo's limitations, do not restate them.**
   `installation.md`'s "What the demo is not" is the list, and
   `demo/README.md` gives the reasons. The page adds only the four
   things in finding 3 that neither says. For the processes, the
   settings and the certificates, it links `installation.md`'s
   "What a running Kerbside needs" and `manual.md`, as phase 2
   decided for the by-hand page.

4. **Kolla's images are described accurately and not recommended
   outside Kolla.** The page says where they are published, that
   they are built from Kerbside's `develop`, how they start, and
   that nothing runs them outside Kolla-Ansible. It gives no recipe
   for running them standalone. The step verifies the `kolla_start`
   and `config.json` claims against Kolla's source, and whether
   Kolla's 2026.2 branch also builds from `develop`, rather than
   inferring either.

5. **`kolla-ansible.md` gets the image correction in the same step,**
   because it is the same fact on a neighbouring page (finding 2).
   That is a sentence or two on line 16 and in "What it leaves to
   you", and nothing else on that page changes.

6. **`docs/index.md`:** the Containers row becomes a link, and its
   description gains no new claims. The page carries the detail.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | opus | none | Write `docs/deployment/containers.md`, add `- containers.md: Containers` to `docs/deployment/order.yml` after `kolla-ansible.md`, correct `docs/deployment/kolla-ansible.md`'s image claims, and make `docs/index.md`'s Containers row a link. Read first: this plan (all of it), `docs/deployment/manual.md` and `docs/deployment/kolla-ansible.md` (form and tone, and the four `##` headings in order: `## Who this is for`, `## What it deploys`, `## How to use it`, `## Status and limitations`, the last a `Not covered \| Why` table), `docs/installation.md` "What a running Kerbside needs" and "Try it: the demo stack", including "What the demo is not", `demo/README.md`, and the comments in `demo/Dockerfile`, `demo/docker-compose.yml` and `demo/entrypoint.sh`. For Kolla, clone `https://opendev.org/openstack/kolla` shallow into your scratch directory, at master and at `stable/2026.2` if that branch exists. Read only. Check: `kolla/common/sources.yaml`'s `kerbside-base` entry on each branch; `kolla/docker/kerbside/*/Dockerfile.j2`; and how `kolla_start` and `config.json` work (`kolla/docker/base/start.sh`, `set_configs.py`, and `KOLLA_CONFIG_STRATEGY`). For the registry, `curl -s 'https://quay.io/api/v1/repository/openstack.kolla/kerbside-api?includeTags=true'` (and `kerbside-proxy`, `nova-libvirt-spice`, `nova-libvirt`), parsed with `python3 -I`. **Who this is for:** the operator who wants Kerbside in containers, compose or Kubernetes. Answer directly: no first-party image (link #553), no production compose file, no Kubernetes support. Point to the by-hand page (`manual.md`) and the Kolla-Ansible page for the mechanisms that exist. **What it deploys:** the demo stack (three services, its image built locally) by link, and Kolla's published images, described per decision 4: registry, tags, built from `develop` (and say what that means against `installation.md`'s release advice), `kolla_start`, no Kerbside configuration in the image. **How to use it:** the demo, by link only. Then a `###` on what a production-shaped container deployment has to settle, per finding 3 and decisions 2 and 3: the image, the split (say exactly which processes share a container and why, citing `API_SOCKET_PATH` in `docs/configuration.md`), configuration and secrets, and the certificate lifecycle. Do not restate "What the demo is not": link it. Then a `###` on Kubernetes: nothing exists, and it waits on #553. **Status and limitations:** at least no first-party image; the PyPI-default demo is run by no lane (finding 5); Kolla's images are built from `develop` and untested outside Kolla-Ansible; and no Kubernetes. In `kolla-ansible.md`, correct line 16 and the "What it leaves to you" images bullet per finding 2. Kerbside's images are published by Kolla; `nova-libvirt-spice` has to be built. Check the latter against quay on the day. In `docs/index.md`, link the Containers row to `deployment/containers.md` and change nothing else in it. State backend TLS only conditionally, as `rust/kerbside-proxy/src/backend.rs` behaves. Run `python3 tools/check-backend-tls-claims.py`, `timeout 900 python3 tools/mutate-backend-tls-claims.py`, `timeout 900 tox -epy3 -- kerbside.tests.unit.test_docs_links` and `pre-commit run --all-files` (after `git add -N` on the new page). Check every `#anchor` by hand: the link test strips anchors (#490). Commit subject: "Document Kerbside in containers." |
| 4b | low | (management session) | n/a | Post one comment on #553. It is published, so confirm the wording with Mikal first. Draft: Kolla now publishes `kerbside-api` and `kerbside-proxy` on quay.io (`openstack.kolla`, `master` and `2026.2` tags, three distributions, two architectures). They are built from Kerbside's `develop` branch, per Kolla's `sources.yaml`, not from a release. So "not usable outside a Kolla deployment" is better put as "not designed or tested for use outside Kolla": the image starts through `kolla_start` and a `config.json` that Kolla-Ansible writes. Link `docs/deployment/containers.md`, or this phase's pull request until it merges. Record the comment's URL in the execution notes. |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| A reader takes Kolla's published images as a supported way to run Kerbside outside Kolla. | Decision 4: the page gives no standalone recipe. It says the images are built from `develop`, and its limitations table says nothing tests them outside Kolla-Ansible. The management session reads the image paragraph for anything that reads as an endorsement before committing 4a. |
| The registry facts age: tags, distributions, the build reference. | The page dates them, and names Kolla's `sources.yaml` as the authority on what is built. The definition of done re-queries quay on the day. |
| The page restates "What the demo is not" anyway. | Decision 3. The definition of done checks that the page links that section and that none of its six row labels appears on the page as a heading or a table row. |
| The `kolla-ansible.md` correction swings too far and says no images need building. | Finding 2 separates the two cases. The management session checks the `nova-libvirt-spice` claim against quay before committing. |

## Definition of done

- [x] `docs/deployment/containers.md` exists with exactly the four
      `##` headings, in order:
      `grep '^## ' docs/deployment/containers.md`.
- [x] `docs/deployment/order.yml` lists `manual.md`,
      `kolla-ansible.md` and `containers.md`, in that order, and
      nothing else.
- [x] The page links #553 and `installation.md`'s
      `#what-the-demo-is-not`:
      `grep -c 'issues/553\|what-the-demo-is-not' docs/deployment/containers.md`
      is at least 2.
- [x] No row label of "What the demo is not" appears on the page as
      a heading or a table row's first cell. Checked by reading.
- [x] The page says Kolla's images are built from `develop`, and
      that matches Kolla master's `sources.yaml` on the day.
- [x] Every registry fact the page states matches a quay.io query
      made on the day, and the page dates them.
- [x] `kolla-ansible.md` no longer says Kerbside's images have to
      be built, and still says `nova-libvirt-spice` does:
      `grep -n 'built from kerbside-patches' docs/deployment/kolla-ansible.md`
      prints nothing.
- [x] The master plan's inventory row for the container image names
      quay.io and `develop`.
- [x] The Containers row of `docs/index.md` links
      `deployment/containers.md`.
- [x] Every anchor in the changed pages resolves. Checked by hand,
      because the link test strips anchors (#490).
- [x] `tools/check-backend-tls-claims.py` exits 0, and
      `tools/mutate-backend-tls-claims.py` still reports every
      mutation caught.
- [x] `tox -epy3`, `tox -eflake8` and `pre-commit run --all-files`
      pass.
- [x] The #553 comment from step 4b exists, and its URL is in the
      execution notes.

## Execution notes

- Back brief answered on 2026-10-28 (AEDT). Mikal kept decision 2,
  approved step 4b's comment as drafted, and gave this session
  permission to push and open the pull request once the definition
  of done is checked.
- Step 4a re-queried quay.io on 2026-10-28. The x86-64 tags were
  pushed that day, and the `-aarch64` tags a week or so earlier. Kolla's
  `stable/2026.2` also builds `kerbside-base` from `develop`.
- The review checked the claims step 4a flagged as inferred.
  Kolla-Ansible sets `KOLLA_CONFIG_STRATEGY` for every container
  (`ansible/group_vars/all/common.yml`). kerbside-patches documents
  the `nova-libvirt-spice` build in `docs/building.md` rather than
  holding a script for it, so the page links that.
- Step 4b posted the approved comment on #553 on 2026-10-28:
  https://github.com/shakenfist/kerbside/issues/553#issuecomment-6460770776
- Definition of done checked on 2026-10-28. `tox -epy3` passed 556
  tests, and flake8, pre-commit and the TLS claim guard passed. The
  mutation tool caught all 12 mutations, and all 27 local anchors in
  the three changed pages resolve. quay.io and Kolla's `sources.yaml`
  were re-queried that day and match the page.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of the plan and how the work you intend to
do aligns with it. Two points need Mikal's answer before 4a runs:

- **Decision 2:** no production compose file, even as an example.
- **Step 4b's comment wording**, which is published on #553.
