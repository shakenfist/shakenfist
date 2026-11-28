# Logging for tests is configured via the SHAKENFIST_LOG_TO_STDOUT environment
# variable, which tox.ini sets and this package defaults below for every run
# that does not go through tox. When set to '1',
# shakenfist_utilities.logs.setup() will write to stdout instead of the host's
# syslog. That stdout is also the stestr worker's subunit result stream, so
# ShakenFistTestCase redirects those handlers into memory for the duration of
# each test and reports what they captured only for a test which did not pass
# -- see shakenfist/tests/logcapture.py and issue #4991. A syslog handler which
# does get installed is contained per test on the same terms (issue #5106).
import os


# Default it here as well as in tox.ini, because tox.ini only covers the
# invocations that go through tox (issue #5107). The variable describes how the
# suite was invoked rather than the fact that a test is running, and every
# other path has to remember it independently: #4725 was a tools/ shell script
# running stestr directly, testenv:cover was a tox environment that did not
# inherit {[testenv]setenv}, and an IDE test explorer, a hand-typed
# `stestr run` in an activated venv or an agent driving the venv interpreter
# have no file in this repository to patch at all. Importing this package is
# the one thing all of them do, and discovery imports it before the modules
# under test, so logs.setup() builds the right handler the first time instead
# of logcapture having to swap one out afterwards.
#
# setdefault, not assignment: SHAKENFIST_LOG_TO_STDOUT=0 in the environment
# still reaches the syslog branch, for anyone deliberately exercising it.
#
# This is the cheap half of the fix. logcapture.py is the robust half -- it
# holds even for a handler installed during a test by a module imported for
# the first time inside it, which is after this has had its say. Neither
# replaces the other; see issue #5106.
os.environ.setdefault('SHAKENFIST_LOG_TO_STDOUT', '1')
