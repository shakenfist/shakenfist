# Copyright 2019 Michael Still and contributors
"""Every stestr run in a tools/ shell script logs to stdout, not syslog.

``tox.ini`` sets ``SHAKENFIST_LOG_TO_STDOUT=1`` for every test
environment, so ``shakenfist_utilities.logs.setup()`` writes to stdout
and stestr captures the logging, showing it only for failing tests. A
shell script which runs stestr directly -- outside tox -- gets none of
that setenv, and ``logs.setup()`` falls back to
``SysLogHandler('/dev/log')``: every test's logging lands in the host's
journal instead. On the sfcbr CI runners that journal is shipped to
Loki, and ``tools/ci-enum-widening-test.sh`` doing exactly this put the
runner log stream over its ingest budget (issue #4725).

The defect class is "a script runs stestr without re-exporting what
tox.ini would have exported", so the guard is over the class rather
than the one script that was caught: every shell script under
``tools/`` whose executable text invokes stestr must export
``SHAKENFIST_LOG_TO_STDOUT=1`` -- the exact value ``logs.setup()``
compares against -- on an earlier line. The scan is scoped to ``*.sh``
because that is where the class lives; the Python tools under
``tools/`` do not invoke stestr.

Following ``test_ci_raw_creates.py``, the scan asserts its own floor:
the two scripts known to invoke stestr must be among what it found, so
a rename or a glob change cannot quietly turn this into a green run
over nothing.
"""

import glob
import os
import re

from shakenfist.tests import base


TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))),
    'tools')

# The exact line logs.setup() needs to have taken effect: it compares
# the environment value against the string '1', so 'true' or 'yes'
# would export something and still log to syslog.
EXPORT_RE = re.compile(r'^\s*export SHAKENFIST_LOG_TO_STDOUT=1\s*$')

# An stestr invocation in executable text: `stestr run ...` or
# `python -m stestr run ...`. Comment lines are excluded before this
# is applied, so a script merely talking about stestr does not count.
STESTR_RE = re.compile(r'(^|\s|")(stestr\s+run|-m\s+stestr)\b')
COMMENT_RE = re.compile(r'^\s*#')

# The scripts which ran stestr when this guard was written. The floor
# exists so the walk finding nothing reads as the walk being broken,
# not as the class being empty.
KNOWN_STESTR_SCRIPTS = {'check-api-declaration-guards.sh',
                        'ci-enum-widening-test.sh',
                        'mutate-nested-sweep.sh'}


def _stestr_lines(path):
    """1-indexed (line number, text) of each executable stestr invocation."""
    found = []
    with open(path) as f:
        for number, line in enumerate(f, start=1):
            if COMMENT_RE.match(line):
                continue
            if STESTR_RE.search(line):
                found.append((number, line.rstrip()))
    return found


def _first_export_line(path):
    """The 1-indexed line of the required export, or None."""
    with open(path) as f:
        for number, line in enumerate(f, start=1):
            if EXPORT_RE.match(line):
                return number
    return None


class StestrLogsToStdoutTestCase(base.ShakenFistTestCase):
    def test_every_stestr_script_exports_log_to_stdout_first(self):
        invokers = set()

        for path in sorted(glob.glob(os.path.join(TOOLS_DIR, '*.sh'))):
            stestr_lines = _stestr_lines(path)
            if not stestr_lines:
                continue

            name = os.path.basename(path)
            invokers.add(name)
            export_line = _first_export_line(path)

            self.assertIsNotNone(
                export_line,
                'tools/%s runs stestr outside tox without exporting '
                'SHAKENFIST_LOG_TO_STDOUT=1, so logs.setup() will install '
                'a SysLogHandler and every test logs to the host journal '
                '-- which the CI runners ship to Loki (issue #4725). '
                'Export exactly what tox.ini exports before the first '
                'stestr run.' % name)
            self.assertLess(
                export_line, stestr_lines[0][0],
                'tools/%s exports SHAKENFIST_LOG_TO_STDOUT=1 on line %d, '
                'after the stestr invocation on line %d (%r). The export '
                'has to come first or that run still logs to syslog.'
                % (name, export_line, stestr_lines[0][0],
                   stestr_lines[0][1]))

        missing = KNOWN_STESTR_SCRIPTS - invokers
        self.assertEqual(
            set(), missing,
            'The scan did not find an stestr invocation in %s. Either the '
            'script was renamed or stopped running stestr -- update '
            'KNOWN_STESTR_SCRIPTS -- or the scan has gone blind and this '
            'guard is passing over nothing.' % sorted(missing))
