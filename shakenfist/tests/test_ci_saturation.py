# Copyright 2019 Michael Still and contributors
"""The saturation suite's sizing arithmetic, checked without a cluster.

``cluster_ci_tests/test_saturation.py`` proves the scheduler's capacity
refusals against a deployed cluster, and cluster CI only runs in the merge
queue -- so nothing about that file is checkable on a ``pull_request``.
Most of it genuinely needs the cluster. Its sizing arithmetic does not: it
is pure functions of a ``/admin/resources`` reading, and
PLAN-ci-cloud-sizing-phase-03's review observed that this is the one part
of the change whose correctness need not wait for a merge run. Those
functions therefore live in ``shakenfist_ci/sizing.py``, a module kept free
of suite and ``shakenfist_client`` imports so this file can load it by path
-- the same arrangement ``retries.py`` and
``test_ci_claims_headroom.py`` already use, and for the same reason.

Two things are covered here.

First the arithmetic, asserted as the two properties a caller depends on
rather than as particular return values: a sized request must strictly
exceed the figure it was sized from, and must stay a request a cluster
would entertain. An "impossible" request which turns out to be servable is
the flake the phase exists to remove, and a request of zero or of a
negative size is not a request at all.

Second, the client symbols. ``test_saturation.py`` names
``apiclient.InsufficientResourcesException``,
``apiclient.ResourceNotFoundException`` and ``delete_instance()``'s
``async_request`` keyword. All three are valid in the installed client
today, but ``shakenfist_client`` is not a test dependency of this
repository and the ``(collection)`` matrix is skipped on ``pull_request``,
so a typo or a rename next door would surface only in a merge-queue run --
the trap ``coding_rules.md`` documents with the federation test that died
in ``setUp`` for four commits. This cannot check the names exist in the
installed client, only that the suite still names the ones it was written
against, which is what ``test_ci_claims_headroom.py``'s ``CLAIM_VERBS``
guard does for the claims suite.
"""

import ast
import importlib.util
import os

from shakenfist.tests import base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')

SIZING_PATH = os.path.join(CI_SUITE, 'sizing.py')
SATURATION_TEST_PATH = os.path.join(
    CI_SUITE, 'cluster_ci_tests', 'test_saturation.py')


# The apiclient names the saturation suite depends on. Duplicated from
# client-python rather than imported, because shakenfist_client is not a
# test dependency of this repository -- so this catches drift within this
# repository, not skew against an installed client.
SATURATION_EXCEPTIONS = (
    'InsufficientResourcesException',
    'ResourceNotFoundException',
)


def _load_sizing():
    spec = importlib.util.spec_from_file_location(
        'shakenfist_ci_sizing_under_test', SIZING_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sizing = _load_sizing()


class OneUnitBeyondTestCase(base.ShakenFistTestCase):
    """The two properties, over every shape a reading can take.

    ``value`` arrives from ``/admin/resources``, so it can be an int, a
    fractional float (``cpu_hard_max`` is ``cpu_schedulable *
    CPU_OVERCOMMIT_RATIO``, unfloored), zero on a node publishing nothing,
    or negative on a node recorded past its own lowered ledger.
    """

    VALUES = (-1000, -2.5, -1, -0.5, 0, 0.5, 1, 2.5, 3, 3.999, 27, 1024.5,
              1000000)

    def test_the_result_strictly_exceeds_the_value_it_was_sized_from(self):
        """The whole point: the request must not be servable.

        A result equal to the ceiling is admitted by every one of the
        capacity predicates, each of which refuses only on a strict
        excess (``current + requested > limit``), so an off-by-one here
        would leave the saturation tests asserting a 507 that never
        arrives.
        """
        for value in self.VALUES:
            self.assertGreater(
                sizing.one_unit_beyond(value), value,
                'one_unit_beyond(%r) does not exceed %r, so a request '
                'sized from it would be admitted rather than refused.'
                % (value, value))

    def test_the_result_is_always_a_request_a_cluster_would_entertain(self):
        """Saturation on the dimension being sized must not size a zero ask.

        Every negative reading floors to at most zero, and a create of
        zero vCPUs, zero MB or zero GB is refused (or accepted) for
        reasons which have nothing to do with the stage under test.
        """
        for value in self.VALUES:
            result = sizing.one_unit_beyond(value)
            self.assertGreaterEqual(
                result, 1,
                'one_unit_beyond(%r) returned %r, which is not a size any '
                'create could ask for.' % (value, result))
            self.assertIsInstance(
                result, int,
                'one_unit_beyond(%r) returned %r, which is not an integer '
                'count of vCPUs, MB or GB.' % (value, result))

    def test_an_explicit_minimum_is_honoured_and_still_exceeded(self):
        # The minimum is a floor, not an override: where value is already
        # at or above it, the result is still one unit beyond value.
        self.assertEqual(64, sizing.one_unit_beyond(-1, minimum=64))
        self.assertEqual(64, sizing.one_unit_beyond(63, minimum=64))
        self.assertEqual(65, sizing.one_unit_beyond(64, minimum=64))
        self.assertEqual(101, sizing.one_unit_beyond(100, minimum=64))

    def test_a_fractional_ceiling_is_exceeded_not_rounded_down_to(self):
        # cpu_hard_max is cpu_schedulable * CPU_OVERCOMMIT_RATIO, so a
        # fractional product is the ordinary case rather than an oddity:
        # 2 schedulable threads at a ratio of 3.5 publishes 7.0, and 1 at
        # 2.5 publishes 2.5. Rounding such a figure down (a bare int()
        # with no increment) would size a request the node can serve.
        self.assertEqual(3, sizing.one_unit_beyond(2.5))
        self.assertEqual(8, sizing.one_unit_beyond(7.0))
        self.assertEqual(4, sizing.one_unit_beyond(3.999))


class EffectiveCPUCeilingTestCase(base.ShakenFistTestCase):
    """``cpu_limit``, falling back the way ``_has_sufficient_cpu()`` does."""

    def test_a_published_cpu_limit_is_the_ceiling(self):
        self.assertEqual(3, sizing.effective_cpu_ceiling(
            {'cpu_limit': 3, 'cpu_hard_max': 3.0}))

    def test_a_cpu_limit_below_the_hard_max_still_wins(self):
        # The row's limit is the figure the predicate tests against, even
        # where the node's live hard maximum has since risen above it --
        # which is the ordinary state of a node whose cpu_schedulable grew
        # before the reconciler caught up.
        self.assertEqual(3, sizing.effective_cpu_ceiling(
            {'cpu_limit': 3, 'cpu_hard_max': 6.0}))

    def test_a_null_cpu_limit_falls_back_to_the_live_hard_max(self):
        """An unguarded node (P7) is not a limitless node.

        ``summarize_resources()`` publishes a null ``cpu_limit`` for a
        node the capacity reconciler has written no row for, and
        ``_has_sufficient_cpu()`` then measures it against its own
        ``hard_max_cpus``. Reading the null as zero, or dropping the node
        from the cluster-wide ``max()``, would understate the cluster's
        real ceiling -- and if that node holds the largest ceiling, a
        request sized from the guarded nodes alone is admitted there and
        the saturation test asserts a refusal which never comes.
        """
        self.assertEqual(6.0, sizing.effective_cpu_ceiling(
            {'cpu_limit': None, 'cpu_hard_max': 6.0}))

    def test_a_zero_cpu_limit_is_a_ceiling_and_not_a_missing_one(self):
        # Distinct from None: a row which really does permit nothing.
        self.assertEqual(0, sizing.effective_cpu_ceiling(
            {'cpu_limit': 0, 'cpu_hard_max': 6.0}))

    def test_an_entry_publishing_neither_figure_reads_as_zero(self):
        # A 1 vCPU request then exceeds it, which is the right answer for
        # a node publishing no ceiling at all: there is nothing to size
        # beyond, and the smallest possible ask is what the rest of the
        # cluster's maximum will be compared against.
        self.assertEqual(0, sizing.effective_cpu_ceiling({}))
        self.assertEqual(1, sizing.one_unit_beyond(
            sizing.effective_cpu_ceiling({})))


class EffectiveRAMCeilingTestCase(base.ShakenFistTestCase):
    """``max(ram_max, ram_available + ram_committed)``, and why it is a max.

    ``ram_max`` is the published ceiling and is what normally answers.
    The reconstruction is defensive: where a capacity row's
    ``limit_memory_mb`` is the bound winning ``ram_available``'s
    ``min()``, ``ram_available + ram_committed`` recovers it exactly, so a
    row whose limit has drifted *above* the node's measured ``ram_max``
    cannot silently cap what the test believes the cluster's true ceiling
    to be.
    """

    def test_ram_max_is_the_ceiling_when_it_is_the_larger(self):
        self.assertEqual(12288, sizing.effective_ram_ceiling({
            'ram_max': 12288, 'ram_available': 4096, 'ram_committed': 2048}))

    def test_a_ledger_limit_above_ram_max_is_not_capped_by_it(self):
        # limit_memory_mb reconstructs as 9000 + 3000 = 12000, above the
        # measured ram_max of 8192. Sizing from ram_max alone would build
        # a request this node's ledger would admit.
        self.assertEqual(12000, sizing.effective_ram_ceiling({
            'ram_max': 8192, 'ram_available': 9000, 'ram_committed': 3000}))

    def test_headroom_alone_never_lowers_the_ceiling(self):
        """The correction this function exists for.

        D24 originally sized this test from ``ram_available``, which is
        headroom: a sibling stestr worker deleting an instance *raises*
        it between the read and the create, and an impossible request
        sized from it becomes possible. A node with almost no headroom
        left must still report its full ceiling here.
        """
        self.assertEqual(12288, sizing.effective_ram_ceiling({
            'ram_max': 12288, 'ram_available': 0, 'ram_committed': 12288}))

    def test_a_missing_ram_max_does_not_read_as_a_zero_ceiling(self):
        # The ledger reconstruction still answers, so the node is not
        # dropped out of the cluster-wide max() by a metrics gap.
        self.assertEqual(6000, sizing.effective_ram_ceiling({
            'ram_available': 2000, 'ram_committed': 4000}))
        self.assertEqual(6000, sizing.effective_ram_ceiling({
            'ram_max': None, 'ram_available': 2000, 'ram_committed': 4000}))

    def test_an_entry_publishing_nothing_reads_as_zero(self):
        self.assertEqual(0, sizing.effective_ram_ceiling({}))
        self.assertEqual(1, sizing.one_unit_beyond(
            sizing.effective_ram_ceiling({})))


class SaturationSuiteSourceTestCase(base.ShakenFistTestCase):
    """Shared AST access to the functional saturation suite.

    Source is parsed rather than imported: the functional suite needs
    shakenfist_client, which is not a test dependency here.
    """

    def _saturation_tree(self):
        with open(SATURATION_TEST_PATH) as f:
            return ast.parse(f.read())

    def _attributes_of(self, tree, name):
        return {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and
            isinstance(node.value, ast.Name) and node.value.id == name}

    def _calls_of(self, tree, method_name):
        return [
            call for call in ast.walk(tree)
            if isinstance(call, ast.Call) and
            isinstance(call.func, ast.Attribute) and
            call.func.attr == method_name]


class SaturationSuiteDispatchTestCase(SaturationSuiteSourceTestCase):
    def test_the_suite_names_only_the_pinned_apiclient_symbols(self):
        self.assertEqual(
            set(SATURATION_EXCEPTIONS),
            self._attributes_of(self._saturation_tree(), 'apiclient'),
            'The saturation suite reaches for a different set of '
            'apiclient symbols than the ones pinned here. A name which '
            'is not a real client attribute raises AttributeError from '
            'an except clause, and only in a merge-queue run.')

    def test_every_delete_asks_for_the_asynchronous_path(self):
        """The fill is released without waiting on each instance in turn.

        A ``delete_instance()`` which lost its ``async_request`` keyword
        would still delete, so nothing would fail -- the release would
        simply wait up to a minute per fill instance, stacked in front of
        the ledger poll, and the test would start timing out rather than
        reporting anything about the ledger.
        """
        deletes = self._calls_of(self._saturation_tree(), 'delete_instance')
        self.assertNotEqual(
            [], deletes,
            'The saturation suite no longer deletes any instance, so '
            'either it has stopped releasing its fill or this guard is '
            'looking at the wrong code.')
        for call in deletes:
            self.assertIn(
                'async_request', [kw.arg for kw in call.keywords],
                'A delete_instance() call in the saturation suite does '
                'not pass async_request, so releasing the fill waits on '
                'each instance in turn.')


class SaturationSuiteSizingWiringTestCase(SaturationSuiteSourceTestCase):
    def test_the_suite_sizes_through_the_module_this_file_tests(self):
        """Guards the split, not just the arithmetic.

        These helpers were moved out of the functional suite so they
        could be tested at all. A later edit which inlined the
        arithmetic back into the test module would leave this file
        passing while testing nothing anybody runs.
        """
        self.assertEqual(
            {'one_unit_beyond', 'effective_cpu_ceiling',
             'effective_ram_ceiling'},
            self._attributes_of(self._saturation_tree(), 'sizing'),
            'The saturation suite no longer sizes its requests through '
            'exactly the three shakenfist_ci.sizing helpers this file '
            'covers, so either some sizing arithmetic has moved back '
            'into the functional suite where nothing can test it, or a '
            'new helper needs covering here.')
