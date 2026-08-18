# Copyright 2019 Michael Still and contributors
"""No test may ask whether a namespace name is in get_namespaces().

``get_namespaces()`` returns a list of ``external_view()`` dicts. A
namespace *name* is therefore never ``in`` its result, and the comparison
is not a type error -- it is a quiet False. Four places in the functional
suite made that mistake independently:

* ``base.py``'s ``_remove_namespace()``, which meant no namespaced test
  deleted its namespace between 2020 and 2026;
* ``smoke_ci_tests/test_auth.py``, where it was a vacuous assertion;
* ``cluster_ci_tests/test_auth.py``, likewise;
* ``cluster_ci_tests/test_upgrades.py``, where it made a ``skipTest()``
  guard incapable of detecting the condition it names, so a test that
  should run on an upgraded cluster would skip there too.

The functional guard for the first of those needs a deployed cluster
(``smoke_ci_tests/test_ci_harness.py``). This one needs nothing, runs in
the required check, and covers the whole family rather than the one
instance that had consequences -- which matters, because the evidence is
that this is a mistake people make repeatedly and that reviewing for it
does not catch.

The check is on the *shape of the comparison*, not on a list of known
sites, so a fifth site fails before it is merged.
"""

import ast
import os

from shakenfist.tests import base


CI_SUITE = os.path.join('shakenfist', 'deploy', 'shakenfist_ci')

# The membership operators. `is`/`==` against the list are not the same
# mistake and are not this test's business.
MEMBERSHIP_OPERATORS = (ast.In, ast.NotIn)

# The assertion forms of the same thing. testtools spells the container
# second, so the container is argument index 1.
MEMBERSHIP_ASSERTIONS = ('assertIn', 'assertNotIn')


def _is_get_namespaces_call(node):
    """True for anything.get_namespaces(), whatever the receiver."""
    return (isinstance(node, ast.Call) and
            isinstance(node.func, ast.Attribute) and
            node.func.attr == 'get_namespaces')


def _names_bound_to_get_namespaces(tree):
    """Local names assigned the result of a get_namespaces() call.

    Deliberately whole-module rather than per-function: a name that means
    "the raw listing" anywhere in a file means it everywhere, and being
    too eager here costs a rename, where being too narrow costs a
    repeat of the bug.
    """
    bound = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not _is_get_namespaces_call(node.value):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bound.add(target.id)
    return bound


def _containers_tested_for_membership(tree):
    """Every expression used as the container of a membership test."""
    containers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, MEMBERSHIP_OPERATORS):
                    containers.append((node.lineno, comparator))
        elif (isinstance(node, ast.Call) and
              isinstance(node.func, ast.Attribute) and
              node.func.attr in MEMBERSHIP_ASSERTIONS and
              len(node.args) > 1):
            containers.append((node.lineno, node.args[1]))
    return containers


def scan_source(source):
    """Line numbers where a namespace listing is used as a container."""
    tree = ast.parse(source)
    bound = _names_bound_to_get_namespaces(tree)

    offences = []
    for lineno, container in _containers_tested_for_membership(tree):
        if _is_get_namespaces_call(container):
            offences.append(lineno)
        elif isinstance(container, ast.Name) and container.id in bound:
            offences.append(lineno)
    return offences


class CINamespaceMembershipTestCase(base.ShakenFistTestCase):
    def _suite_root(self):
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), CI_SUITE)

    def test_the_check_recognises_the_mistake(self):
        """The positive control, in all three forms it was made in.

        Without this the test below would pass on an empty result, which
        is how the mistake survived six years of review in the first
        place.
        """
        offences = scan_source(
            'if name in self.system_client.get_namespaces():\n'
            '    pass\n'
            'self.assertNotIn(name, self.system_client.get_namespaces())\n'
            'namespaces = self.system_client.get_namespaces()\n'
            'self.assertIn(name, namespaces)\n')
        self.assertEqual(
            [1, 3, 5], offences,
            'The check did not recognise a namespace listing used as a '
            'membership container, so it cannot be trusted to report a '
            'real one.')

    def test_the_check_accepts_the_correct_form(self):
        """namespace_names() is the fix, and must not be reported.

        A check that flagged the correct spelling too would be deleted
        by the first person it inconvenienced.
        """
        self.assertEqual(
            [], scan_source(
                'self.assertIn(\n'
                '    name, namespace_names(client.get_namespaces()))\n'
                'namespaces = client.get_namespaces()\n'
                'self.assertIn(name, namespace_names(namespaces))\n'
                'for ns in client.get_namespaces():\n'
                '    pass\n'))

    def test_no_test_compares_a_name_against_the_listing(self):
        found = []
        root = self._suite_root()
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                if not filename.endswith('.py'):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path) as f:
                    source = f.read()
                for lineno in scan_source(source):
                    found.append(
                        '%s:%d' % (os.path.relpath(path, root), lineno))

        self.assertEqual(
            [], found,
            'A namespace name is being tested for membership of '
            'get_namespaces(), which returns dicts. The comparison is '
            'always false, so whatever it guards never happens and '
            'whatever it asserts is never checked. Wrap the listing in '
            'shakenfist_ci.base.namespace_names(). Sites: %s'
            % ', '.join(found))
