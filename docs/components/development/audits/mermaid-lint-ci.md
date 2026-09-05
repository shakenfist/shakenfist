# Audit: mermaid diagrams linted in CI

## What we check

A repository whose markdown contains any `mermaid` fence carries
`tools/mermaid-lint.sh` and runs it from a CI workflow.

Mermaid fails at *render* time, not at commit time. A diagram with a
syntax error commits cleanly, passes actionlint, shellcheck, flake8
and skillsaw alike, and then shows an error box on GitHub and nothing
at all on the mkdocs sites. Nothing else in CI reads a diagram, so
until this check existed a broken diagram was found by whoever next
looked at the page.

Both halves are required. The script alone is a thing nobody runs, and
a workflow step alone would mean each project inventing its own
invocation of a container -- the point of shipping the wrapper is that
the docker arguments, the entrypoint override and the exit-status
handling are written once.

A diagram is recognised by a ```` ```mermaid ```` fence: backticks,
with no space before the language. That is deliberately narrower than
markdown allows, because it is what `mmdc` recognises -- a
`~~~mermaid` block renders nothing and exits zero, so counting one
would mark a repository covered for a diagram its linter never sees.

The script draws the same line, and then goes further than the audit
can: where the audit merely declines to count a fence `mmdc` cannot
read, `tools/mermaid-lint.sh` refuses it. There are two such fences --
a tilde-fenced block, and one written as ```` ``` mermaid ```` with a
space before the language. GitHub renders both as diagrams even though
`mmdc` reads nothing in either, so failing open would ship an unlinted
diagram through the exact gap the linter exists to close, with the run
printing "nothing to lint" and exiting zero -- a failure wearing the
shape of a success. Instead the script names the file, the line and
what to change and exits 1, alongside any parse errors from the same
run; a refusal outranks the renderer's status, so a broken diagram is
never reported under a failed image pull's 125.
`MermaidLintScriptTest` in `scripts/tests/` pins that behaviour, and
pins the audit's narrower answer next to it.

The two therefore give deliberately different answers to the same
input, and a repository whose only diagrams are tilde-fenced sees
both: N/A here, red in the lane. That is the intended direction. The
reason the audit does not count such a block was that counting one
would call the repository covered for a diagram nothing renders;
refusing the block removes the diagram rather than the coverage.

The script classifies fences by tracking fence state rather than by
matching lines, so a fence shown inside a longer fence is an example
rather than a diagram -- otherwise a page documenting this rule would
fail the repository that wrote it. Nesting is the only way to quote a
fence: indented code blocks are deliberately not modelled, because
four spaces before a fence is far more often a diagram inside a list
item, which must still be linted, than a diagram being quoted. Prose
is safe by a separate rule -- a backtick fence's info string may not
contain a backtick, so a line opening with an inline code span is not
a fence -- without which such a line would open one that never closes
and hide every diagram below it.

Blockquotes are the one place the two halves agree and both are
wrong. Neither the regex nor the script looks past a leading `>`, so
a diagram inside a blockquote is N/A here and skipped rather than
refused there, while GitHub renders it and `mmdc` reports "No mermaid
charts found" for the same file -- measured, not assumed. It is
therefore the fail-open shape the tilde and spaced refusals exist to
close, left open deliberately: because the two halves agree, no
repository is called covered for a diagram nothing renders, and
refusing a blockquoted fence means deciding what a fence nested
inside a blockquoted fence is, which is a rule with its own blind
spot. Blockquoted diagrams are vanishingly rare; a repository that
grows one should promote it to the top level rather than wait for
this to be modelled.

The audit's regex is a line match and has no notion of nesting at
all, which is a divergence with no consequence: a repository whose
only ```` ```mermaid ```` fence is a nested example is asked for a
linter that then finds nothing to lint, and passes.

Repositories with no mermaid diagrams are N/A. This is a check on
diagrams that exist, not a requirement that every project have some;
`diagram-format` is what moves a repository from hand-drawn diagrams
into this check's scope.

### Why a container

`mmdc` renders through puppeteer and so needs a browser. Running it
from the upstream image keeps chromium and a node toolchain off the
runners, and renders exactly what the sites will render.

There is no lighter path worth taking. mermaid's own `parse()` under
plain node throws `DOMPurify.addHook is not a function` for
`flowchart` and `stateDiagram-v2`, the two most common types in this
fleet, so a DOM-free checker reports false failures on exactly the
diagrams that matter; supplying a DOM with jsdom pulls in an undici
newer than the runners' node.

The cost is smaller than it looks. The image is cached after its first
pull, and rendering is about 1.4 seconds per file amortised inside a
single container -- ryll's seven diagram-bearing files in ten seconds,
ryll and kerbside together in twenty-two. Nearly all of the real cost
is the virtual machine the job needs, which is why the shipped
workflow is path-filtered to markdown.

### The runner

`[self-hosted, vm, debian-12-docker, s]`, not `static`. Static runners
have no docker daemon. The label must also appear in
`.github/actionlint.yaml`, or actionlint fails on the workflow.

## Template

Template: `templates/mermaid-lint/`
See: `templates/mermaid-lint/README.md`

Both files copy directly with no per-project substitution. A project
that already has a CI gate job may prefer to add the script as a step
there instead of taking the shipped workflow; the template's README
covers that, and why a path-filtered workflow must not simply be made
a required status check.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#mermaid-lint-ci).
