"""Guards on the functional harness itself.

Everything else in these suites tests the cluster. This file tests
``shakenfist_ci.base``, because one of its helpers was broken for six
years without any test noticing: ``_remove_namespace()`` asked whether a
namespace *name* was in the result of ``get_namespaces()``, which is a
list of ``external_view()`` dicts. A string is never in that list, so
from 2020 until 2026 no namespaced functional test deleted the namespace
it created, and every functional run on a long-lived cluster left one
behind permanently.

Nothing in the unit suite can see that. The helper only misbehaves
against a real API, and it misbehaves by succeeding -- which is why four
rounds of review, a rewrite of the teardown path and a phase spent
hunting vacuous tests all walked past it. So the guard belongs here,
where there is an API to be wrong about.
"""

import testtools

from shakenfist_ci import base
from shakenfist_ci.base import namespace_names


class TestNamespaceHelpers(base.BaseTestCase):
    def test_namespace_names_reads_the_api_shape(self):
        """The helper against the real API, not against a fixture.

        A hand-written fixture would only prove the helper agrees with
        whoever wrote the fixture. This asks the cluster.
        """
        namespaces = self.system_client.get_namespaces()
        self.assertNotEqual(
            [], namespaces,
            'The cluster reported no namespaces at all, which cannot be '
            'right -- system always exists. Nothing below this can mean '
            'anything.')
        self.assertIn(
            'system', namespace_names(namespaces),
            'get_namespaces() no longer carries a name for each namespace, '
            'so namespace_names() -- and everything in the harness which '
            'asks whether a namespace exists -- needs revisiting.')

    def test_remove_namespace_removes_the_namespace(self):
        """The positive control for the fix.

        Written the way it is because the bug was that this helper
        returned successfully having done nothing at all.
        """
        name = 'ci-harness-%s' % self._uniquifier()
        self._make_namespace(name, self._uniquifier())
        self.addCleanup(self._remove_namespace, name)

        self.assertIn(
            name, namespace_names(self.system_client.get_namespaces()),
            'The namespace was not created, so this test cannot say '
            'anything about deleting one.')

        self._remove_namespace(name)
        self.assertNotIn(
            name, namespace_names(self.system_client.get_namespaces()),
            '_remove_namespace() returned without deleting the namespace. '
            'That is exactly how it behaved from 2020 until 2026, by '
            'comparing a name against a list of dicts, so check that '
            'comparison first.')

    def test_remove_namespace_tolerates_an_absent_namespace(self):
        """Callers use it as cleanup, so it must not care if it is late.

        A test which fails before creating its namespace still runs its
        cleanups, and both test_federation cases register this helper
        with addCleanup() directly.
        """
        self._remove_namespace('ci-harness-absent-%s' % self._uniquifier())

    def test_a_namespaced_test_deletes_its_namespace(self):
        """End to end, which is the claim that actually matters.

        The helper being correct is not the same as the teardown path
        using it correctly, and the teardown path is where the six years
        of leaked namespaces came from.
        """
        # Defined here rather than at module level because stestr
        # discovers TestCase subclasses by walking the module, and a
        # leading underscore does not exempt one -- collected, it ran as
        # a test in its own right and reported under a name nobody
        # writing this file intended. Inside the method it is invisible
        # to discovery and still perfectly runnable.
        class EmptyNamespacedTest(base.BaseNamespacedTestCase):
            def __init__(self, *args, **kwargs):
                kwargs['namespace_prefix'] = 'harness'
                super().__init__(*args, **kwargs)

            def runTest(self):
                pass

        case = EmptyNamespacedTest()
        namespace = case.namespace

        result = testtools.TestResult()
        case.run(result)

        self.assertTrue(
            result.wasSuccessful(),
            'An empty namespaced test did not pass, so its teardown '
            'cannot be judged. Errors: %s. Failures: %s.'
            % (result.errors, result.failures))
        self.assertNotIn(
            namespace, namespace_names(self.system_client.get_namespaces()),
            'A namespaced test ran to completion and left its namespace '
            '%s behind. Every functional run leaks one when this fails, '
            'and on a long-lived cluster they never go away.' % namespace)
