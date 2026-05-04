# Instar Commentary

*A Lions-style commentary on the instar codebase.*

This directory contains an annotated walkthrough of instar's source code,
inspired by John Lions' *Commentary on UNIX 6th Edition* (1977). Lions
annotated the ~9,000 lines of the V6 UNIX kernel so that students could
understand not just *what* the code did, but *why* it was written that way.

We take the same approach here, but adapted for a living codebase. Rather
than annotating individual source lines (which would drift with every
commit), we organise the commentary around concepts, data flows, and
architectural decisions -- things that change slowly even when the code
changes fast. Where we reference code, we use struct/function names rather
than line numbers.

## Documents

| Document | Purpose |
|----------|---------|
| [Reading Order](/components/instar/commentary/reading-order/) | Which files to read, in what sequence, and what to look for in each |
| [Architectural Decisions](/components/instar/commentary/architectural-decisions/) | The *why* behind every major design choice |

## How to keep this up to date

The commentary is structured so that architectural-level content (which
changes rarely) is separated from code-level references (which change
more often). When preparing a PR for push, add a check:

> Has the commentary in `docs/commentary/` been reviewed against this
> change? If architectural decisions or data flow have changed, update
> the relevant commentary document.

Claude Code can also help: after a significant change, ask it to review
whether the commentary sections covering the affected modules still
accurately describe the code.

## Scope

The commentary covers the production code in `src/`. The `prototypes/`
directory is historical scaffolding -- it tells the story of how the
architecture was discovered, but the commentary focuses on where the code
ended up, not how it got there. The technology primer
(`docs/technology-primer.md`) provides the background knowledge assumed
by the commentary; read it first if you are not familiar with KVM, virtio,
or bare-metal programming.
