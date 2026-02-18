# A more structured and less verbose approach to logging

## Prompt

Before responding to questions or discussion points in this
document, explore the occystrap codebase thoroughly. Read relevant
source files, understand existing patterns (project structure,
command-line argument handling, input source abstractions, output
formatting, error handling), and ground your answers in what the
code actually does today. Do not speculate about the codebase when
you could read it instead. Where a question touches on external
concepts (OCI image specs, Docker/Podman compatibility, registry
APIs), research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

## Situation

`occystrap` is an interesting and useful tool which has grown
organically over time. It lacks a consistent approach to what
warrants the various levels of log message, and is therefore
overly verbose during normal operation. Log messages also lack
structure -- a message may say "filtered N elements from layer",
but it does not specify _which_ layer.

## Mission and problem statement

`occystrap` already depends on Shaken Fist's structured logging
library in `shakenfist_utilities.logs`, but does not use the
`with_fields()` functionality. It is also fair criticism to say
that the logging library does not do a great job of rendering
`with_fields()` information sensible for console scripts like
`occystrap`, so it is possible we need to perform some cleanups
in the shared code as well.

For every `LOG` call in `occystrap` we should audit the log message
to ensure that it:

* Provides relevant context: the layer it is acting on, the
  filter being applied if relevant, and so on.

* Log message level: we massively overuse `info()` level for
  example, which should only be used for messages a user would
  actually care about either because they're actionable, or
  directly speak to their request. Simply starting to process a
  layer for example should be at `debug()` level. A filter
  _changing_ a layer should be at `info()`. Obviousy errors and
  warnings should remain at those facility levels as well.

* Many log lines are painful to read because they have been
  wrapped too tightly. We can fix this by pre-loading context
  into local variables (see the example below), using f-strings,
  and by wrapping lines at 80 characters. We often wrap much
  earlier right now.

### Logger initialization consistency

Currently only `main.py` uses
`shakenfist_utilities.logs.setup_console()` to initialize its
logger. Every other module uses plain
`logging.getLogger(__name__)` and often manually calls
`LOG.setLevel(logging.INFO)`. This means `with_fields()` is not
available on those loggers. As part of this work, all modules
should be migrated to use `shakenfist_utilities.logs` consistently
so that `with_fields()` is available everywhere. The manual
`setLevel()` calls should also be removed -- log level should be
controlled centrally by the CLI entry point, not scattered across
individual modules.

### Cleaning up `--verbose`

The current `--verbose` flag sets the root logger and all its
handlers to `DEBUG`, which turns on debug output for every
library (`requests`, `urllib3`, etc.) -- this is extremely noisy
and buries `occystrap`'s own debug messages. As part of this
work, `--verbose` should be scoped to just the `occystrap.*`
loggers. If there is a need for the full firehose (including
third-party library debug output), a separate `--debug` flag
could be added for that purpose.

### Progress bars for slow operations

For operations we know will be slow (e.g. downloading large
layers), we should use `tqdm` progress bars rather than periodic
log messages. `tqdm` is already a dependency of `client-python`
in the Shaken Fist ecosystem with an established usage pattern,
so this is a natural fit.

Key design points for `tqdm` integration:

* **Stacked progress bars for parallel downloads.** `tqdm`
  supports multiple simultaneous progress bars via the
  `position` parameter. Each concurrent download gets a unique
  `position` value (0, 1, 2, ...) so bars stack vertically
  without overwriting each other -- similar to how Docker
  displays parallel layer pulls.

* **Interactive detection.** `tqdm` supports automatic TTY
  detection via `disable=None`, which checks
  `sys.stderr.isatty()`. When running in a non-interactive
  context (piped to a file, CI, cron), the progress bars are
  suppressed entirely and we should fall back to periodic log
  messages instead. Example:

  ```python
  from tqdm import tqdm

  progress = tqdm(
      total=layer_size,
      desc=f'Layer {digest[:12]}',
      unit='B',
      unit_scale=True,
      disable=None,  # auto-detect TTY
      position=layer_index
  )
  ```

* **Logging integration.** `tqdm` and Python's `logging` module
  both write to `stderr` by default, which causes garbled output
  when log messages fire during an active progress bar. `tqdm`
  provides `tqdm.contrib.logging.logging_redirect_tqdm()` as an
  official solution -- this context manager temporarily redirects
  log output through `tqdm.write()`, which clears the progress
  bar before printing and redraws it afterward.

* **Non-interactive fallback.** When `tqdm` is disabled (non-TTY),
  we should emit periodic `LOG.info()` progress messages instead
  (e.g. every 30 seconds or every 10% of layer size) so that
  scripts and CI logs still show that work is happening.

Handling interactive vs non-interactive progress bars, as well
as periodic messages during non-interactive use should be handled by
a single helper wrapper to ensure consistency of usage.

An example of using a local variable for context and to save
space when logging:

```python
def do_thing_to_layer(layer, thing):
    log = LOG.with_fields({
        'layer': layer,
        'thing': thing
    })


    log.debug('Starting processing')
    ...
    if layer changed:
        log.info('Layer altered by filter')
    ...
    log.debug('Finished processing')
```

## Execution

Please scan the `occystrap` code, thinking both about what a
human user needs to know in normal day to day operation, as well
as what an LLM might need to proccess. Remember that when things
go wrong we can always use `--verbose` to display debug messages.
However, humans are impatient sorts, so for example if its been
a long time since we last emitted a progress message then a user
might be concerned about a crash and might welcome a message we
would otherwise think wasn't particularly important.

### Testing strategy

Testing is hard here. I think perhaps enforcement is the right
way to ensure we don't end up back in a bad state. I am not sure
what that looks like though. `black` might help with line wrapping,
but is likely onerous in terms of other formatting changes and
enforces a style we may not fully agree with. Perhaps unit tests
should assert that not "too many" log messages are emitted? I am
open to suggestions here.

The following approaches should be used in combination:

* **Log-level linting via a pre-commit script.** A simple script
  that scans for `LOG.info(` calls per file and flags any file
  that exceeds a threshold. This won't catch _wrong_ levels, but
  it catches verbosity regressions where someone adds many new
  `info` calls without thinking about whether they should be
  `debug`.

* **Integration test log capture.** Rather than asserting exact
  log message counts (which is fragile), assert that a standard
  pull operation at `INFO` level produces fewer than N lines of
  output. This catches verbosity creep without being tied to
  specific messages.

* **`with_fields()` enforcement.** A grep-based pre-commit check
  that warns if a new `LOG.info/debug/warning/error` call appears
  in a function that does not have a prior `with_fields()` call.
  This is imperfect but useful as a nudge toward structured
  logging.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The actionability of log messages has improved.
* We have some form of precommit enforcement to keep us honest
  in the future.
* We are using `with_fields()` when appropriate.
* All modules use `shakenfist_utilities.logs` consistently for
  logger initialization -- no manual `setLevel()` calls remain.
* `--verbose` is scoped to `occystrap.*` loggers only.
* Slow operations (large layer downloads) use `tqdm` progress
  bars in interactive sessions, with a log-based fallback for
  non-interactive contexts.

### Implementation summary

This plan has been implemented across six commits:

1. **Logger initialization consistency** (`e20ae2d`): Migrated all
   modules to `shakenfist_utilities.logs.setup_console(__name__)`,
   removed manual `setLevel()` calls, monkeypatched
   `ConsoleLoggingHandler` to write to stderr.
2. **Log level audit and with_fields() adoption** (`bf0edc4`):
   Downgraded ~60 `LOG.info()` calls to `LOG.debug()` across 13
   files. Added `with_fields()` structured summaries to the four
   main pipeline endpoints.
3. **--verbose cleanup and --debug flag** (`87dd747`): Scoped
   `--verbose` to `occystrap.*` loggers only. Added `--debug` for
   the full firehose including library output.
4. **tqdm progress wrapper** (`0656684`): Created
   `occystrap/progress.py` with `LayerProgress` (tqdm in TTY,
   periodic log in non-TTY) and `redirect_logging()`. Integrated
   into registry download and upload paths.
5. **Pre-commit enforcement** (`6f77532`): Added
   `tools/check-log-levels.sh` pre-commit hook enforcing max 10
   `LOG.info()` calls per file.
6. **Documentation updates**: Updated README.md, AGENTS.md, and
   ARCHITECTURE.md to reflect all changes.

### Future work

We should list obvious extensions, known issues, unrelated bugs
we encountered, and anything else we should one day do but have
chosen to defer to here so that we don't forget them.

* **Integration test log capture**: Assert that a standard pull
  at INFO level produces fewer than N lines of output (deferred
  as it requires functional test infrastructure).
* **`with_fields()` enforcement**: A grep-based pre-commit check
  that warns if new log calls lack structured context (deferred
  as the benefit is marginal given the log-level linting).
* **Unit tests for progress.py**: The `LayerProgress` class and
  `redirect_logging()` helper have no unit test coverage. Tests
  should verify TTY vs non-TTY behavior, update intervals, and
  context manager lifecycle.
* **Unit tests for --verbose/--debug flags**: The CLI flag logic
  in `main.py` that scopes logger levels has no test coverage.

### Bugs fixed during this work

*None tracked at this time.*

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
