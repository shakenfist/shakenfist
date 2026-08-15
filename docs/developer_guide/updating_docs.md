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
