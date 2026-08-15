#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Check that documentation links and their anchors resolve.

A link like `[network](#network)` or
`[object states](/developer_guide/state_machine/#upload)` fails silently when
its target anchor does not exist: mkdocs does not warn, the link still renders,
and the reader is dropped at the top of the page instead. A heading rename
therefore breaks every inbound link to it without anything noticing. A link
whose target *file* is gone fails just as quietly.

This checker resolves each link's target file, reports the ones that do not
exist, and for anchored links collects the anchors that the target file
actually defines (both generated heading slugs and explicit `{#id}`
attributes, which the attr_list extension honours) and reports the links whose
anchor is absent.

Sources are every page under `docs/` plus the root markdown files, which
`ROOT_FILES` names. The root files are now indexes into `docs/` rather than
documents in their own right, so their links are the ones a heading rename is
most likely to break, and mkdocs never sees them at all.

`docs/plans/` and `docs/components/` are excluded as sources: plans are
point-in-time records which are not maintained after they land, and components
are synchronised in from other repositories. Both remain valid link targets.
"""

import os
import re
import sys


DOCS_DIR = 'docs'
EXCLUDED_PREFIXES = (
    os.path.join(DOCS_DIR, 'plans'),
    os.path.join(DOCS_DIR, 'components'),
)

# Root markdown files which link into docs/. These are not part of the mkdocs
# site, so nothing else validates them.
ROOT_FILES = ('AGENTS.md', 'ARCHITECTURE.md', 'CLAUDE.md', 'README.md')

HEADING_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*$')
ATTR_ID_RE = re.compile(r'\{#([^}]+)\}')
LINK_RE = re.compile(r'\]\(([^)\s]+)\)')
# Anything addressed by scheme (https:, mailto:, ftp:) or protocol-relative
# (//host/path) leaves the repository, so there is nothing here to resolve.
EXTERNAL_RE = re.compile(r'^([a-z][a-z0-9+.-]*:|//)', re.IGNORECASE)


def slugify(text):
    """Render a heading the way the mkdocs toc extension does."""
    return re.sub(r'\s+', '-', re.sub(r'[^\w\s-]', '', text.strip().lower()))


def anchors_in(path):
    """The set of anchors a markdown file defines."""
    found = set()
    with open(path, errors='replace') as f:
        for line in f:
            heading = HEADING_RE.match(line)
            if heading:
                title = heading.group(2)
                explicit = ATTR_ID_RE.search(title)
                if explicit:
                    # An explicit id replaces the generated slug, but record
                    # both: the slug remains a reasonable thing to link to if
                    # the attr_list extension is ever disabled.
                    found.add(explicit.group(1))
                    title = ATTR_ID_RE.sub('', title)
                found.add(slugify(title))

            # Definition-list style entries carry their id inline rather than
            # on a heading, which is how the glossary is written.
            for inline in ATTR_ID_RE.finditer(line):
                found.add(inline.group(1))
    return found


def repo_root_for(docs_dir):
    """The directory the root markdown files live in.

    Kept relative when docs_dir is, so a reported problem names
    `AGENTS.md` rather than an absolute path into somebody's checkout.
    """
    return os.path.dirname(docs_dir) or os.curdir


def markdown_files(docs_dir=DOCS_DIR, root_dir=None):
    if root_dir is None:
        root_dir = repo_root_for(docs_dir)
    for filename in ROOT_FILES:
        path = os.path.normpath(os.path.join(root_dir, filename))
        if os.path.isfile(path):
            yield path

    for dirpath, dirnames, filenames in os.walk(docs_dir):
        dirnames.sort()
        for filename in sorted(filenames):
            if not filename.endswith('.md'):
                continue
            path = os.path.join(dirpath, filename)
            if path.startswith(EXCLUDED_PREFIXES):
                continue
            yield path


def resolve_target(source, target_path, docs_dir=DOCS_DIR):
    """Map the file portion of a link to a path on disk, or None."""
    if not target_path:
        return source

    if target_path.startswith('/'):
        # Site-absolute, and written in mkdocs' directory-url form, so
        # `/developer_guide/state_machine/` is `state_machine.md`.
        candidate = os.path.join(docs_dir, target_path.strip('/'))
    else:
        candidate = os.path.normpath(
            os.path.join(os.path.dirname(source), target_path))

    for attempt in (candidate, candidate + '.md',
                    os.path.join(candidate, 'index.md')):
        if os.path.isfile(attempt):
            return attempt
    return None


def check_anchors(docs_dir=DOCS_DIR, root_dir=None):
    """Return a sorted list of human readable problem descriptions."""
    problems = []
    cache = {}

    for source in markdown_files(docs_dir, root_dir=root_dir):
        with open(source, errors='replace') as f:
            content = f.read()

        for match in LINK_RE.finditer(content):
            link = match.group(1)
            if EXTERNAL_RE.match(link):
                continue

            target_path, _, anchor = link.partition('#')
            # A bare '#' is a deliberate no-op link, and an anchor-only link
            # names the source file itself.
            if not target_path and not anchor:
                continue

            target = resolve_target(source, target_path, docs_dir=docs_dir)
            if not target:
                problems.append(
                    f'{source}: {link} -> no such file')
                continue

            if not anchor:
                continue

            if target not in cache:
                cache[target] = anchors_in(target)
            if anchor not in cache[target]:
                problems.append(
                    f'{source}: {link} -> {target} defines no anchor #{anchor}')

    return sorted(problems)


def main():
    problems = check_anchors()
    for problem in problems:
        print(problem)
    if problems:
        print(f'\n{len(problems)} broken documentation link(s).')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
