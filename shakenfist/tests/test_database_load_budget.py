# Copyright 2019 Michael Still and contributors
import io
from importlib import resources

import yaml
from pydantic import ValidationError

from shakenfist.schema import database_load_budget as budget
from shakenfist.tests import base


class DatabaseLoadBudgetTestCase(base.ShakenFistTestCase):
    """The shipped budget file must parse, and must ship.

    The packaging assertion is the one worth having. Nothing in
    pyproject.toml names this file: it reaches the wheel because
    setuptools_scm finds every git tracked file. That is fine until
    somebody tidies package-data, at which point `sf-ctl database-load`
    keeps working in a checkout and in CI and stops working on a node.
    Reading it the way a deployed process does is what catches that.
    """

    def test_budget_is_readable_as_package_data(self):
        text = (resources.files(budget.BUDGET_PACKAGE)
                .joinpath(budget.BUDGET_RESOURCE).read_text(encoding='utf-8'))
        self.assertIn('entries:', text)
        self.assertEqual(text, budget.budget_text())

    def test_budget_parses_and_validates(self):
        b = budget.load_budget()
        self.assertEqual(1, b.version)
        self.assertNotEqual(0, len(b.entries))

    def test_every_entry_predicts_something(self):
        # The model validator enforces this, but the file is generated and
        # then hand edited, so assert it against the shipped data too.
        for entry in budget.load_budget().entries:
            self.assertTrue(
                entry.per_node_base_qps is not None
                or entry.cluster_base_qps is not None
                or entry.per_instance_qps is not None,
                '%s/%s predicts nothing' % entry.key)

    def test_entry_without_terms_is_rejected(self):
        self.assertRaises(
            ValidationError, budget.BudgetEntry,
            operation='Nothing', caller_daemon='api', note='no terms')

    def test_entry_keys_are_unique(self):
        b = budget.load_budget()
        keys = [e.key for e in b.entries]
        self.assertEqual(len(keys), len(set(keys)))

    def test_duplicate_entries_are_rejected(self):
        entry = {'operation': 'GetNode', 'caller_daemon': 'net',
                 'per_node_base_qps': 0.2, 'note': 'a note'}
        self.assertRaises(
            ValidationError, budget.DatabaseLoadBudget.model_validate,
            {'version': 1,
             'defaults': {'tolerance_multiplier': 2.0,
                          'tolerance_floor_qps': 0.5,
                          'unbudgeted_fixed_rate_qps': 0.25},
             'entries': [entry, dict(entry)]})

    def test_provisional_entries_name_an_issue_and_are_not_enforced(self):
        provisional = [e for e in budget.load_budget().entries
                       if e.provisional is not None]
        # If this list is ever empty the assertions below pass vacuously,
        # which would quietly retire the mechanism.
        self.assertNotEqual(0, len(provisional))
        for entry in provisional:
            self.assertLess(0, entry.provisional.issue)
            self.assertNotEqual('', entry.provisional.reason.strip())
            self.assertFalse(entry.enforced)

    def test_activity_coupled_entries_are_not_enforced(self):
        for entry in budget.load_budget().entries:
            if entry.activity_coupled:
                self.assertFalse(entry.enforced)

    def test_every_entry_names_the_loop_that_produces_it(self):
        # A budget entry whose note does not say what makes the traffic is
        # a number nobody can act on when it goes red.
        for entry in budget.load_budget().entries:
            self.assertLess(20, len(entry.note.strip()),
                            '%s/%s has no useful note' % entry.key)

    def test_expected_qps_composes_the_three_terms(self):
        entry = budget.BudgetEntry(
            operation='X', caller_daemon='net', per_node_base_qps=0.5,
            cluster_base_qps=1.0, per_instance_qps=0.25, note='a note')
        self.assertEqual(0.5 * 6 + 1.0 + 0.25 * 8, entry.expected_qps(6, 8))

    def test_expected_qps_never_goes_negative(self):
        # GetNodeDaemonState/cluster carries a negative cluster term.
        entry = budget.BudgetEntry(
            operation='X', caller_daemon='cluster', per_node_base_qps=0.5,
            cluster_base_qps=-0.3, note='a note')
        self.assertEqual(0.0, entry.expected_qps(0, 0))
        self.assertAlmostEqual(2.7, entry.expected_qps(6, 0))

    def test_elected_cluster_daemon_poll_matches_the_daemon_code(self):
        # The one entry which encodes an arithmetic claim about the code
        # rather than a measurement: every daemon polls its own state row
        # at 1/DAEMON_STATE_POLL_INTERVAL, except the single elected
        # cluster daemon, which polls from a 5s loop instead. If either
        # constant moves, this budget entry is wrong.
        from shakenfist.daemons import daemon

        entry = budget.load_budget().get('GetNodeDaemonState', 'cluster')
        per_node = 1.0 / daemon.DAEMON_STATE_POLL_INTERVAL
        self.assertEqual(per_node, entry.per_node_base_qps)
        self.assertAlmostEqual(-(per_node - 1.0 / 5.0),
                               entry.cluster_base_qps)

    def test_ceiling_is_above_expected(self):
        b = budget.load_budget()
        d = b.defaults
        for entry in b.entries:
            expected = entry.expected_qps(6, 8)
            ceiling = entry.ceiling_qps(
                6, 8, d.tolerance_multiplier, d.tolerance_floor_qps)
            self.assertLess(expected, ceiling)

    def test_doc_block_records_its_provenance(self):
        raw = yaml.safe_load(io.StringIO(budget.budget_text()))
        doc = raw['_doc']
        for key in ('derived_from', 'window', 'samples', 'cluster_shape',
                    'method', 'base_term_caveat', 'coverage_caveat',
                    'rederive'):
            self.assertIn(key, doc)
        # The coverage caveat is the expensive lesson from phase 6 and the
        # reason a pre 2026-08-11 number cannot be reused.
        self.assertIn('3708', doc['coverage_caveat'])
