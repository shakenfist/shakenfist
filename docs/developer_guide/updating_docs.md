# Updating These Docs

Built using MkDocs: https://www.mkdocs.org/  
Theme: https://squidfunk.github.io/mkdocs-material/customization/  

## Setup

Install mkdocs and the material theme 
```bash
pip install mkdocs-material
```

## Viewing Locally

Start the live web-server with
```bash
mkdocs serve
```
View at http://localhost:8000

## Deploying to GitHub Pages

Build and deploy with
```bash
mkdocs gh-deploy
```
This will push to the `gh-pages` branch of the current git remote.

## Navigation Bar

The navigation bar is configured via the `mkdocs.yml` file in the repository root.

## Link Checking

`tools/check-doc-anchors.py` resolves every markdown link in `docs/` and in
the root markdown files (`AGENTS.md`, `ARCHITECTURE.md`, `CLAUDE.md`,
`README.md`), reporting links whose target file is missing and anchored links
whose target heading does not exist. Neither mkdocs nor the docs-tests
workflow notices either failure — the link still renders and drops the reader
at the top of the page — and the root files are not part of the mkdocs site
at all, so nothing else looks at their links.

It runs as a pre-commit hook and, because no workflow runs pre-commit, as
`shakenfist/tests/test_doc_anchors.py` in CI.

`docs/plans/` and `docs/components/` are excluded as link *sources*: plans are
point-in-time records which are not maintained after they land, and components
are synchronised in from the sibling repositories. Both remain valid targets.

## Plan Status Checking

`tools/check-plan-status.py` covers `docs/plans/` and the plan template, which
the link checker deliberately does not read. It recomputes what
`docs/plans/index.md` publishes about each master plan from the plan itself:

* **Statuses** come from the `plan-status-vocabulary` shared block in
  `PLAN-TEMPLATE.md` rather than a constant here, so a term renamed upstream
  cannot leave the checker rejecting a status the template tells authors to
  write. Every status cell in a master plan's Execution table, and every
  status in the index, must be one of those terms and nothing else.
* **The `Phases` column** is arithmetic over the plan's own Execution table,
  recomputed rather than trusted. The index status and that table also have
  to agree: a plan cannot be `Complete` with an unfinished phase, or
  `Proposed`/`Not started` with every phase resolved.
* **Registration** — every master plan appears in both `index.md` and
  `order.yml`, and neither names a plan which is not there.
* **Reachability** — every plan-to-plan link resolves, and every phase plan
  has an inbound link. This one matters because phase plans are deliberately
  absent from `order.yml` and the index lists master plans only, which leaves
  the master plan's Execution table as the only route to a phase document. A
  phase filename written as bare text rather than a link is a page nothing
  leads to; thirty-eight of ninety-two were in that state before the check
  existed.

Example tables and links in prose do not count: fenced code blocks are
skipped, and a table row must begin in column zero, which is what separates a
plan's own table from the indented one in the `plan-file-conventions` shared
block.

Like the link checker, it runs as a pre-commit hook and as
`shakenfist/tests/test_plan_status.py` in CI. The unit test job skips
docs-only changes, so `functional-tests.yml` carries a `plans` paths filter
alongside `code` — without it the guard would skip on precisely the changes
it polices.
