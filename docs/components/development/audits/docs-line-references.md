# Audit: Documentation line references

## What we check

Documentation names code by symbol -- a function, a class, a file --
and never by line number. A reference such as `kerbside/main.py:92` <!-- audit-ok: docs-line-references -->
or `spice-channel.c:1987,2743-2746`, or a GitHub link anchored to a <!-- audit-ok: docs-line-references -->
line on a branch (`blob/develop/x.py#L10`), fails the check. <!-- audit-ok: docs-line-references -->

A line number goes stale on the next edit to the file it points
into, and that edit is nearly always in a pull request that never
touches the document. No review of a diff sees the reference drift,
and nothing renders it broken, so it keeps asserting something about
code that has moved: the reader who follows it lands on unrelated
lines, which is worse than no pointer at all. When kerbside's
use-case pages were cleaned up in October 2026, several of their
forty-odd references had already drifted -- two cited for the console
token and audit lookups pointed into a different function. A
function or class name survives nearly every edit and can be
searched for, and a reader who wants the line will find it in
seconds.

This is why the rule is an audit and not only a review item. A
per-change review, `PUSH-AUDIT.md` included, can stop a pull request
adding a reference, but it cannot see one going stale, because the
change that breaks it is to the code, and the document is not in
the diff. Only a sweep of the whole tree finds drift.

Scope is the documentation content files: the top-level
`README.md`, `AGENTS.md` and `ARCHITECTURE.md`, and every markdown
file under `docs/` except those under a `plans/` directory and the
repository's `doc_content_excludes`. Plans are out because a plan is
a dated record of the code as it was when it was written, and a line
number there was accurate for that date. References into other
projects' source (spice-gtk, libvirt, an upstream action) are in
scope: an unpinned upstream drifts exactly as the repository's own
code does.

Four shapes are not flagged:

* **Fenced blocks.** Quoted tool output -- a linter message, a JSON
  example with a `location` field -- carries locations nobody is
  asked to follow.
* **A location with a column** (`app.rs:278:17`), which is quoted
  compiler, panic or linter output, even inline.
* **A GitHub permalink pinned to a full commit sha.** It addresses
  an immutable file and cannot drift.
* **Generated consistency-audit blocks**, and any line carrying an
  `audit-ok: docs-line-references` comment, for the rare line that
  genuinely needs the number.

A repository with no documentation content is N/A.

## Template

No template. Fix each reference at its source:

* name the enclosing function, method or class instead, with the
  file it lives in where that helps -- `_parse_sources()` in
  `kerbside/main.py`;
* drop the reference where the prose already says what the code
  does and the pointer adds nothing for the reader;
* if the exact lines matter, link a GitHub permalink pinned to a
  commit sha, which records which version the text describes.

A repository can stop new references at commit time with a
`pygrep` hook in `.pre-commit-config.yaml`. Kerbside's is the
reference copy:

```yaml
- repo: local
  hooks:
    - id: docs-line-references
      name: No source line-number references in documentation
      language: pygrep
      entry: >-
        \b[\w./-]+\.(py|pyi|rs|go|sh|proto|js|ts|c|h|cc|cpp|toml|ya?ml|j2|html):\d+(?:[-,]\d+)*(?![\d:])|/blob/(?![0-9a-f]{40}/)[^\s)#]+#L\d+
      types: [markdown]
      exclude: (^|/)plans/
```

The hook is advisory -- it can be skipped, and it does not know
about fences or generated blocks -- so this audit remains the
backstop.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#docs-line-references).
