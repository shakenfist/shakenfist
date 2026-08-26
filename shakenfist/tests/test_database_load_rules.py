# Copyright 2026 Michael Still and contributors

"""Tests for examples/prometheus-database-load-rules.yaml.

The rules file is a rendering of shakenfist/data/database_load_budget.yaml.
That is the whole point of generating it: an operator's alerts and our CI
check and sf-ctl must not be able to hold different opinions about what
normal load is. So the assertion which matters here is that the committed
file is byte for byte what the generator produces from the committed
budget, which fails if either is edited alone.

The PromQL itself is checked with promtool, which needs a Prometheus
binary this suite cannot assume. What is asserted here instead is the
structure a reader of these rules depends on -- that every budgeted pair
appears in every coefficient series, that provisional and activity coupled
pairs are excluded from the alerting flag, and that the tolerances match
the budget's own defaults.
"""

import importlib.util
import io
import re
import os

import yaml

from shakenfist.schema import database_load_budget
from shakenfist.tests import base


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _load_generator():
    path = os.path.join(_repo_root(), 'tools',
                        'generate-database-load-rules.py')
    spec = importlib.util.spec_from_file_location('generate_rules', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()

RULES_PATH = os.path.join(_repo_root(), 'examples',
                          'prometheus-database-load-rules.yaml')


def _generate():
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        generator.main()
    return buf.getvalue()


def _committed():
    with open(RULES_PATH, encoding='utf-8') as f:
        return f.read()


def _rules():
    parsed = yaml.safe_load(_committed())
    return parsed['groups'][0]['rules']


# The generated form of one term, from which both labels are read. Reading
# only the operation would let a pair vanish from a coefficient series
# while another caller of the same operation kept the name present, which
# is exactly the failure the test below is for.
PAIR_RE = re.compile(
    r'"operation", "([^"]+)", "", ""\), "caller_daemon", "([^"]+)"')


def _pairs_in(expr):
    return set(PAIR_RE.findall(expr))


class DatabaseLoadRulesTestCase(base.ShakenFistTestCase):
    def test_committed_rules_match_the_generator(self):
        self.assertEqual(
            _generate(), _committed(),
            'examples/prometheus-database-load-rules.yaml is not what '
            'tools/generate-database-load-rules.py produces from '
            'shakenfist/data/database_load_budget.yaml. Regenerate it: '
            'tools/generate-database-load-rules.py > '
            'examples/prometheus-database-load-rules.yaml')

    def test_the_rules_are_valid_yaml_in_prometheus_shape(self):
        parsed = yaml.safe_load(_committed())
        self.assertEqual(1, len(parsed['groups']))
        self.assertEqual('shakenfist-database-load', parsed['groups'][0]['name'])
        for rule in _rules():
            self.assertTrue('record' in rule or 'alert' in rule)
            self.assertIn('expr', rule)

    def test_every_budgeted_pair_is_in_every_coefficient_series(self):
        # PromQL arithmetic intersects label sets, so a pair which appears
        # in two of the three coefficient rules and not the third vanishes
        # from the modelled rate entirely -- and a pair with no model is a
        # pair no alert can fire for.
        #
        # Asserted per pair rather than per operation, because most
        # operations have several callers: GetInstanceAttributes appears
        # with seven, so dropping its /net term would leave the operation
        # present via /api and an operation-level assertion would not
        # notice.
        budget = database_load_budget.load_budget()
        expected = {e.key for e in budget.entries}
        by_name = {r['record']: r['expr'] for r in _rules() if 'record' in r}
        for name in ('sf_database:budget:per_node_base',
                     'sf_database:budget:cluster_base',
                     'sf_database:budget:per_instance'):
            self.assertEqual(expected, _pairs_in(by_name[name]), name)

    def test_the_pair_reader_reads_both_labels(self):
        # _pairs_in() drives the assertion above, and a regex which
        # matched nothing, or matched only the operation, would make it
        # pass on a series which had lost a caller.
        pairs = _pairs_in(
            'label_replace(label_replace(vector(0.026), "operation", '
            '"AcquireLock", "", ""), "caller_daemon", "cleaner", "", "")')
        self.assertEqual({('AcquireLock', 'cleaner')}, pairs)

    def test_provisional_pairs_are_not_alerted_on(self):
        # GetReferencesFrom/api records a known defect (#3876). Alerting on
        # it would tell every operator about a bug we already know about,
        # every day, until it is fixed.
        budget = database_load_budget.load_budget()
        by_name = {r['record']: r['expr'] for r in _rules() if 'record' in r}
        enforced = by_name['sf_database:budget:enforced']
        provisional = [e for e in budget.entries if e.provisional]
        self.assertNotEqual(0, len(provisional))
        for entry in provisional:
            self.assertNotIn(
                '"operation", "%s", "", ""), "caller_daemon", "%s"'
                % entry.key, enforced)

    def test_enforced_series_matches_the_budget(self):
        budget = database_load_budget.load_budget()
        by_name = {r['record']: r['expr'] for r in _rules() if 'record' in r}
        enforced = by_name['sf_database:budget:enforced']
        for entry in budget.entries:
            marker = ('"operation", "%s", "", ""), "caller_daemon", "%s"'
                      % entry.key)
            if entry.enforced:
                self.assertIn(marker, enforced, '%s/%s missing' % entry.key)
            else:
                self.assertNotIn(marker, enforced,
                                 '%s/%s should not alert' % entry.key)

    def test_tolerances_come_from_the_budget(self):
        defaults = database_load_budget.load_budget().defaults
        by_name = {r['record']: r['expr'] for r in _rules() if 'record' in r}
        self.assertIn('* %s + %s' % (defaults.tolerance_multiplier,
                                     defaults.tolerance_floor_qps),
                      by_name['sf_database:budget_ceiling'])
        alerts = {r['alert']: r['expr'] for r in _rules() if 'alert' in r}
        self.assertIn('> %s' % defaults.unbudgeted_fixed_rate_qps,
                      alerts['ShakenFistUnbudgetedDatabasePolling'])

    def test_there_is_an_alert_for_the_model_going_blind(self):
        # Every other alert in the file depends on instances_active. If it
        # stops being scraped the modelled series are empty, nothing can
        # fire, and a silent ruleset is indistinguishable from a healthy
        # cluster. This is the positive control for the production side,
        # the same idea as the CI check's poll rate assertion.
        alerts = {r['alert']: r['expr'] for r in _rules() if 'alert' in r}
        self.assertIn('ShakenFistDatabaseLoadModelBlind', alerts)
        self.assertIn('absent(sf_database:cluster_nodes)',
                      alerts['ShakenFistDatabaseLoadModelBlind'])

    def test_the_file_says_it_is_generated(self):
        self.assertIn('GENERATED FILE', _committed())
