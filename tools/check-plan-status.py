#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Check that plan statuses and the plan index agree with each other.

`docs/plans/index.md` carries one row per master plan: a status, and the
arithmetic of how many of that plan's phases are complete. Both are derived
quantities -- the truth lives in each master plan's own Execution table -- so
both drift the moment someone updates one and not the other. That drift is
what motivated the index rework in the first place, and the vocabulary this
checks is justified on being machine readable, so this is the machine that
reads it.

Four things are checked:

* every status cell in a master plan's Execution table is one of the seven
  vocabulary terms, and nothing else;
* every index row's status is a vocabulary term too;
* each index row's `N of M` recomputes from the linked plan's Execution
  table; and
* every master plan in `docs/plans/` is registered in both `index.md` and
  `order.yml`.

The vocabulary is the `plan-status-vocabulary` shared block in
`PLAN-TEMPLATE.md`; the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`.

Four plans predate the Execution-table convention and are counted by hand.
They are named in `HAND_COUNTED` rather than detected, so that a plan cannot
join them by quietly omitting its table: adding a name here is a deliberate
edit, and the index preamble lists the same four for readers.
"""

import os
import re
import sys


PLANS_DIR = os.path.join('docs', 'plans')
INDEX = os.path.join(PLANS_DIR, 'index.md')
ORDER = os.path.join(PLANS_DIR, 'order.yml')

# The whole of the shared vocabulary. Matching is case-insensitive, per the
# block, but the canonical spelling is the one to write.
VOCABULARY = ('Proposed', 'Not started', 'In progress', 'Blocked', 'Complete',
              'Abandoned', 'Superseded')

# Plans whose phases are not in an Execution table, so their index arithmetic
# cannot be recomputed. See the index preamble, which names the same four.
HAND_COUNTED = {
    'blob-storage-roadmap.md',          # phases as headings
    'api-query-batching-roadmap.md',    # phases as headings
    'PLAN-attribute-field-masks.md',    # phases as headings
    'PLAN-queue-performance.md',        # numbered in steps, not phases
}

PHASE_FILE_RE = re.compile(r'-phase-\d')
SEPARATOR_RE = re.compile(r'^\|[\s:|-]+\|$')
LINK_RE = re.compile(r'\[[^\]]*\]\(([^)]+)\)')
INDEX_ROW_RE = re.compile(r'^\|\s*\d{4}-\d{2}-\d{2}\s*\|')

# A row of dots is how a plan writes "and so on" in a table it has not
# finished decomposing. It is not a status claim.
ELLIPSIS = '...'


def cells(line):
    """The cells of a markdown table row."""
    return [c.strip() for c in line.strip().strip('|').split('|')]


def status_tables(path, require_phase=False):
    """Every table in path with a Status column, as lists of status cells.

    A header is recognised by the separator row beneath it rather than by its
    content, so a data row which happens to look like a header cannot start a
    phantom table. Prose between two tables ends the first one.
    """
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines()

    tables = []
    current = None
    for offset, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith('|'):
            current = None
            continue
        if SEPARATOR_RE.match(stripped):
            continue

        following = lines[offset + 1].strip() if offset + 1 < len(lines) else ''
        if following.startswith('|') and SEPARATOR_RE.match(following):
            names = [c.lower() for c in cells(stripped)]
            current = None
            if 'status' in names and (not require_phase or 'phase' in names):
                current = {'index': names.index('status'), 'rows': []}
                tables.append(current)
            continue

        if current is None:
            continue
        row = cells(stripped)
        if current['index'] < len(row):
            current['rows'].append((offset + 1, row[current['index']]))
    return tables


def execution_table(path):
    """The phase table of a master plan: the largest Phase-and-Status table.

    A plan may carry more than one -- a cross-repo plan tracks phases in a
    sibling repository too -- and the longest is the plan's own.
    """
    tables = [t for t in status_tables(path, require_phase=True) if t['rows']]
    if not tables:
        return None
    return max(tables, key=lambda t: len(t['rows']))


def masters(plans_dir):
    """Master plan filenames: everything in plans_dir bar phases and the index."""
    return sorted(
        name for name in os.listdir(plans_dir)
        if name.endswith('.md') and name != 'index.md'
        and not PHASE_FILE_RE.search(name)
        and os.path.isfile(os.path.join(plans_dir, name)))


def index_rows(path):
    """The index's plan rows, as {plan filename: (lineno, status, phases)}."""
    rows = {}
    with open(path, encoding='utf-8') as f:
        for lineno, line in enumerate(f, start=1):
            if not INDEX_ROW_RE.match(line):
                continue
            row = cells(line)
            if len(row) < 5:
                continue
            link = LINK_RE.search(row[1])
            if not link:
                continue
            rows[os.path.basename(link.group(1).split('#')[0])] = (
                lineno, row[3], row[4])
    return rows


def main():
    if not os.path.isdir(PLANS_DIR):
        return 0

    known = {term.lower() for term in VOCABULARY}
    problems = []

    plans = masters(PLANS_DIR)
    rows = index_rows(INDEX)

    for name in plans:
        path = os.path.join(PLANS_DIR, name)

        table = execution_table(path)
        if table:
            for lineno, value in table['rows']:
                if value != ELLIPSIS and value.lower() not in known:
                    problems.append(
                        '%s:%d: phase status %r is not one of %s'
                        % (path, lineno, value, ', '.join(VOCABULARY)))

        if name not in rows:
            problems.append(
                '%s: master plan is not registered in %s' % (path, INDEX))
            continue

        lineno, status, phases = rows[name]
        if status.lower() not in known:
            problems.append('%s:%d: index status %r is not one of %s'
                            % (INDEX, lineno, status, ', '.join(VOCABULARY)))

        if name in HAND_COUNTED:
            continue

        if table is None:
            expected = '—'
        else:
            counted = [v for _, v in table['rows'] if v != ELLIPSIS]
            done = sum(1 for v in counted if v.lower() == 'complete')
            expected = '%d of %d' % (done, len(counted))

        # A plan whose phases are enumerated but provisional is allowed to
        # publish no arithmetic; publishing the wrong arithmetic is not.
        if phases != expected and not (phases == '—' and table is not None):
            problems.append(
                '%s:%d: %s says %r, but %s counts %r'
                % (INDEX, lineno, name, phases, path, expected))

    with open(ORDER, encoding='utf-8') as f:
        ordered = f.read()
    for name in plans:
        if ('- %s:' % name) not in ordered:
            problems.append('%s: master plan is not registered in %s'
                            % (os.path.join(PLANS_DIR, name), ORDER))

    for stale in sorted(set(rows) - set(plans)):
        problems.append('%s: %s is listed but is not a master plan in %s'
                        % (INDEX, stale, PLANS_DIR))

    if problems:
        print('Plan status problems found:\n')
        for problem in problems:
            print('  %s' % problem)
        print('\n%d problem(s). The status vocabulary is the '
              'plan-status-vocabulary block in PLAN-TEMPLATE.md; the index\'s '
              '"N of M" is counted from each plan\'s Execution table.'
              % len(problems))
        return 1

    print('Plan statuses and index arithmetic agree (%d master plans).'
          % len(plans))
    return 0


if __name__ == '__main__':
    sys.exit(main())
