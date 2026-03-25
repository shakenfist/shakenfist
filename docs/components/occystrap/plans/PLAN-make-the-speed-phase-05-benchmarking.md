# Phase 5: Benchmarking and tuning

## Prompt

Before responding to questions or discussion points in this
document, explore the occystrap codebase thoroughly. Read relevant
source files, understand existing patterns (pipeline architecture,
input/filter/output interfaces, URI parsing, CLI commands, registry
authentication, error handling), and ground your answers in what
the code actually does today. Do not speculate about the codebase
when you could read it instead. Where a question touches on
external concepts (Docker Registry V2, OCI specs, container image
formats, compression), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

Consult `ARCHITECTURE.md` for the pipeline pattern, element types,
input/filter/output interfaces, and cross-cutting concerns (layer
caching, parallel downloads, compression). Consult `CLAUDE.md` for
build commands and project conventions.

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass tests,
and have a clear commit message explaining what changed and why.

## Goal

Measure the performance improvements from Phases 1-4, tune
default thread pool sizes, and document performance
characteristics so users know how to get the best results
from `-j` and `-J`.

## Current state

Phases 1-4 introduced:

1. **httpx with HTTP/2** — connection pooling, multiplexed
   streams, reduced TLS handshake overhead
2. **Parallel Quay API resolution** — tag existence checks
   run concurrently via ThreadPoolExecutor
3. **Concurrent multi-image processing** — `-J` flag for
   processing multiple images simultaneously
4. **DirWriter os.rename** — zero-copy layer placement for
   same-filesystem temp dirs

There is no existing benchmarking infrastructure. The CI
functional tests verify correctness but don't measure
performance. We need a repeatable way to measure wall-clock
time across the key workflows and different `-j`/`-J` values.

## Design

### Benchmark script: `tools/benchmark.sh`

A shell script (per project convention — large scripts go in
`tools/`) that runs representative workflows and reports
timing. It requires a local Docker registry at localhost:5000
populated with test images (same setup as CI).

#### Workflows to benchmark

| ID | Command | Tests |
|----|---------|-------|
| `single-pull` | `registry://localhost:5000/library/ubuntu:latest` → `dir://` | Single image, network+disk |
| `single-tar` | `registry://localhost:5000/library/ubuntu:latest` → `tar://` | Single image, sequential output |
| `single-push` | `registry://localhost:5000/library/ubuntu:latest` → `registry://localhost:5000/bench/ubuntu:latest` | Mirror/push with compression |
| `bulk-quay-info` | `quay://projectquay/*:latest` info | API resolution speed |
| `multi-dir` | Multiple images → `dir://?unique_names=true` | Multi-image concurrency |

Each workflow runs with several `-j`/`-J` combinations to
find optimal defaults:

- `-j 1`, `-j 4` (current default), `-j 8`, `-j 16`
- `-J 1` (current default equivalent), `-J 3`, `-J 6`

#### Output format

The script outputs a simple TSV table:

```
workflow	j	J	wall_s	exit_code
single-pull	1	1	12.3	0
single-pull	4	1	4.7	0
...
```

This is easy to paste into a spreadsheet or process with
`awk`/`column -t`. A `--json` flag outputs JSON for
programmatic consumption.

#### Timing

Each test is timed with `time` (wall-clock). The output
directory is cleaned between runs to ensure a cold cache.
Layer cache is disabled (no `--layer-cache`) to measure
raw transfer performance.

### Performance tuning documentation: `docs/performance.md`

A new doc page covering:

1. **What `-j` controls** — per-image layer download
   parallelism. Higher values help with high-latency
   registries. Diminishing returns above 8 for most
   registries.
2. **What `-J` controls** — multi-image concurrency. Higher
   values help when processing many small images (e.g.,
   `quay://` bulk). Limited by registry rate limits.
3. **What `--rate-limit` controls** — requests per second
   cap. Required for aggressive `-j`/`-J` to avoid 429s.
4. **What `--retries` controls** — persistence with
   exponential backoff on transient failures.
5. **Recommended settings** — a table of scenarios with
   suggested `-j`/`-J`/`--rate-limit` values.
6. **Connection efficiency** — httpx with HTTP/2 means
   fewer TLS handshakes, multiplexed requests. No user
   action needed, just context for why things are faster.

### Default tuning

Based on benchmark results, we may adjust the default values
for `-j` and `-J` in `main.py`. Current defaults:

- `-j 4` — likely fine for most use cases
- `-J 3` — may need adjustment based on measurements

Any default changes will be a separate commit with
justification in the commit message.

## Open questions

1. **Should the benchmark script be runnable in CI?**

   *Recommendation:* Not in this phase. CI environments have
   variable performance (shared runners, network variability).
   The script is designed for local use on a developer machine
   with a local registry. We can add a CI benchmark job later
   if we want regression detection, but that requires a
   dedicated runner with consistent performance.

2. **Should we add a `--benchmark` flag to the CLI itself?**

   *Recommendation:* No. A separate script is more flexible
   and doesn't pollute the CLI with testing concerns. The
   script can be updated independently of the release cycle.

## Implementation steps

### Step 1: Create benchmark script

Write `tools/benchmark.sh` that:
- Checks for a local registry at localhost:5000
- Populates it with test images if needed
- Runs each workflow with each `-j`/`-J` combination
- Reports timing in TSV (default) or JSON (`--json`)
- Cleans output directories between runs

### Step 2: Create performance documentation

Write `docs/performance.md` covering the tuning knobs,
recommended settings, and how HTTP/2 connection efficiency
works under the hood.

### Step 3: Update docs index and README

Add a link to `docs/performance.md` from `docs/index.md`
and mention performance tuning in the README. Update
ARCHITECTURE.md if needed.

### Step 4: Run benchmarks and tune defaults

Run the benchmark script, analyze results, and adjust
defaults if warranted. Document the benchmark results and
rationale for any changes.

## Commit plan

1. **Add benchmark script and performance documentation.**
   Create `tools/benchmark.sh` and `docs/performance.md`.
   Update `docs/index.md`, README, and ARCHITECTURE.md.

2. **Tune defaults based on benchmark results.**
   Adjust `-j`/`-J` defaults if measurements justify it.
   Include benchmark results summary in commit message.

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Benchmark results vary across machines | High | Low | Document that results are relative, not absolute |
| Rate limiting during bulk benchmarks | Medium | Low | Use local registry for most tests |
| Default changes regress some workflows | Low | Medium | Only change defaults with clear evidence |

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `flake8 --max-line-length=120` and
  `pre-commit run --all-files`.
* New code follows the existing pipeline pattern (input/filter/
  output interfaces) where applicable.
* There are unit tests for core logic and integration tests for
  new CLI commands.
* Lines are wrapped at 120 characters, single quotes for strings,
  double quotes for docstrings.
* Documentation in `docs/` has been updated to describe any new
  commands or features.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated if the change adds or modifies modules or CLI commands.

### Future work

- CI-based benchmark regression detection with a dedicated
  stable runner.
- Flame graph / cProfile integration for identifying
  CPU-bound hotspots.
- Memory profiling for large bulk operations to ensure
  concurrent image processing doesn't exhaust RAM.

### Bugs fixed during this work

(None yet.)

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
