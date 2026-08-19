# Copyright 2019 Michael Still and contributors
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
from shakenfist_client import apiclient


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
            'system', base.namespace_names(namespaces),
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
            name, base.namespace_names(self.system_client.get_namespaces()),
            'The namespace was not created, so this test cannot say '
            'anything about deleting one.')

        self._remove_namespace(name)
        self.assertNotIn(
            name, base.namespace_names(self.system_client.get_namespaces()),
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

    def _refusing_client(self, name, *errors):
        """A stand-in for system_client which fails the delete to order.

        The retry loop cannot be reached from a real cluster on demand:
        producing the refusal it retries means holding a namespace with a
        live instance and racing the delete against it, and producing the
        refusals it must *not* retry means a malformed request the client
        will not send. So this substitutes the client rather than the
        cluster, which is the only part of the behaviour under test.

        Installs itself as ``self.system_client`` and returns it, so a
        caller which needs the delete count can have it.
        """
        class RefusingClient:
            def __init__(self):
                self.remaining = list(errors)
                self.deletes = 0

            def get_namespaces(self):
                return [{'name': name}]

            def delete_namespace(self, namespace):
                self.deletes += 1
                if self.remaining:
                    raise self.remaining.pop(0)

        client = RefusingClient()
        self.system_client = client
        return client

    def _refusal(self, message, status=400):
        return apiclient.STATUS_CODES_TO_ERRORS[status](
            'API request failed', 'DELETE', '/auth/namespaces/x', status,
            '{"error": "%s", "status": %d}' % (message, status))

    def test_remove_namespace_retries_a_namespace_which_is_not_yet_empty(self):
        """The refusal which clears is waited out rather than raised.

        tearDown() deletes instances and networks through a non-blocking
        client, so the namespace can be seconds short of deletable when
        the delete is attempted.
        """
        client = self._refusing_client(
            'ci-harness-retry',
            self._refusal('you cannot delete a namespace with instances'))

        self._remove_namespace('ci-harness-retry')
        self.assertEqual(
            2, client.deletes,
            'The delete was not retried past a refusal which clears, so a '
            'namespaced test will fail its teardown on a timing artefact.')

    def test_remove_namespace_does_not_retry_an_unrelated_refusal(self):
        """A 400 which will never clear is raised at once.

        This runs at the end of a teardown which has already waited up to
        ten minutes, so retrying an error which cannot resolve buys
        nothing and hides it for two more minutes.
        """
        client = self._refusing_client(
            'ci-harness-permanent', self._refusal('key is not a string'))

        self.assertRaises(
            apiclient.RequestMalformedException,
            self._remove_namespace, 'ci-harness-permanent')
        self.assertEqual(
            1, client.deletes,
            'A 400 which has nothing to do with a non-empty namespace was '
            'retried, which only delays the report of it.')

    def test_remove_namespace_gives_up_at_the_deadline(self):
        """A refusal which does not clear still fails the test.

        Retrying forever would recreate the problem this file is about:
        a helper which never reports that it did not do its job.
        """
        self._refusing_client(
            'ci-harness-deadline',
            self._refusal('you cannot delete a namespace with instances'))

        self.assertRaises(
            apiclient.RequestMalformedException,
            self._remove_namespace, 'ci-harness-deadline', 0)

    def test_remove_namespace_accepts_a_delete_race(self):
        """Deleted between the listing and the delete is success.

        Two tests tearing down concurrently, or a cleanup running twice,
        should not turn into a failure.
        """
        self._refusing_client(
            'ci-harness-race',
            self._refusal('namespace not found', status=404))

        self._remove_namespace('ci-harness-race')

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

        # The inner test deletes its own namespace at teardown, which is
        # the thing being asserted below -- but only if it gets as far as
        # teardown. setUp() creates the namespace before it adds the key,
        # and unittest does not run tearDown for a setUp which raises, so
        # a failure between those two calls would leave this test leaking
        # exactly the namespace it exists to detect. The helper
        # early-returns when the name is not listed, so in the passing
        # case this costs one listing call.
        self.addCleanup(self._remove_namespace, namespace)

        result = testtools.TestResult()
        case.run(result)

        self.assertTrue(
            result.wasSuccessful(),
            'An empty namespaced test did not pass, so its teardown '
            'cannot be judged. Errors: %s. Failures: %s.'
            % (result.errors, result.failures))
        self.assertNotIn(
            namespace, base.namespace_names(self.system_client.get_namespaces()),
            'A namespaced test ran to completion and left its namespace '
            '%s behind. Every functional run leaks one when this fails, '
            'and on a long-lived cluster they never go away.' % namespace)
