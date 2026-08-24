#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Generate examples/prometheus-database-load-rules.yaml from the budget.

The rules and the budget must not be able to disagree, so the rules are not
written by hand: they are a rendering of
shakenfist/data/database_load_budget.yaml, and
shakenfist/tests/test_database_load_rules.py asserts the committed file is
what this produces from the committed budget. Editing either alone fails
that test.

    tools/generate-database-load-rules.py > \\
        examples/prometheus-database-load-rules.yaml

The shape of the output is worth understanding before changing it. The
budget is a model rather than a set of thresholds -- expected load is a
per-node base, a constant for work done once cluster wide, and a
coefficient per standing instance -- so the rules have to evaluate that
model against the cluster they are running on. They do it by materialising
the three coefficients as labelled series, one sample per (operation,
caller_daemon) pair, and combining them with the cluster's own shape as
reported by instances_active. Every pair appears in every coefficient
series, including with a zero, because PromQL arithmetic on two series
intersects their label sets and a pair missing from one term would
otherwise vanish from the result entirely.
"""

import sys

from shakenfist.schema import database_load_budget


HEADER = '''# Prometheus rules for Shaken Fist database load.
#
# GENERATED FILE. Do not edit: run
# tools/generate-database-load-rules.py and commit the result. The numbers
# come from shakenfist/data/database_load_budget.yaml, and a test asserts
# this file is exactly what that budget renders to.
#
# What these are for
# ------------------
#
# Shaken Fist's database load is mostly polling, and polling rates are set
# by how many things exist rather than by how much work anybody is doing.
# So "expect under N queries per second" is not a useful expectation for
# anybody else's cluster. These rules evaluate a model instead: a per-node
# base, a constant for the work the elected cluster daemon does once for
# the whole cluster, and a coefficient per standing instance. The useful
# question they answer is not "is my database busy" but "is my database
# busier than a cluster of my shape should be" -- which is the difference
# between load that grew because you grew, and load that grew because
# something broke.
#
# Installing this
# ---------------
#
#   1. Scrape sf-database. Every sf-database instance serves Prometheus
#      metrics on MARIADB_GATEWAY_METRICS_PORT (13006 by default) on its
#      mesh IP. All of the rules below sum across the tier, so scrape
#      every instance rather than picking one.
#
#   2. Scrape sf-resources too, on RESOURCES_METRICS_PORT (13007). The
#      model needs to know the shape of your cluster and that is where
#      instances_active comes from. Without it every modelled value is
#      empty and the alerts below can never fire -- which looks exactly
#      like everything being fine.
#
#   3. Copy this file into your Prometheus rule directory, the one named
#      by rule_files in prometheus.yml, and reload.
#
#   4. Confirm it evaluates: query sf_database:budget_ceiling in the
#      expression browser. It should return one series per budgeted pair.
#      If it returns nothing, step 2 is usually why.
#
# Confirming an alert actually fires, which is worth doing once: copy this
# file, change the "* {multiplier}" in the ceiling rule to "* 0", and
# reload. Every budgeted pair is then over its ceiling and
# ShakenFistDatabasePairOverBudget fires for all of them within the hour.
# Put the original back afterwards. A rule which is loaded but never
# evaluated reads the same as a healthy cluster, and the only way to tell
# them apart is to have seen it fire once.
#
# What to do when one fires
# -------------------------
#
# Run "sf-ctl database-load --json" and attach its output to an issue at
# https://github.com/shakenfist/shakenfist/issues. It reports the same
# comparison this does, per caller, without needing Prometheus. See
# docs/operator_guide/database.md.

groups:
  - name: shakenfist-database-load
    interval: 5m
    rules:
'''


INDENT = ' ' * 10


def label_term(operation, caller, value):
    """One (operation, caller_daemon) sample carrying a constant.

    vector() produces an unlabelled sample, so the labels which make it
    join against the measured series have to be pasted on. This is the
    only way to get a lookup table into PromQL, and it is why these rules
    are generated rather than written.
    """
    return (INDENT + 'label_replace(label_replace(vector(%s), "operation", '
            '"%s", "", ""), "caller_daemon", "%s", "", "")'
            % (value, operation, caller))


def union_rule(name, comment, terms):
    return '\n'.join(
        ['      # %s' % comment,
         '      - record: %s' % name,
         '        expr: |-',
         ('\n' + INDENT + 'or\n').join(terms)]) + '\n'


def coefficient_rule(name, entries, attribute, comment):
    terms = []
    for entry in entries:
        value = getattr(entry, attribute)
        terms.append(label_term(entry.operation, entry.caller_daemon,
                                0.0 if value is None else value))
    return union_rule(name, comment, terms)


def flag_rule(name, entries, comment):
    return union_rule(name, comment,
                      [label_term(e.operation, e.caller_daemon, 1)
                       for e in entries])


def main():
    budget = database_load_budget.load_budget()
    entries = sorted(budget.entries, key=lambda e: e.key)
    enforced = [e for e in entries if e.enforced]
    defaults = budget.defaults

    out = [HEADER]

    out.append('''      # The shape of this cluster. sf-resources publishes
      # instances_active from every node whatever its roles, so counting
      # the series counts nodes and summing them counts standing
      # instances.
      - record: sf_database:cluster_nodes
        expr: count(instances_active)

      - record: sf_database:standing_instances
        expr: sum(instances_active)

      # Measured load. The one hour rate is for graphing and the one day
      # rate is what the alerts below use: this detects a regression
      # rather than an incident, and a day of smoothing keeps a busy
      # afternoon from reading as one.
      - record: sf_database:request_rate
        expr: |-
          sum by (operation, caller_daemon) (rate(database_requests_total[1h]))

      - record: sf_database:request_rate:1d
        expr: |-
          sum by (operation, caller_daemon) (rate(database_requests_total[1d]))

      - record: sf_database:request_rate:total
        expr: sum(sf_database:request_rate)

''')

    out.append(coefficient_rule(
        'sf_database:budget:per_node_base', entries, 'per_node_base_qps',
        'Budget coefficients, rendered from database_load_budget.yaml. '
        'Every\n      # pair appears in all three, zero included, because '
        'PromQL arithmetic\n      # intersects label sets and a pair '
        'missing a term would drop out.'))
    out.append('\n')
    out.append(coefficient_rule(
        'sf_database:budget:cluster_base', entries, 'cluster_base_qps',
        'Work done once for the whole cluster rather than once per node. '
        'This\n      # one can be negative: the elected cluster daemon '
        'polls its daemon\n      # state row from a five second loop '
        'rather than the two second interval\n      # every other daemon '
        'idles at, so its pair is 0.5 per node less 0.3.'))
    out.append('\n')
    out.append(coefficient_rule(
        'sf_database:budget:per_instance', entries, 'per_instance_qps',
        'Load which scales with how many instances are standing.'))
    out.append('\n')
    out.append(flag_rule(
        'sf_database:budget:enforced', enforced,
        'The pairs worth alerting on. A pair is left out when its budget '
        'is\n      # provisional -- it records a known defect rather than '
        'a floor worth\n      # defending -- or when it is activity '
        'coupled, meaning its level is\n      # set by what your users and '
        'tooling do rather than by a loop of ours.'))

    out.append('''
      # The model, evaluated for this cluster. Clamped at zero because of
      # the negative cluster term described above.
      - record: sf_database:modelled_rate
        expr: |-
          clamp_min(
            sf_database:budget:per_node_base
              * on() group_left() sf_database:cluster_nodes
            + sf_database:budget:cluster_base
            + sf_database:budget:per_instance
              * on() group_left() sf_database:standing_instances,
            0)

      # The ceiling is deliberately generous. These rules exist to catch a
      # new polling loop or one which lost its bulk read, not to police a
      # ten percent drift.
      - record: sf_database:budget_ceiling
        expr: |-
          sf_database:modelled_rate * {multiplier} + {floor}

      - alert: ShakenFistDatabasePairOverBudget
        expr: |-
          (
            sf_database:request_rate:1d > sf_database:budget_ceiling
          ) and on (operation, caller_daemon) sf_database:budget:enforced
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: >-
            {{{{ $labels.caller_daemon }}}} is making far more
            {{{{ $labels.operation }}}} calls than a cluster of this shape
            should
          description: >-
            Measured {{{{ $value | printf "%.2f" }}}} calls per second
            against a modelled ceiling for this cluster's node and
            instance counts. Either something is doing more database work
            than it used to, or the cluster changed shape in a way the
            model does not capture. Run "sf-ctl database-load --json" and
            attach the output to an issue.

      - alert: ShakenFistUnbudgetedDatabasePolling
        expr: |-
          (
            sf_database:request_rate:1d
              unless on (operation, caller_daemon)
                sf_database:budget:per_node_base
          ) > {unbudgeted}
        for: 1h
        labels:
          severity: warning
        annotations:
          summary: >-
            {{{{ $labels.caller_daemon }}}} is calling
            {{{{ $labels.operation }}}} at a steady rate nobody budgeted
            for
          description: >-
            This (operation, caller_daemon) pair is not in Shaken Fist's
            shipped load budget and has sustained
            {{{{ $value | printf "%.2f" }}}} calls per second for an hour,
            which is what a new polling loop looks like. If you are
            running a modified Shaken Fist this may be yours; otherwise
            please report it.

      - alert: ShakenFistDatabaseLoadModelBlind
        expr: absent(sf_database:cluster_nodes)
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: The database load model cannot see the cluster's shape
          description: >-
            instances_active is absent, so every modelled value above is
            empty and neither of the other alerts in this file can fire.
            That looks exactly like a healthy cluster, which is why this
            alert exists. Scrape sf-resources on RESOURCES_METRICS_PORT
            (13007 by default) on every node.
'''.format(multiplier=defaults.tolerance_multiplier,
           floor=defaults.tolerance_floor_qps,
           unbudgeted=defaults.unbudgeted_fixed_rate_qps))

    sys.stdout.write(''.join(out))


if __name__ == '__main__':
    main()
