#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Check that documentation does not cite plan phase numbers.

Documentation describes the current state of the software, not the
history of how it was built. "Feature YYY, implemented in phase ZZZ"
tells a reader nothing they need: either the feature is implemented, in
which case the docs describe it plainly, or it is not, in which case
they link to the master plan in docs/plans/. This is the fleet-wide
plan-phase-references consistency audit, enforced here at commit time
rather than by a report the morning after it lands.

The pattern is deliberately looser than the audit's `phase <number>`: a
lettered sub-phase such as "phase 4a" slips past the audit's trailing
word boundary but is exactly the same defect, and one lived in the
operator guide for that reason.

Skipped, matching the audit spec: any file under a `plans/` directory
at any depth (plan documents legitimately discuss their own phases),
`docs/components/` (an automated import of other repositories'
documentation, fixed at the source rather than here), fenced code
blocks and inline code spans, and lines carrying an explicit
`<!-- audit-ok: phase-reference -->` marker.
"""

import os
import re
import sys


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOCS_DIR = 'docs'

# The root markdown files are in scope for the same reason the docs are:
# they describe current behaviour to a reader who was not present for
# the construction.
ROOT_FILES = ('AGENTS.md', 'ARCHITECTURE.md', 'README.md')

EXCLUDED_DIR_NAMES = ('plans', 'components')

PHASE_RE = re.compile(r'\bphase\s+\d+', re.IGNORECASE)
INLINE_CODE_RE = re.compile(r'`[^`]*`')
MARKER = '<!-- audit-ok: phase-reference -->'


def markdown_files(root_dir=None):
    root_dir = root_dir or REPO_ROOT
    for name in ROOT_FILES:
        path = os.path.join(root_dir, name)
        if os.path.isfile(path):
            yield path
    for dirpath, dirnames, filenames in os.walk(
            os.path.join(root_dir, DOCS_DIR)):
        dirnames[:] = sorted(
            d for d in dirnames if d not in EXCLUDED_DIR_NAMES)
        for filename in sorted(filenames):
            if filename.endswith('.md'):
                yield os.path.join(dirpath, filename)


def phase_references(path):
    """(line number, matched text) for each phase reference in one file."""
    found = []
    in_fence = False
    with open(path, errors='replace') as f:
        for number, line in enumerate(f, start=1):
            if line.lstrip().startswith(('```', '~~~')):
                in_fence = not in_fence
                continue
            if in_fence or MARKER in line:
                continue
            for match in PHASE_RE.finditer(INLINE_CODE_RE.sub('', line)):
                found.append((number, match.group(0)))
    return found


def problems(root_dir=None):
    found = []
    for path in markdown_files(root_dir=root_dir):
        for number, text in phase_references(path):
            found.append(
                f'{path}:{number}: plan phase reference {text!r} -- '
                'describe the current behaviour, or link the master plan '
                'in docs/plans/ instead')
    return found


def main():
    found = problems()
    for problem in found:
        print(problem)
    if found:
        print(f'\n{len(found)} plan phase reference(s) in documentation.')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
