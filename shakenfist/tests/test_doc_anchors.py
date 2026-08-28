# Copyright 2026 Michael Still and contributors

"""Tests for tools/check-doc-anchors.py.

An intra-documentation anchor link whose target anchor does not exist fails
silently -- mkdocs does not warn, the link still renders, and the reader lands
at the top of the page. Two such links (`docs/glossary.md` linking `#network`
when the glossary had no network entry, and the artifacts API reference linking
`#uploads` when the state machine heading is `## Upload`) survived in tree for
exactly that reason. The checker closes the hole; these tests keep it honest,
and the final test is the regression guard for the docs themselves.

The checker also covers the root markdown files and links with no anchor at
all. AGENTS.md and ARCHITECTURE.md are indexes into `docs/` rather than
documents in their own right, so their links are the ones a heading rename is
most likely to break -- and they are not part of the mkdocs site, so nothing
else validates them.
"""

import importlib.util
import os
import tempfile

from shakenfist.tests import base


def _load_checker():
    # The checker is a standalone script rather than an importable module,
    # because it is also a pre-commit hook entry point.
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'tools', 'check-doc-anchors.py')
    spec = importlib.util.spec_from_file_location('check_doc_anchors', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO_ROOT = root
    return module


checker = _load_checker()


class DocAnchorHelperTestCase(base.ShakenFistTestCase):
    def test_slugify_matches_mkdocs(self):
        self.assertEqual('upload', checker.slugify('Upload'))
        self.assertEqual('the-nonce', checker.slugify('The nonce'))
        self.assertEqual(
            'stray-vxlan-reaping', checker.slugify('Stray vxlan reaping'))
        # Punctuation is dropped, not replaced with a separator.
        self.assertEqual(
            'whats-a-blob', checker.slugify("What's a blob?"))

    def test_anchors_from_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'page.md')
            with open(path, 'w') as f:
                f.write('# Page title\n\n## Upload\n\nSome words.\n')

            self.assertEqual(
                {'page-title', 'upload'}, checker.anchors_in(path))

    def test_anchors_from_explicit_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'page.md')
            with open(path, 'w') as f:
                f.write('## A heading{#custom-id}\n\n'
                        '**glossary entry**{#glossary-entry} -- text.\n')

            found = checker.anchors_in(path)
            self.assertIn('custom-id', found)
            self.assertIn('glossary-entry', found)
            # The generated slug is recorded too, without the id attribute
            # leaking into it.
            self.assertIn('a-heading', found)

    def test_resolve_target_same_file(self):
        self.assertEqual(
            'docs/glossary.md',
            checker.resolve_target('docs/glossary.md', ''))

    def test_resolve_target_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'guide'))
            target = os.path.join(tmp, 'guide', 'other.md')
            open(target, 'w').close()
            source = os.path.join(tmp, 'index.md')

            self.assertEqual(
                target,
                checker.resolve_target(source, 'guide/other.md', docs_dir=tmp))

    def test_resolve_target_site_absolute_directory_url(self):
        # mkdocs' use_directory_urls form: /a/b/ is docs/a/b.md.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, 'developer_guide'))
            target = os.path.join(tmp, 'developer_guide', 'state_machine.md')
            open(target, 'w').close()

            self.assertEqual(
                target,
                checker.resolve_target(
                    os.path.join(tmp, 'index.md'),
                    '/developer_guide/state_machine/', docs_dir=tmp))

    def test_resolve_target_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                checker.resolve_target(
                    os.path.join(tmp, 'index.md'), 'nope.md', docs_dir=tmp))


class DocAnchorCheckTestCase(base.ShakenFistTestCase):
    def _write(self, tmp, name, content):
        path = os.path.join(tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_valid_anchors_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'glossary.md',
                        '**stray vxlan**{#stray-vxlan} -- matches no '
                        '[network](#network).\n\n'
                        '**network**{#network} -- a virtual network.\n')
            self._write(tmp, 'developer_guide/state_machine.md', '## Upload\n')
            self._write(tmp, 'other.md',
                        'See [uploads](/developer_guide/state_machine/'
                        '#upload).\n')

            self.assertEqual([], checker.check_anchors(docs_dir=tmp, root_dir=tmp))

    def test_missing_anchor_in_same_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'glossary.md',
                        'matches no [network](#network).\n')

            problems = checker.check_anchors(docs_dir=tmp, root_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('#network', problems[0])
            self.assertIn('glossary.md', problems[0])

    def test_missing_anchor_in_other_file_is_reported(self):
        # The exact shape of the artifacts.md regression: plural anchor,
        # singular heading.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'developer_guide/state_machine.md', '## Upload\n')
            self._write(tmp, 'developer_guide/api_reference/artifacts.md',
                        'See [object states](/developer_guide/state_machine/'
                        '#uploads).\n')

            problems = checker.check_anchors(docs_dir=tmp, root_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('#uploads', problems[0])

    def test_missing_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'index.md', 'See [gone](gone.md#anchor).\n')

            problems = checker.check_anchors(docs_dir=tmp, root_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('no such file', problems[0])

    def test_external_and_unanchored_links_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'other.md', 'A page.\n')
            self._write(tmp, 'index.md',
                        'See [x](https://example.com/page#frag), '
                        '[y](http://example.com#frag), '
                        '[z](mailto:a@example.com#frag), '
                        '[w](other.md) and [v](#).\n')

            self.assertEqual([], checker.check_anchors(docs_dir=tmp, root_dir=tmp))

    def test_other_schemes_and_protocol_relative_links_ignored(self):
        # Now that a missing target file is a problem rather than a silent
        # skip, anything addressed by scheme has to be recognised as leaving
        # the repository -- otherwise '//example.com/x' resolves against
        # docs/ and is reported as a missing page.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'index.md',
                        'See [a](//example.com/page), '
                        '[b](ftp://example.com/x.md), '
                        '[c](HTTPS://example.com/y#frag) and '
                        '[d](irc://example.com/chan).\n')

            self.assertEqual(
                [], checker.check_anchors(docs_dir=tmp, root_dir=tmp))

    def test_missing_file_without_anchor_is_reported(self):
        # The anchor-less case used to pass silently: check_anchors() only
        # looked at links containing a '#', so a link to a page which had
        # been deleted or moved was invisible to it.
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'index.md', 'See [gone](gone.md).\n')

            problems = checker.check_anchors(docs_dir=tmp, root_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('no such file', problems[0])
            self.assertIn('gone.md', problems[0])

    def test_root_files_are_checked(self):
        # AGENTS.md and ARCHITECTURE.md are indexes into docs/ and are not
        # part of the mkdocs site, so nothing else validates their links.
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, 'docs')
            self._write(docs, 'developer_guide/standards.md', '## Code style\n')
            self._write(
                tmp, 'AGENTS.md',
                'See [style](docs/developer_guide/standards.md#code-style), '
                '[gone](docs/developer_guide/nope.md) and '
                '[renamed](docs/developer_guide/standards.md#old-name).\n')

            problems = checker.check_anchors(docs_dir=docs, root_dir=tmp)
            self.assertEqual(2, len(problems))
            self.assertTrue(
                all(os.path.basename(p.split(':')[0]) == 'AGENTS.md'
                    for p in problems), problems)
            self.assertIn('no such file', problems[0])
            self.assertIn('defines no anchor #old-name', problems[1])

    def test_root_files_default_to_the_parent_of_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, 'docs')
            self._write(docs, 'index.md', '# Index\n')
            self._write(tmp, 'README.md', 'See [gone](docs/gone.md).\n')

            problems = checker.check_anchors(docs_dir=docs)
            self.assertEqual(1, len(problems))
            self.assertIn('README.md', problems[0])

    def test_absent_root_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, 'docs')
            self._write(docs, 'index.md', '# Index\n')

            sources = list(checker.markdown_files(docs, root_dir=tmp))
            self.assertEqual([os.path.join(docs, 'index.md')], sources)

    def test_plans_and_components_excluded_from_anchor_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'index.md', '# Index\n')
            self._write(tmp, 'plans/plan.md',
                        'See [idx](../index.md#missing-anchor).\n')
            self._write(tmp, 'components/thing.md',
                        'See [gone](../gone.md#nope).\n')

            self.assertEqual([], checker.check_anchors(docs_dir=tmp, root_dir=tmp))

    def test_link_out_of_docs_is_reported(self):
        # A relative link to a file which exists elsewhere in the repository
        # renders fine locally but breaks when the docs site imports docs/,
        # so it must be an absolute URL (the docs-external-links audit).
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, 'docs')
            self._write(tmp, 'shakenfist/code.py', '# code\n')
            self._write(docs, 'guide.md',
                        'See [code](../shakenfist/code.py).\n')

            problems = checker.check_anchors(docs_dir=docs, root_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('../shakenfist/code.py', problems[0])
            self.assertIn('absolute', problems[0])

    def test_plan_link_out_of_docs_is_reported(self):
        # The issue-3792 shape: a plan referencing code by a repo-root
        # relative path, which resolves to nothing inside docs/. Plans skip
        # anchor checking but still get the escaping-link check; an absolute
        # https URL for the same target is fine.
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, 'docs')
            self._write(docs, 'index.md', '# Index\n')
            self._write(docs, 'plans/plan.md',
                        'See [code](shakenfist/instance.py#L344), '
                        '[abs](https://github.com/shakenfist/shakenfist/'
                        'blob/develop/shakenfist/instance.py#L344) and '
                        '[idx](../index.md#missing-anchor).\n')

            problems = checker.check_anchors(docs_dir=docs, root_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('shakenfist/instance.py#L344', problems[0])
            self.assertIn('absolute', problems[0])

    def test_components_not_scanned_for_escaping_links(self):
        # Components are synchronised in from other repositories, so a fix
        # made here would be overwritten by the next synchronisation.
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, 'docs')
            self._write(docs, 'index.md', '# Index\n')
            self._write(docs, 'components/instar/bench.md',
                        'See [tests](../tests/test_bench.py).\n')

            self.assertEqual([], checker.check_anchors(docs_dir=docs, root_dir=tmp))

    def test_root_files_may_link_outside_docs(self):
        # The root markdown files are not part of the imported docs tree, so
        # the absolute-URL rule does not apply to them.
        with tempfile.TemporaryDirectory() as tmp:
            docs = os.path.join(tmp, 'docs')
            self._write(docs, 'index.md', '# Index\n')
            self._write(tmp, 'GOALS.md', '# Goals\n')
            self._write(tmp, 'README.md', 'See [goals](GOALS.md).\n')

            self.assertEqual([], checker.check_anchors(docs_dir=docs, root_dir=tmp))


class ShippedDocsTestCase(base.ShakenFistTestCase):
    def test_shipped_documentation_anchors_resolve(self):
        docs = os.path.join(checker.REPO_ROOT, 'docs')
        if not os.path.isdir(docs):
            self.skipTest('documentation tree is not present')

        cwd = os.getcwd()
        os.chdir(checker.REPO_ROOT)
        self.addCleanup(os.chdir, cwd)

        problems = checker.check_anchors()
        self.assertEqual(
            [], problems,
            'Broken documentation anchor links:\n' + '\n'.join(problems))

    def test_glossary_defines_the_terms_it_links(self):
        # The glossary is the page most likely to link its own entries, and
        # the #network breakage lived there. Assert the entries the stray
        # vxlan definition depends on exist by name, so a rename is loud.
        path = os.path.join(checker.REPO_ROOT, 'docs', 'glossary.md')
        if not os.path.isfile(path):
            self.skipTest('documentation tree is not present')

        found = checker.anchors_in(path)
        for anchor in ('network', 'node', 'namespace', 'stray-vxlan'):
            self.assertIn(anchor, found)
