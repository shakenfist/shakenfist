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

These things are checked:

* every status cell in a master plan's Execution table is one of the seven
  vocabulary terms, and nothing else;
* every index row's status is a vocabulary term too, and does not contradict
  the phases beneath it in either direction;
* each index row's `N of M` recomputes from the linked plan's Execution
  table, and no Execution row is too short to be counted;
* every master plan in `docs/plans/` is registered in both `index.md` and
  `order.yml`, and neither names a plan that is not there;
* every markdown link between plans resolves to a file that exists; and
* every phase plan is linked from somewhere in `docs/plans/`.

Example tables and example links do not count as either. Plans document the
phase-table convention by showing one -- the `plan-file-conventions` shared
block carries an indented example, PLAN-qemu-futures.md a fenced one -- so
fenced blocks are skipped and a table row must start in column zero. Before
that, both examples parsed as real tables, and because the largest
Phase-and-Status table wins, a small plan created from the template could
have had its arithmetic computed from the template's placeholder rows.

That last one is the invariant the template's own wording now rests on.
Phase plans are deliberately absent from `order.yml`, and the index lists
master plans only, which leaves the master plan's Execution table as the
sole path to a phase document. A phase filename written as bare text
rather than a link is therefore a page nothing in the repository leads to.
Thirty-eight of the ninety-two phase plans were in that state before this
check existed.

The published site is a weaker claim than it looks: `mkdocs.yml.tmpl`
lists the Plans nav by hand and names three plans, so most master plans do
not reach the navigation either. The rule here is about the repository's
own navigability, which is where these documents are actually read.

The vocabulary is read from the `plan-status-vocabulary` shared block in
`PLAN-TEMPLATE.md` rather than copied here, so that a term added or renamed
in the canonical block (which lives in shakenfist/development, at
`templates/shared-blocks/plan-status-vocabulary.md`) cannot leave this
checker rejecting a status the template tells authors to write.

Four plans predate the Execution-table convention and are counted by hand,
and two more keep a table which is a placeholder rather than a phase list.
They are named in `HAND_COUNTED` and `PROVISIONAL` rather than detected, so
that a plan cannot join them by quietly omitting or stubbing its table:
adding a name is a deliberate edit, and the index preamble names the same
six for readers.

`order.yml` is registration, not navigation. Nothing in this repository
renders it -- `mkdocs.yml.tmpl` lists the Plans nav by hand -- but the format
and its semantics belong to `tools/sync_component_docs.py` in
shakenfist/actions, which every repository whose docs are synced into a site
uses as a per-directory nav allowlist. Keeping the file complete is what
makes generating that nav from it a later mechanical change rather than an
archaeology exercise.
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

# The index status is asserted to follow the phase table, in the index
# preamble and in the template's note. These are the two directions of that
# rule which hold whatever a plan does with abandoned or superseded phases.
UNFINISHED = {'proposed', 'not started', 'in progress', 'blocked'}
STARTLESS = {'proposed', 'not started'}

PHASE_FILE_RE = re.compile(r'-phase-\d')
FENCE_RE = re.compile(r'^\s*(?:```|~~~)')
SEPARATOR_RE = re.compile(r'^\|[\s:|-]+\|$')
CELL_SPLIT_RE = re.compile(r'(?<!\\)\|')
ABSOLUTE_RE = re.compile(r'^(?:[a-z][a-z0-9+.-]*:|//|/)')
# order.yml is a flat list of single-key mappings. Anchoring to the start of
# the line matters: a substring test is satisfied by a commented-out entry,
# which is the most likely way to produce exactly the unregistered state the
# check exists to catch. Commented entries are matched separately rather than
# ignored, because in a directory order.yml commenting an entry out is the
# sanctioned way to keep a page out of the navigation (see
# tools/sync_component_docs.py in shakenfist/actions), so the two states
# deserve different messages.
ORDER_ENTRY_RE = re.compile(r'^[ \t]*-[ \t]*([^\s:#][^:\n]*):', re.MULTILINE)
ORDER_COMMENTED_RE = re.compile(r'^[ \t]*#[ \t]*-[ \t]*([^\s:#][^:\n]*):',
                                re.MULTILINE)
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


def outside_code(lines):
    """The lines of a document with fenced code blocks blanked out.

    Plans document the phase-table convention by showing one, so a naive
    parse finds example tables and example links in prose and treats them
    as real. Blanking rather than dropping the lines keeps every later
    offset -- and so every line number in an error message -- honest.
    """
    masked = []
    fenced = False
    for line in lines:
        if FENCE_RE.match(line):
            fenced = not fenced
            masked.append('')
            continue
        masked.append('' if fenced else line)
    return masked


def status_tables(path, require_phase=False):
    """Every table in path with a Status column, as lists of status cells.

    A header is recognised by the separator row beneath it rather than by its
    content, so a data row which happens to look like a header cannot start a
    phantom table. Prose between two tables ends the first one.

    A row must begin in column zero. That is what separates a plan's own
    table from the example one in the `plan-file-conventions` shared block,
    which sits two spaces deep inside a bullet; fenced examples are already
    gone by the time this reads the lines. Both were being parsed, and
    since execution_table() takes the longest table, a plan created from
    the template could have had its arithmetic computed from the template's
    own placeholder rows.

    Returns tables as {'index', 'rows', 'short'}: 'short' collects rows with
    too few cells to reach the status column, which are reported rather than
    dropped -- a hidden row is the drift this checker exists to catch.
    """
    with open(path, encoding='utf-8') as f:
        lines = outside_code(f.read().splitlines())

    tables = []
    current = None
    for offset, line in enumerate(lines):
        if not line.startswith('|'):
            current = None
            continue
        if SEPARATOR_RE.match(line):
            continue

        following = lines[offset + 1] if offset + 1 < len(lines) else ''
        if following.startswith('|') and SEPARATOR_RE.match(following):
            names = [c.lower() for c in cells(line)]
            current = None
            if 'status' in names and (not require_phase or 'phase' in names):
                current = {'index': names.index('status'), 'rows': [], 'short': []}
                tables.append(current)
            continue

        if current is None:
            continue
        row = cells(line)
        if current['index'] < len(row):
            current['rows'].append((offset + 1, row[current['index']]))
        else:
            current['short'].append((offset + 1, len(row)))
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
    something links to. Fenced code blocks are skipped: a link displayed as
    an example is not a way to reach anything, for the same reason an
    example table is not a phase table.
    """
    targets = []
    inbound = set()
    for name in sorted(os.listdir(plans_dir)):
        if not name.endswith('.md'):
            continue
        path = os.path.join(plans_dir, name)
        with open(path, encoding='utf-8') as f:
            lines = outside_code(f.read().splitlines())
        for lineno, line in enumerate(lines, start=1):
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


def problems():
    """Every inconsistency found, as a list of human-readable strings.

    Separate from main() so that the tests can assert on *which* rule fired
    rather than on the exit code: a fixture broken one way usually trips
    more than one rule, and an exit code cannot tell them apart.
    """
    def under(*parts):
        return os.path.normpath(os.path.join(REPO_ROOT, *parts))

    plans_dir = under(PLANS_DIR)
    index = under(INDEX)
    order = under(ORDER)
    template = under(TEMPLATE)

    if not os.path.isdir(plans_dir):
        return []

    terms = vocabulary(template)
    if not terms:
        return ['%s carries no plan-status-vocabulary shared block, so there '
                'is nothing to check statuses against. Copy it from '
                'templates/shared-blocks/ in shakenfist/development.'
                % template]
    known = {term.lower() for term in terms}
    listed = ', '.join(terms)

    found = []
    plans = masters(plans_dir)
    rows = index_rows(index)

    for name in plans:
        path = os.path.join(plans_dir, name)

        table = execution_table(path)
        if table:
            for lineno, value in table['rows']:
                if value != ELLIPSIS and value.lower() not in known:
                    found.append('%s:%d: phase status %r is not one of %s'
                                 % (path, lineno, value, listed))
            for lineno, width in table['short']:
                found.append(
                    '%s:%d: Execution table row has %d cell(s), too few to '
                    'reach the status column, so it is invisible to the '
                    'phase count.' % (path, lineno, width))

        if name not in rows:
            found.append('%s: master plan is not registered in %s'
                         % (path, index))
            continue

        lineno, status, phases = rows[name]
        if status.lower() not in known:
            found.append('%s:%d: index status %r is not one of %s'
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
            found.append('%s:%d: %s says %r, but %s counts %r'
                         % (index, lineno, name, phases, path, expected))

        if table is None:
            continue
        values = {v.lower() for _, v in table['rows'] if v != ELLIPSIS}
        if status.lower() == 'complete' and values & UNFINISHED:
            found.append(
                '%s:%d: %s is Complete, but %s still has a phase which is '
                'not. A phase may be Complete, Abandoned or Superseded for '
                'the plan to be done.' % (index, lineno, name, path))
        if status.lower() in STARTLESS and values and not (values & STARTLESS):
            found.append(
                '%s:%d: %s is %s, but every phase in %s has been resolved.'
                % (index, lineno, name, status, path))

    with open(order, encoding='utf-8') as f:
        raw = f.read()
    ordered = set(ORDER_ENTRY_RE.findall(raw))
    commented = set(ORDER_COMMENTED_RE.findall(raw))
    for name in plans:
        if name in ordered:
            continue
        if name in commented:
            found.append(
                '%s: master plan is commented out of %s, which hides it from '
                'the navigation while the index still publishes a row for it.'
                % (os.path.join(plans_dir, name), order))
        else:
            found.append('%s: master plan is not registered in %s'
                         % (os.path.join(plans_dir, name), order))
    for entry in sorted(ordered):
        if not os.path.isfile(os.path.join(plans_dir, entry)):
            found.append('%s: lists %r, which is not a file in %s'
                         % (order, entry, plans_dir))

    for stale in sorted(set(rows) - set(plans)):
        found.append('%s: %s is listed but is not a master plan in %s'
                     % (index, stale, plans_dir))

    targets, inbound = plan_links(plans_dir)
    for source, lineno, target in targets:
        resolved = os.path.normpath(
            os.path.join(os.path.dirname(source), target))
        if not os.path.isfile(resolved):
            found.append('%s:%d: link to %r, which does not exist'
                         % (source, lineno, target))

    for name in sorted(os.listdir(plans_dir)):
        if not name.endswith('.md') or not PHASE_FILE_RE.search(name):
            continue
        if name not in inbound:
            found.append(
                '%s: phase plan has no inbound link, so nothing in the '
                'published documentation leads to it. Link it from its '
                "master plan's Execution table."
                % os.path.join(plans_dir, name))

    return found


def main():
    found = problems()
    if found:
        print('Plan status problems found:\n')
        for problem in found:
            print('  %s' % problem)
        print('\n%d problem(s). The status vocabulary is the '
              'plan-status-vocabulary block in %s; the index\'s "N of M" is '
              'counted from each plan\'s Execution table; and a phase plan '
              'is reachable only from that table.' % (len(found), TEMPLATE))
        return 1

    print('Plan statuses, index arithmetic and phase links agree.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
