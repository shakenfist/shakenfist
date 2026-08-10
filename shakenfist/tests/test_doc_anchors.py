# Copyright 2026 Michael Still and contributors

"""Tests for tools/check-doc-anchors.py.

An intra-documentation anchor link whose target anchor does not exist fails
silently -- mkdocs does not warn, the link still renders, and the reader lands
at the top of the page. Two such links (`docs/glossary.md` linking `#network`
when the glossary had no network entry, and the artifacts API reference linking
`#uploads` when the state machine heading is `## Upload`) survived in tree for
exactly that reason. The checker closes the hole; these tests keep it honest,
and the final test is the regression guard for the docs themselves.
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

            self.assertEqual([], checker.check_anchors(docs_dir=tmp))

    def test_missing_anchor_in_same_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'glossary.md',
                        'matches no [network](#network).\n')

            problems = checker.check_anchors(docs_dir=tmp)
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

            problems = checker.check_anchors(docs_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('#uploads', problems[0])

    def test_missing_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'index.md', 'See [gone](gone.md#anchor).\n')

            problems = checker.check_anchors(docs_dir=tmp)
            self.assertEqual(1, len(problems))
            self.assertIn('no such file', problems[0])

    def test_external_and_unanchored_links_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'index.md',
                        'See [x](https://example.com/page#frag), '
                        '[y](http://example.com#frag), '
                        '[z](mailto:a@example.com#frag), '
                        '[w](other.md) and [v](#).\n')

            self.assertEqual([], checker.check_anchors(docs_dir=tmp))

    def test_plans_and_components_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The exclusions are relative to the real docs directory, so
            # exercise them there rather than in a tempdir.
            self.assertTrue(
                checker.EXCLUDED_PREFIXES[0].endswith(
                    os.path.join('docs', 'plans')))
            self.assertTrue(
                checker.EXCLUDED_PREFIXES[1].endswith(
                    os.path.join('docs', 'components')))

            self._write(tmp, 'index.md', 'ok\n')
            self.assertEqual([], checker.check_anchors(docs_dir=tmp))


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
