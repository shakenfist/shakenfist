# Copyright 2019 Michael Still and contributors
"""The namespace ownership predicate is called, never restated.

`docs/developer_guide/coding_rules.md` opens with "Never restate a
visibility predicate", and it opens with that because restating one
shipped a bug: `requires_artifact_access` spelled out the ownership test
as `if a.shared and requestor not in [a.namespace, 'system']`, which is
inverted in both directions, and since `arg_is_artifact_ref` resolves a
uuid straight to `Artifact.from_db` with no namespace filter, that
decorator was the only thing between a guessed uuid and another
tenant's artifacts.

The rule has always had a function to call for the artifact *access*
predicate, `namespace_or_shared_filter`. It did not have one for the
*ownership* predicate until `api_base.namespace_owns()`, so until then
`request_namespace() not in [obj.namespace, 'system']` was open-coded
at twelve sites across four object types -- instances, networks,
artifacts and labels -- plus the agent operation surface and the
namespace a network create names. Every one of them now calls the
helper, and this guard is what keeps it that way: a thirteenth
restatement fails the build rather than waiting to be noticed by a
reviewer who happens to remember the rule.

The shape matched is deliberately wider than the exact expression that
was there. Any membership test against a literal containing `'system'`
is reported, whichever way round it is written and whatever the other
element is, because the bug the rule records was a restatement that had
been *edited* -- an exact-string check would have passed the inverted
version happily.

One site is exempt, and it is the helper's own body
(`api_base.namespace_owns`), found by function name rather than by line
number so that moving it does not silently widen the exemption. Adding
a second exemption means editing this file, which is the point: the
discussion happens in review rather than in a comment nobody reads.

Source is parsed with `ast` rather than grepped, following
`test_ci_raw_creates.py`, so that a predicate wrapped across two lines
or spelled with a tuple instead of a list is still seen.
"""

import ast
import os

from shakenfist.tests import base


PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIPPED_DIRECTORIES = ('.tox', '__pycache__', '.git')

# The one function allowed to spell the predicate out: it is the
# definition everything else calls.
EXEMPT_FUNCTIONS = ('namespace_owns',)

# There were 637 .py files in the package when this was written. The
# floor is a long way below that because its job is to catch a walk
# which found nothing -- a renamed directory, a changed layout -- not to
# track the file count.
MINIMUM_SCANNED_FILES = 200

PRIVILEGED_NAMESPACE = 'system'


def _exempt_nodes(tree):
    """Every node inside a function this guard does not police."""
    exempt = set()
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in EXEMPT_FUNCTIONS):
            for child in ast.walk(node):
                exempt.add(id(child))
    return exempt


def _is_ownership_predicate(node):
    """True for a membership test against a literal naming 'system'.

    `x in [y, 'system']` and `x not in (y, 'system')` both match, as
    does a set literal, because the question is which test is being
    performed and not how it was punctuated.
    """
    if not isinstance(node, ast.Compare):
        return False
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.In, ast.NotIn)):
        return False
    comparator = node.comparators[0]
    if not isinstance(comparator, (ast.List, ast.Tuple, ast.Set)):
        return False
    return any(isinstance(element, ast.Constant)
               and element.value == PRIVILEGED_NAMESPACE
               for element in comparator.elts)


def find_restated_predicates(source):
    """Line numbers where this source restates the ownership predicate."""
    tree = ast.parse(source)
    exempt = _exempt_nodes(tree)
    return sorted(node.lineno for node in ast.walk(tree)
                  if _is_ownership_predicate(node) and id(node) not in exempt)


def _package_sources():
    for dirpath, dirnames, filenames in os.walk(PACKAGE_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIPPED_DIRECTORIES]
        for filename in sorted(filenames):
            if filename.endswith('.py'):
                yield os.path.join(dirpath, filename)


class NamespaceOwnershipPredicateTestCase(base.ShakenFistTestCase):
    def test_the_check_recognises_a_restatement(self):
        """The positive control: without it a clean scan proves nothing."""
        self.assertEqual(
            [1],
            find_restated_predicates(
                "if request_namespace() not in [a.namespace, 'system']:\n"
                "    pass\n"))

    def test_a_tuple_spelling_is_recognised(self):
        """The helper's own body uses a tuple, so a copy of it would too."""
        self.assertEqual(
            [1],
            find_restated_predicates(
                "allowed = caller in (obj.namespace, 'system')\n"))

    def test_the_positive_sense_is_recognised(self):
        """`in` as well as `not in`.

        An inverted restatement is the exact defect the rule records, so
        a guard which only caught the refusal spelling would have passed
        the bug it exists to prevent.
        """
        self.assertEqual(
            [1],
            find_restated_predicates(
                "if request_namespace() in [a.namespace, 'system']:\n"
                "    pass\n"))

    def test_a_wrapped_predicate_is_recognised(self):
        """Why this parses rather than greps: a line based check misses this."""
        self.assertEqual(
            [1],
            find_restated_predicates(
                "if request_namespace() not in [\n"
                "        a.namespace, 'system']:\n"
                "    pass\n"))

    def test_the_helper_body_is_exempt(self):
        self.assertEqual(
            [],
            find_restated_predicates(
                "def namespace_owns(obj_namespace, caller_namespace):\n"
                "    return caller_namespace in (obj_namespace, 'system')\n"))

    def test_the_exemption_is_by_function_not_by_file(self):
        """A restatement elsewhere in the helper's file is still reported."""
        self.assertEqual(
            [4],
            find_restated_predicates(
                "def namespace_owns(obj_namespace, caller_namespace):\n"
                "    return caller_namespace in (obj_namespace, 'system')\n"
                "def requires_something(func):\n"
                "    return request_namespace() in [func.namespace, 'system']\n"))

    def test_a_call_to_the_helper_is_not_a_restatement(self):
        """What the converted sites look like."""
        self.assertEqual(
            [],
            find_restated_predicates(
                "if not api_base.namespace_owns(a.namespace,\n"
                "                               request_namespace()):\n"
                "    pass\n"))

    def test_an_unrelated_membership_test_is_not_reported(self):
        """The guard keys on the privileged literal, not on `in` itself."""
        self.assertEqual(
            [],
            find_restated_predicates(
                "if state in [dbo.STATE_CREATED, dbo.STATE_DELETED]:\n"
                "    pass\n"))

    def test_the_package_restates_the_predicate_nowhere(self):
        """The guard itself, over the shipped code."""
        offences = []
        scanned = 0
        for path in _package_sources():
            with open(path) as f:
                source = f.read()
            scanned += 1
            for lineno in find_restated_predicates(source):
                offences.append(
                    '%s:%d' % (os.path.relpath(path, PACKAGE_ROOT), lineno))

        self.assertGreater(
            scanned, MINIMUM_SCANNED_FILES,
            'only %d files scanned, so this guard found almost nothing to '
            'check -- has the package layout moved?' % scanned)
        self.assertEqual(
            [], offences,
            'these sites restate the namespace ownership predicate instead '
            'of calling api_base.namespace_owns(); see '
            'docs/developer_guide/coding_rules.md: %s' % ', '.join(offences))
