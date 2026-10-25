# Copyright 2026 Michael Still and contributors
"""Every sf_api.error() inside an except block suppresses the traceback.

The banner at the top of ``external_api/app.py`` says to pass
``suppress_traceback=True`` to ``error()`` inside an expected exception
block, because ``shakenfist_utilities.api.error()`` otherwise formats
the live exception and logs a full traceback at INFO. An ordinary
client-state refusal -- rebooting a powered off instance, a malformed
address, a bad max_versions -- then pollutes every "tracebacks are
bugs" signal, and because the error-mining key over that logger is a
catch-all, a later real traceback through it is masked (#4523). Six
instance lifecycle 409s did exactly that for two months before anyone
noticed, and a sweep for the fix found thirteen more sites across
eight files.

So the rule is held closed mechanically: an ``sf_api.error()`` call
lexically inside an ``except`` handler, anywhere under
``shakenfist/external_api/``, must pass a literal
``suppress_traceback=True``. A site which genuinely wants the
traceback recorded should log it deliberately -- the way
``suppress_exceptions_to_client()`` in ``base.py`` calls
``LOG.exception()`` and then suppresses -- rather than leave it to the
error helper's accident of timing.

The check parses source with ``ast`` rather than importing the API,
following ``test_ci_raw_creates.py``'s pattern. Only calls count:
a reference to ``sf_api.error`` logs nothing by itself. The keyword's
value must be the constant ``True`` -- an explicit ``False`` is the
defect spelled out, and a variable is unverifiable here, so both fail
closed.
"""

import ast
import glob
import os

from shakenfist.tests import base


EXTERNAL_API = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'external_api')

# There were 22 files and 48 guarded call sites when this was written.
# The floors sit a long way below that: their job is to catch a glob
# which found nothing or almost nothing, not to track the counts.
MINIMUM_API_FILES = 10
MINIMUM_GUARDED_SITES = 25


def _is_sf_api_error_call(node):
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'error'
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'sf_api')


def _suppresses_traceback(call):
    """True only for a literal suppress_traceback=True.

    An explicit False is the defect spelled out rather than an
    exemption, and a non-constant value cannot be verified from the
    source, so both fail closed.
    """
    for keyword in call.keywords:
        if keyword.arg == 'suppress_traceback':
            return (isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True)
    return False


def find_error_calls_in_except_blocks(source):
    """(lineno, suppressed) for each sf_api.error() call in a handler.

    Nested except handlers are both walked, so a call inside an inner
    handler is seen from the outer one too; the set makes each site
    count once.
    """
    tree = ast.parse(source)
    sites = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for inner in ast.walk(node):
            if _is_sf_api_error_call(inner):
                sites.add((inner.lineno, _suppresses_traceback(inner)))
    return sorted(sites)


def find_unsuppressed_error_calls(source):
    """Line numbers of except-block error() calls which log a traceback."""
    return [lineno for lineno, suppressed
            in find_error_calls_in_except_blocks(source)
            if not suppressed]


class ErrorSuppressesTracebackTestCase(base.ShakenFistTestCase):
    def test_the_check_recognises_an_unsuppressed_call(self):
        """The positive control: without this a clean scan proves nothing."""
        offences = find_unsuppressed_error_calls(
            "try:\n"
            "    parse()\n"
            "except ValueError as e:\n"
            "    return sf_api.error(400, str(e))\n")
        self.assertEqual([4], offences)

    def test_a_suppressed_call_is_accepted(self):
        offences = find_unsuppressed_error_calls(
            "try:\n"
            "    parse()\n"
            "except ValueError as e:\n"
            "    return sf_api.error(400, str(e), suppress_traceback=True)\n")
        self.assertEqual([], offences)

    def test_an_explicit_false_is_an_offence(self):
        """suppress_traceback=False in an except block is the defect
        spelled out, not a decision this guard defers to."""
        offences = find_unsuppressed_error_calls(
            "try:\n"
            "    parse()\n"
            "except ValueError as e:\n"
            "    return sf_api.error(400, str(e), suppress_traceback=False)\n")
        self.assertEqual([4], offences)

    def test_a_non_constant_value_fails_closed(self):
        offences = find_unsuppressed_error_calls(
            "try:\n"
            "    parse()\n"
            "except ValueError as e:\n"
            "    return sf_api.error(400, str(e), suppress_traceback=quiet)\n")
        self.assertEqual([4], offences)

    def test_a_call_outside_an_except_block_is_not_checked(self):
        """Outside a handler there is no live exception for error() to
        format, so there is nothing to suppress."""
        offences = find_unsuppressed_error_calls(
            "if not name:\n"
            "    return sf_api.error(400, 'name must be specified')\n")
        self.assertEqual([], offences)

    def test_a_call_in_a_nested_handler_is_reported_once(self):
        """ast.walk() reaches an inner handler's call from the outer
        handler too, so without deduplication every nested site -- the
        symbolic mode fallback in instance.py is one -- would be
        reported twice."""
        offences = find_unsuppressed_error_calls(
            "try:\n"
            "    int(mode)\n"
            "except ValueError:\n"
            "    try:\n"
            "        symbolic(mode)\n"
            "    except ValueError as e:\n"
            "        return sf_api.error(406, str(e))\n")
        self.assertEqual([7], offences)

    def test_no_unsuppressed_error_calls_in_the_api(self):
        """The real guard: every except-block error() under
        shakenfist/external_api/ suppresses the traceback."""
        found = []
        guarded = 0
        paths = sorted(glob.glob(os.path.join(EXTERNAL_API, '*.py')))
        for path in paths:
            with open(path) as f:
                source = f.read()
            sites = find_error_calls_in_except_blocks(source)
            guarded += len(sites)
            for lineno, suppressed in sites:
                if not suppressed:
                    found.append('%s:%d' % (os.path.basename(path), lineno))

        # glob over a directory which is not there yields nothing and
        # raises nothing, so a clean result has to be shown to be a
        # result before it is trusted.
        self.assertGreater(
            len(paths), MINIMUM_API_FILES,
            'The scan found %d Python files under %s, so an empty '
            'offence list below would mean nothing.'
            % (len(paths), EXTERNAL_API))
        self.assertGreater(
            guarded, MINIMUM_GUARDED_SITES,
            'The scan found %d sf_api.error() calls in except blocks, '
            'so an empty offence list below would mean nothing.' % guarded)

        self.assertEqual(
            [], found,
            'An sf_api.error() call inside an except block does not pass '
            'suppress_traceback=True, so an ordinary refusal there logs a '
            'full traceback at INFO and pollutes the "tracebacks are '
            'bugs" error mining (#4523). Pass suppress_traceback=True; if '
            'the traceback is genuinely wanted, log it deliberately with '
            'LOG.exception() first. Sites: %s' % ', '.join(found))
