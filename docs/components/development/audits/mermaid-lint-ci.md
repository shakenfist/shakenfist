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
`tools/mermaid-lint.sh` greps for the same shape, and a test asserts
the two definitions stay together.

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
