# Copyright 2026 Michael Still and contributors

"""Tests for tools/neutralise-pr-body.sh.

The issue-fix workflow publishes a model-authored pull request description
verbatim, and GitHub acts on two things it may find there. An @mention
notifies a real person the instant `gh pr create` runs -- before any human
has looked at the draft, and a notification cannot be taken back. An
issue-closing keyword closes an unrelated issue when the pull request
merges.

The prompt forbids both, and the prompt is not enough: a side effect which
fires automatically and is irreversible should not rest on the model having
complied. These tests pin the mechanical defence, and in particular pin
what it must NOT touch -- a description quoting a decorator, an email
address or a path is a normal description, and mangling it to defuse a
hazard that is not there is its own defect.
"""

import os
import subprocess
import tempfile

from shakenfist.tests import base


def _script_path():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, 'tools', 'neutralise-pr-body.sh')


SCRIPT = _script_path()


class NeutralisePrBodyTestCase(base.ShakenFistTestCase):
    def _neutralise(self, body):
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, 'pr-description.txt')
            with open(path, 'w') as f:
                f.write(body)

            proc = subprocess.run(
                [SCRIPT, path], capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, proc.stderr)

            with open(path) as f:
                return f.read()

    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_a_mention_loses_its_at_sign(self):
        # Which is what the prompt asks the model to do itself.
        self.assertEqual(
            'Thanks to someone for the diagnosis.\n',
            self._neutralise('Thanks to @someone for the diagnosis.\n'))

    def test_a_team_mention_loses_its_at_sign(self):
        self.assertEqual(
            'Raised by an-org/a-team.\n',
            self._neutralise('Raised by @an-org/a-team.\n'))

    def test_a_mention_at_the_start_of_a_line_is_caught(self):
        self.assertEqual(
            'someone asked for this.\n',
            self._neutralise('@someone asked for this.\n'))

    def test_an_email_address_is_untouched(self):
        # The character before the @ is what distinguishes the two.
        self.assertEqual(
            'Reported by foo@example.com in passing.\n',
            self._neutralise('Reported by foo@example.com in passing.\n'))

    def test_a_closing_keyword_is_separated_from_its_reference(self):
        # "Fixes issue #12" is a citation; GitHub only closes when the
        # reference immediately follows the keyword.
        self.assertEqual(
            'Fixes issue #12 as a side effect.\n',
            self._neutralise('Fixes #12 as a side effect.\n'))

    def test_every_closing_keyword_inflection_is_caught(self):
        for keyword in ('Fix', 'Fixes', 'Fixed', 'Close', 'Closes',
                        'Closed', 'Resolve', 'Resolves', 'Resolved',
                        'fix', 'fixes', 'closes', 'resolved'):
            self.assertEqual(
                '%s issue #12\n' % keyword,
                self._neutralise('%s #12\n' % keyword),
                'keyword %s was not defused' % keyword)

    def test_a_cross_repository_reference_keeps_its_repository(self):
        self.assertEqual(
            'Closes issue shakenfist/other#34.\n',
            self._neutralise('Closes shakenfist/other#34.\n'))

    def test_a_url_reference_is_defused(self):
        self.assertEqual(
            'This resolves issue https://github.com/o/r/issues/9 too.\n',
            self._neutralise(
                'This resolves https://github.com/o/r/issues/9 too.\n'))

    def test_a_keyword_not_followed_by_a_reference_is_prose(self):
        # Rewriting this would mangle ordinary English.
        self.assertEqual(
            'It fixes a typo, and closes the gap in coverage.\n',
            self._neutralise(
                'It fixes a typo, and closes the gap in coverage.\n'))

    def test_a_bare_issue_reference_still_links(self):
        # A citation without a keyword does not close anything, and the
        # link is useful.
        self.assertEqual(
            'See #77 for the original report.\n',
            self._neutralise('See #77 for the original report.\n'))

    def test_fenced_code_is_untouched(self):
        # GitHub does not linkify inside a fence, so there is nothing to
        # defuse, and a quoted decorator must survive intact.
        body = (
            'Before.\n'
            '\n'
            '```python\n'
            '@property\n'
            'def x(self):  # Fixes #99\n'
            '    pass\n'
            '```\n'
            '\n'
            'After.\n')
        self.assertEqual(body, self._neutralise(body))

    def test_text_after_a_fence_closes_is_defused_again(self):
        # The toggle has to survive the block, or everything after the
        # first fenced example goes unprotected.
        self.assertEqual(
            '```\n@property\n```\nThanks someone.\n',
            self._neutralise('```\n@property\n```\nThanks @someone.\n'))

    def test_several_hazards_on_one_line(self):
        self.assertEqual(
            'Thanks someone and someoneelse; fixes issue #1 and '
            'closes issue #2.\n',
            self._neutralise(
                'Thanks @someone and @someoneelse; fixes #1 and '
                'closes #2.\n'))

    def test_an_ordinary_description_is_returned_unchanged(self):
        body = (
            '## What was wrong\n'
            '\n'
            'The extraction used a sed address range, which re-matches.\n'
            '\n'
            '## What I did not do\n'
            '\n'
            'The publish step is still untested shell.\n')
        self.assertEqual(body, self._neutralise(body))

    def test_a_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            proc = subprocess.run(
                [SCRIPT, os.path.join(tempdir, 'nope.txt')],
                capture_output=True, text=True)
            self.assertEqual(1, proc.returncode)

    def test_wrong_argument_count_is_a_usage_error(self):
        proc = subprocess.run([SCRIPT], capture_output=True, text=True)
        self.assertEqual(2, proc.returncode)
        self.assertIn('usage:', proc.stderr)
