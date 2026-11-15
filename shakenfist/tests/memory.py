# Copyright 2026 Michael Still and contributors

"""Attribute a stestr worker's memory growth to the test which caused it.

``OS_TEST_TIMEOUT`` bounds how long a single test may take. Nothing
bounded how much it may allocate, so a leaking test still passed and
the worker carried the leak into every test scheduled after it -- once
as far as 16.3 GB resident plus 5.9 GB of swap, which held a
workstation in direct reclaim for an hour (issue #4928).
``tools/bounded-test-run.sh`` turns that into a fast failure; this turns
the failure into a test name.

Two bounds are read from one sample, because the growth has two shapes:

* a *growth* limit, for the test which allocates a great deal and keeps
  it, and
* a *ceiling* on the worker, for a leak spread thinly across many tests
  where no single test is over the growth limit. That one names a test
  which may be a victim rather than the culprit, which is still a point
  in the run and a readable message rather than a ``MemoryError`` raised
  inside whichever library happened to be allocating next.

The sample is taken after the test's own cleanups have run, so a
tempdir or a mock patcher released on teardown is not counted. Both
thresholds are loose: the worst-behaved test in the suite as this was
written grew a worker by 63 MB, because importing a module and parsing
the tree are charged to whichever test first did it. These catch a
runaway; they are not a budget.
"""

import gc
import os
import resource


PAGE_SIZE = resource.getpagesize()
MEGABYTE = 1024 * 1024

DEFAULT_GROWTH_LIMIT_MB = 256
DEFAULT_CEILING_MB = 1024

# Once a worker is over the ceiling it stays over it, and every test
# after this one would fail for the same reason. The first one is the
# signal, so the rest are left alone -- the run's bound is what stops
# the worker, not this.
_ceiling_reported = False

ADVICE = (' An allocation a test still holds when it returns is carried '
          'into every test the worker runs after it -- see '
          'shakenfist/tests/memory.py and issue #4928.')


def resident_bytes(path='/proc/self/statm'):
    """This process's resident set size, or None where /proc is absent."""
    try:
        with open(path) as f:
            return int(f.read().split()[1]) * PAGE_SIZE
    except (OSError, IndexError, ValueError):
        return None


def _limit_bytes(environ, name, default_mb):
    """A megabyte limit from the environment, or None when disabled.

    The default lives here rather than in tox.ini's setenv, because a
    script which runs stestr outside tox gets none of that setenv and
    would silently run unguarded -- which is how issue #4725 happened.
    'none' or a value of zero or less turns the limit off. Anything else
    which will not parse falls back to the default rather than off, so a
    typo cannot quietly disable the guard.
    """
    raw = (os.environ if environ is None else environ).get(name, '').strip()
    if not raw:
        return default_mb * MEGABYTE
    if raw.lower() == 'none':
        return None
    try:
        megabytes = int(raw)
    except ValueError:
        return default_mb * MEGABYTE
    if megabytes <= 0:
        return None
    return megabytes * MEGABYTE


def growth_limit_bytes(environ=None):
    """How much one test may add, from SF_TEST_MEMORY_GROWTH_MB."""
    return _limit_bytes(environ, 'SF_TEST_MEMORY_GROWTH_MB',
                        DEFAULT_GROWTH_LIMIT_MB)


def ceiling_bytes(environ=None):
    """How large a worker may become, from SF_TEST_MEMORY_CEILING_MB."""
    return _limit_bytes(environ, 'SF_TEST_MEMORY_CEILING_MB',
                        DEFAULT_CEILING_MB)


def _exceeded(after, before, growth_limit, ceiling):
    if growth_limit is not None and after - before > growth_limit:
        return True
    return ceiling is not None and after > ceiling


def problem_since(before, environ=None):
    """Why this test's memory use is a problem, or None if it is not."""
    global _ceiling_reported

    growth_limit = growth_limit_bytes(environ)
    ceiling = ceiling_bytes(environ)
    if before is None or (growth_limit is None and ceiling is None):
        return None

    after = resident_bytes()
    if after is None or not _exceeded(after, before, growth_limit, ceiling):
        return None

    # Over a bound on the first reading. Python's cycle collector and
    # glibc's arenas both hold memory the test has finished with, so
    # collect and read again before blaming the test for it. Doing it
    # here rather than unconditionally keeps the collect off the path of
    # every test which is within both bounds.
    gc.collect()
    after = resident_bytes()
    if after is None or not _exceeded(after, before, growth_limit, ceiling):
        return None

    if growth_limit is not None and after - before > growth_limit:
        return ('This test grew the worker\'s resident set by %.0f MB, over '
                'the %.0f MB SF_TEST_MEMORY_GROWTH_MB allows.' % (
                    (after - before) / MEGABYTE, growth_limit / MEGABYTE)
                + ADVICE)

    if _ceiling_reported:
        return None
    _ceiling_reported = True
    return ('The worker\'s resident set has reached %.0f MB, over the %.0f MB '
            'SF_TEST_MEMORY_CEILING_MB allows. This test is not necessarily '
            'the one which grew it: the ceiling catches a leak spread across '
            'many tests, where no one test is over '
            'SF_TEST_MEMORY_GROWTH_MB.' % (
                after / MEGABYTE, ceiling / MEGABYTE)
            + ADVICE)
