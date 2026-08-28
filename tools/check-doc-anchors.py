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

`docs/plans/` and `docs/components/` are excluded as anchor-check sources:
plans are point-in-time records which are not maintained after they land, and
components are synchronised in from other repositories. Both remain valid link
targets.

Separately, every page under `docs/` other than `docs/components/` -- plans
included -- must not link out of `docs/` with a relative path. The docs site
imports this tree, so a relative link whose target is elsewhere in the
repository (or a repo-root-relative path which resolves nowhere at all) breaks
on import; such links must be absolute `https://github.com/...` URLs instead
(the docs-external-links consistency audit, issue 3792). Components stay
excluded because a fix made here would be overwritten by the next
synchronisation.
"""

import os
import re
import sys


DOCS_DIR = 'docs'
ANCHOR_EXCLUDED_DIRS = ('plans', 'components')
ESCAPE_ONLY_DIRS = ('plans',)

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


def _docs_markdown(docs_dir, subdirs=None, excluded_dirs=()):
    """Markdown files under docs_dir, or under the named subdirs of it."""
    roots = [docs_dir]
    if subdirs is not None:
        roots = [os.path.join(docs_dir, subdir) for subdir in subdirs]
    excluded = tuple(
        os.path.join(os.path.normpath(docs_dir), d) + os.sep
        for d in excluded_dirs)

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for filename in sorted(filenames):
                if not filename.endswith('.md'):
                    continue
                path = os.path.join(dirpath, filename)
                if os.path.normpath(path).startswith(excluded):
                    continue
                yield path


def markdown_files(docs_dir=DOCS_DIR, root_dir=None):
    if root_dir is None:
        root_dir = repo_root_for(docs_dir)
    for filename in ROOT_FILES:
        path = os.path.normpath(os.path.join(root_dir, filename))
        if os.path.isfile(path):
            yield path

    yield from _docs_markdown(docs_dir, excluded_dirs=ANCHOR_EXCLUDED_DIRS)


def escape_only_files(docs_dir=DOCS_DIR):
    """Markdown checked only for links which do not stay inside docs/.

    Plans are excluded from anchor checking because they are point-in-time
    records, but a relative link out of docs/ still breaks the docs site
    import there, so they get the escaping-link check and nothing else.
    """
    yield from _docs_markdown(docs_dir, subdirs=ESCAPE_ONLY_DIRS)


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

    docs_prefix = os.path.normpath(docs_dir) + os.sep
    sources = [(path, True) for path in markdown_files(docs_dir, root_dir=root_dir)]
    sources += [(path, False) for path in escape_only_files(docs_dir)]

    for source, anchors_checked in sources:
        with open(source, errors='replace') as f:
            content = f.read()

        in_docs = os.path.normpath(source).startswith(docs_prefix)

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

            if in_docs and target_path:
                # The docs site imports this tree, so a relative link whose
                # target is not a file inside docs/ breaks on import: either
                # it resolves to a file elsewhere in the repository, or it is
                # a repo-root-relative path which resolves nowhere at all.
                # For anchor-checked sources the latter case falls through to
                # the more precise 'no such file' report below.
                escapes = (target is not None and
                           not os.path.normpath(target).startswith(docs_prefix))
                if escapes or (target is None and not anchors_checked):
                    problems.append(
                        f'{source}: {link} -> does not resolve to a file '
                        'inside docs/; links out of docs/ must be absolute '
                        'https://github.com/... URLs')
                    continue

            if not anchors_checked:
                continue

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
