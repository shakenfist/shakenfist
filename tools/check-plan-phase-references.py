#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Check that nothing cites a plan phase, decision or step number.

Documentation describes the current state of the software, not the
history of how it was built. "Feature YYY, implemented in phase ZZZ"
tells a reader nothing they need: either the feature is implemented, in
which case the docs describe it plainly, or it is not, in which case
they link to the master plan in docs/plans/. This is the fleet-wide
plan-phase-references consistency audit, enforced here at commit time
rather than by a report the morning after it lands.

The pattern has no trailing word boundary, so that a lettered
sub-phase such as `phase 4a` matches: it is exactly the same defect,
and one lived in the operator guide.

Skipped, matching the audit spec: any file under a `plans/` directory
at any depth (plan documents legitimately discuss their own phases),
`docs/components/` (an automated import of other repositories'
documentation, fixed at the source rather than here), fenced code
blocks and inline code spans, and lines carrying an explicit
`<!-- audit-ok: phase-reference -->` marker.

Source and configuration are held to the same rule, for the same
reason and with three more shapes -- `phase-4`, `decision 3`,
`step 5f` -- by source_problems(). A comment saying a branch was added
in a numbered phase records how the code came to be rather than why it
is the way it is, and the reader of the code has not read the plan.
A pointer to a `PLAN-*.md` file is left alone: an unbuilt behaviour may
legitimately name the plan which will build it, and whether the file
still exists is a different check. So is a URL.

Unlike the fleet-wide audit, which reads one line at a time, the source
check also matches a citation split by a wrap -- `Phase` at the end of
one comment line and `5` at the start of the next. Seven of those were
in this repository while the audit reported one reference, because
nothing could see them.
"""

import os
import re
import subprocess
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

# The source side. The bare token is what the audit accepts in whatever
# comment syntax the file uses, and the markdown marker above contains
# it, so one membership test serves both.
SOURCE_MARKER = 'audit-ok: phase-reference'
SOURCE_FILE_MARKER = 'audit-ok: phase-reference-file'

# A run of backticks and its contents, so that a comment may quote the
# shape it is talking about. The backreference makes the closing run the
# same length as the opening one, so ``phase 4`` is one span rather than
# two empty ones with live prose between them.
CODE_SPAN_RE = re.compile(r'(`+)[^`]*\1')
URL_RE = re.compile(r'\w+://\S+')
PLAN_FILE_RE = re.compile(r'PLAN-[\w.-]*\.md')

SOURCE_SHAPES_RE = re.compile(
    r'\bphases?\s+\d+|\bphase-\d+\b|\bdecisions?\s+\d+(\.\d+)?\b'
    r'|\bsteps?\s+\d+[a-z]\b', re.IGNORECASE)

# The same shapes, split across a line wrap: the noun at the end of one
# line and the number at the start of the next, past any comment or
# docstring decoration. A bare `step 3` is deliberately not a citation
# ("step 1: open the file" is ordinary prose), so the step shape still
# demands its letter.
WRAPPED_NOUN_RE = re.compile(
    r'\b(phases?|decisions?|steps?)\s*$', re.IGNORECASE)
WRAPPED_NUMBER_RE = re.compile(r'^[\s#*]*(\d+[a-z]?|\d+\.\d+)\b')

# Not in scope: markdown is audited as documentation, mkdocs.yml carries
# a nav entry per plan phase page, and a plans directory is the record of
# how the software was built.
SOURCE_SKIP_NAMES = ('mkdocs.yml', 'mkdocs.yaml')

# 2 MB, as the audit does. Past that it is data, not prose.
SOURCE_MAX_BYTES = 2 * 1024 * 1024


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


def _tracked_paths(root_dir):
    """Every file git knows about, or every file present if git does not."""
    try:
        listing = subprocess.run(
            ['git', '-C', root_dir, 'ls-files', '-z'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
        return sorted(p for p in listing.stdout.decode().split('\0') if p)
    except (OSError, subprocess.CalledProcessError):
        found = []
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith('.'))
            for filename in sorted(filenames):
                found.append(os.path.relpath(
                    os.path.join(dirpath, filename), root_dir))
        return sorted(found)


def source_files(root_dir=None):
    """The source and configuration files the audit reads."""
    root_dir = root_dir or REPO_ROOT
    for relative in _tracked_paths(root_dir):
        if relative.endswith('.md'):
            continue
        if os.path.basename(relative) in SOURCE_SKIP_NAMES:
            continue
        if 'plans' in relative.split(os.sep)[:-1]:
            continue
        path = os.path.join(root_dir, relative)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        if os.path.getsize(path) > SOURCE_MAX_BYTES:
            continue
        yield path


def _readable_lines(path):
    """The file's lines, or None if it is not text."""
    with open(path, 'rb') as f:
        raw = f.read()
    if b'\0' in raw:
        return None
    return raw.decode(errors='replace').splitlines()


def _scannable(line):
    """One line with everything that is an address rather than prose gone."""
    line = CODE_SPAN_RE.sub('', line)
    line = URL_RE.sub('', line)
    return PLAN_FILE_RE.sub('', line)


def plan_history_references(path):
    """(line number, matched text) for each plan citation in one file."""
    lines = _readable_lines(path)
    if lines is None:
        return []

    if any(SOURCE_FILE_MARKER in line for line in lines):
        return []

    found = []
    for index, line in enumerate(lines):
        if SOURCE_MARKER in line:
            continue
        scannable = _scannable(line)
        for match in SOURCE_SHAPES_RE.finditer(scannable):
            found.append((index + 1, match.group(0)))

        noun = WRAPPED_NOUN_RE.search(scannable)
        if not noun or index + 1 >= len(lines):
            continue
        following = _scannable(lines[index + 1])
        if SOURCE_MARKER in lines[index + 1]:
            continue
        number = WRAPPED_NUMBER_RE.match(following)
        if not number:
            continue
        # match() rather than fullmatch(): a lettered sub-phase ("phase
        # 4a") is the same citation, and the phase shape carries no
        # trailing boundary precisely so that it matches. The step shape
        # does demand its letter, so a wrapped "steps 2 and 3" is still
        # read as prose.
        wrapped = f'{noun.group(1)} {number.group(1)}'
        if SOURCE_SHAPES_RE.match(wrapped):
            found.append((index + 1, wrapped))
    return found


def source_problems(root_dir=None):
    found = []
    for path in source_files(root_dir=root_dir):
        for number, text in plan_history_references(path):
            found.append(
                f'{path}:{number}: plan history reference {text!r} -- '
                'write the reason the code is this way instead of citing '
                'the plan step which produced it')
    return found


def main():
    found = problems() + source_problems()
    for problem in found:
        print(problem)
    if found:
        print(f'\n{len(found)} plan phase or history reference(s).')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
