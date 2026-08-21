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

Six things are checked:

* every status cell in a master plan's Execution table is one of the seven
  vocabulary terms, and nothing else;
* every index row's status is a vocabulary term too;
* each index row's `N of M` recomputes from the linked plan's Execution
  table;
* every master plan in `docs/plans/` is registered in both `index.md` and
  `order.yml`;
* every markdown link between plans resolves to a file that exists; and
* every phase plan is linked from somewhere in `docs/plans/`.

That last one is the invariant the template's own wording now rests on.
Phase plans are deliberately absent from `order.yml`, so they are absent
from the site navigation, and the index lists master plans only -- which
leaves the master plan's Execution table as the sole path to a phase
document. A phase filename written as bare text rather than a link is
therefore a page no reader can reach. Thirty-eight of the ninety-two phase
plans were in that state before this check existed.

The vocabulary is read from the `plan-status-vocabulary` shared block in
`PLAN-TEMPLATE.md` rather than copied here, so that a term added or renamed
in the canonical block (which lives in shakenfist/development, at
`templates/shared-blocks/plan-status-vocabulary.md`) cannot leave this
checker rejecting a status the template tells authors to write.

Four plans predate the Execution-table convention and are counted by hand.
They are named in `HAND_COUNTED` rather than detected, so that a plan cannot
join them by quietly omitting its table: adding a name here is a deliberate
edit, and the index preamble lists the same four for readers.
"""

import os
import re
import sys


# Overridden by the tests, which point the checker at a fixture tree. The
# default is the working directory, which is where pre-commit runs it.
REPO_ROOT = '.'

PLANS_DIR = os.path.join('docs', 'plans')
INDEX = os.path.join(PLANS_DIR, 'index.md')
ORDER = os.path.join(PLANS_DIR, 'order.yml')

TEMPLATE = 'PLAN-TEMPLATE.md'

# The vocabulary block lists its terms as backticked bullets. Reading them
# from the template keeps this checker and the document authors are handed
# from ever disagreeing.
VOCABULARY_BLOCK_RE = re.compile(
    r'<!-- shared-block: plan-status-vocabulary v\d+ -->(.*?)<!-- shared-block-end -->',
    re.DOTALL)
VOCABULARY_TERM_RE = re.compile(r'^- `([^`]+)`', re.MULTILINE)

# Plans whose phases are not in an Execution table, so their index arithmetic
# cannot be recomputed. See the index preamble, which names the same four.
HAND_COUNTED = {
    'blob-storage-roadmap.md',          # phases as headings
    'api-query-batching-roadmap.md',    # phases as headings
    'PLAN-attribute-field-masks.md',    # phases as headings
    'PLAN-queue-performance.md',        # numbered in steps, not phases
}

# Plans whose Execution table is explicitly a placeholder, so its row count
# is not a phase count and the index publishes no arithmetic for them. Named
# rather than detected, for the same reason HAND_COUNTED is: an exemption
# should be a reviewable edit, not a silent one.
PROVISIONAL = {
    'PLAN-qemu-futures.md',         # open-ended, table ends in an ellipsis row
    'PLAN-artifact-ux-rework.md',   # "0. Decisions pass" plus "(later phases)"
}

PHASE_FILE_RE = re.compile(r'-phase-\d')
SEPARATOR_RE = re.compile(r'^\|[\s:|-]+\|$')
CELL_SPLIT_RE = re.compile(r'(?<!\\)\|')
ABSOLUTE_RE = re.compile(r'^(?:[a-z][a-z0-9+.-]*:|//|/)')
LINK_RE = re.compile(r'\[[^\]]*\]\(([^)]+)\)')
INDEX_ROW_RE = re.compile(r'^\|\s*\d{4}-\d{2}-\d{2}\s*\|')

# A row of dots is how a plan writes "and so on" in a table it has not
# finished decomposing. It is not a status claim.
ELLIPSIS = '...'


def cells(line):
    """The cells of a markdown table row.

    Splitting on every pipe would shift each cell after an escaped one
    (`\\|`, which plans use to quote LogQL and shell pipelines) a column to
    the left, so the status would be read out of the wrong cell and the
    resulting complaint would name the wrong file.
    """
    parts = CELL_SPLIT_RE.split(line.strip().strip('|'))
    return [part.replace('\\|', '|').strip() for part in parts]


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


def vocabulary(template_path):
    """The status terms, read from the shared block in PLAN-TEMPLATE.md."""
    with open(template_path, encoding='utf-8') as f:
        block = VOCABULARY_BLOCK_RE.search(f.read())
    if not block:
        return []
    return VOCABULARY_TERM_RE.findall(block.group(1))


def plan_links(plans_dir):
    """Every markdown link between plan documents.

    Returns (targets, inbound): targets is a list of (source, lineno, target)
    for links naming a `.md` file, and inbound is the set of plan filenames
    something links to.
    """
    targets = []
    inbound = set()
    for name in sorted(os.listdir(plans_dir)):
        if not name.endswith('.md'):
            continue
        path = os.path.join(plans_dir, name)
        with open(path, encoding='utf-8') as f:
            for lineno, line in enumerate(f, start=1):
                for raw in LINK_RE.findall(line):
                    target = raw.split('#')[0].strip()
                    if not target.endswith('.md'):
                        continue
                    if ABSOLUTE_RE.match(target):
                        # An absolute URL is the docs-external-links audit's
                        # business, and its target is not on this filesystem.
                        continue
                    base = os.path.basename(target)
                    if base == name:
                        # A plan naming itself is not a way in.
                        continue
                    inbound.add(base)
                    targets.append((path, lineno, target))
    return targets, inbound


def main():
    def under(*parts):
        return os.path.normpath(os.path.join(REPO_ROOT, *parts))

    plans_dir = under(PLANS_DIR)
    index = under(INDEX)
    order = under(ORDER)
    template = under(TEMPLATE)

    if not os.path.isdir(plans_dir):
        return 0

    terms = vocabulary(template)
    if not terms:
        print('%s carries no plan-status-vocabulary shared block, so there '
              'is nothing to check statuses against. Copy it from '
              'templates/shared-blocks/ in shakenfist/development.' % template)
        return 1
    known = {term.lower() for term in terms}
    listed = ', '.join(terms)

    problems = []
    plans = masters(plans_dir)
    rows = index_rows(index)

    for name in plans:
        path = os.path.join(plans_dir, name)

        table = execution_table(path)
        if table:
            for lineno, value in table['rows']:
                if value != ELLIPSIS and value.lower() not in known:
                    problems.append('%s:%d: phase status %r is not one of %s'
                                    % (path, lineno, value, listed))

        if name not in rows:
            problems.append(
                '%s: master plan is not registered in %s' % (path, index))
            continue

        lineno, status, phases = rows[name]
        if status.lower() not in known:
            problems.append('%s:%d: index status %r is not one of %s'
                            % (index, lineno, status, listed))

        if name in HAND_COUNTED:
            continue

        if table is None or name in PROVISIONAL:
            expected = '—'
        else:
            counted = [v for _, v in table['rows'] if v != ELLIPSIS]
            done = sum(1 for v in counted if v.lower() == 'complete')
            expected = '%d of %d' % (done, len(counted))

        if phases != expected:
            problems.append('%s:%d: %s says %r, but %s counts %r'
                            % (index, lineno, name, phases, path, expected))

    with open(order, encoding='utf-8') as f:
        ordered = f.read()
    for name in plans:
        if ('- %s:' % name) not in ordered:
            problems.append('%s: master plan is not registered in %s'
                            % (os.path.join(plans_dir, name), order))

    for stale in sorted(set(rows) - set(plans)):
        problems.append('%s: %s is listed but is not a master plan in %s'
                        % (index, stale, plans_dir))

    targets, inbound = plan_links(plans_dir)
    for source, lineno, target in targets:
        resolved = os.path.normpath(
            os.path.join(os.path.dirname(source), target))
        if not os.path.isfile(resolved):
            problems.append('%s:%d: link to %r, which does not exist'
                            % (source, lineno, target))

    for name in sorted(os.listdir(plans_dir)):
        if not name.endswith('.md') or not PHASE_FILE_RE.search(name):
            continue
        if name not in inbound:
            problems.append(
                '%s: phase plan has no inbound link, so nothing in the '
                'published documentation leads to it. Link it from its '
                "master plan's Execution table."
                % os.path.join(plans_dir, name))

    if problems:
        print('Plan status problems found:\n')
        for problem in problems:
            print('  %s' % problem)
        print('\n%d problem(s). The status vocabulary is the '
              'plan-status-vocabulary block in %s; the index\'s "N of M" is '
              'counted from each plan\'s Execution table; and a phase plan '
              'is reachable only from that table.' % (len(problems), template))
        return 1

    print('Plan statuses, index arithmetic and phase links agree '
          '(%d master plans, %d terms).' % (len(plans), len(terms)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
