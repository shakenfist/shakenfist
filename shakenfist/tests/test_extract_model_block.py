# Copyright 2026 Michael Still and contributors

"""Tests for tools/extract-model-block.sh.

The issue-fix workflow asks Claude Code for two marker delimited blocks on
stdout -- a commit message and a pull request description -- and publishes
both. The commit message is pushed and its first line becomes the pull
request title, so a parsing mistake here is not cosmetic: it lands in git
history and in a description a human is meant to read before the diff.

The parsing looks trivial and is not. Each test below pins a shape that the
obvious sed implementation got wrong: a range match prints every occurrence
rather than the first, an unterminated block runs to the end of the captured
output and swallows the block after it, a repeated start marker ends up
embedded in the published prose, and a model which copies the fenced
illustration from the prompt gets its whole description rendered as one
preformatted lump. The one thing that must NOT be normalised is fenced code
inside a description, which is why fences are only stripped when they wrap
the entire block.
"""

import os
import subprocess
import tempfile

from shakenfist.tests import base


def _script_path():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, 'tools', 'extract-model-block.sh')


SCRIPT = _script_path()


class ExtractModelBlockTestCase(base.ShakenFistTestCase):
    def _extract(self, block, output):
        """Run the extractor over output, returning (exit code, extracted)."""
        with tempfile.TemporaryDirectory() as tempdir:
            source = os.path.join(tempdir, 'claude-output.txt')
            extracted = os.path.join(tempdir, 'block.txt')
            with open(source, 'w') as f:
                f.write(output)

            proc = subprocess.run(
                [SCRIPT, block, source, extracted],
                capture_output=True, text=True)

            # The output file is always created, so a caller uploading it as
            # a build artifact does not have to special case the failure.
            self.assertTrue(os.path.exists(extracted),
                            'output file was not created')
            with open(extracted) as f:
                return proc.returncode, f.read()

    def test_script_is_executable(self):
        # The workflow copies it and runs it directly rather than via bash.
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_simple_block(self):
        rc, got = self._extract('COMMIT_SUMMARY', (
            'chatter before\n'
            'COMMIT_SUMMARY_START\n'
            'Fix the thing.\n'
            '\n'
            'Body line.\n'
            'COMMIT_SUMMARY_END\n'
            'chatter after\n'))
        self.assertEqual(0, rc)
        self.assertEqual('Fix the thing.\n\nBody line.\n', got)

    def test_two_separate_fenced_blocks_keep_their_fences(self):
        # The wrapping-fence check must confirm the first and last lines
        # are the ONLY fences. Testing just those two lines deletes the
        # outer markers of two unrelated fenced blocks, which inverts
        # every fence after the first in the published body.
        body = (
            '```python\n'
            'x = 1\n'
            '```\n'
            '\n'
            'Some prose here.\n'
            '\n'
            '```python\n'
            'y = 2\n'
            '```\n')
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n' + body + 'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual(body, got)

    def test_an_odd_number_of_fences_is_not_stripped(self):
        # Ambiguous, so leave it alone rather than guess.
        body = (
            '```\n'
            'x = 1\n'
            '```\n'
            '\n'
            '```\n')
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n' + body + 'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual(body, got)

    def test_a_second_complete_block_is_ignored(self):
        # A range match prints every occurrence, so this used to publish the
        # interior marker lines verbatim.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            'First attempt.\n'
            'PR_DESCRIPTION_END\n'
            'PR_DESCRIPTION_START\n'
            'Second attempt.\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual('First attempt.\n', got)
        self.assertNotIn('PR_DESCRIPTION_END', got)
        self.assertNotIn('Second attempt.', got)

    def test_a_repeated_start_marker_restarts_the_block(self):
        # The model narrating what it is about to emit, or abandoning a first
        # attempt without closing it. Taking the first start marker instead
        # would leave the second one embedded in the published prose.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            'Abandoned attempt.\n'
            'PR_DESCRIPTION_START\n'
            'The real description.\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual('The real description.\n', got)
        self.assertNotIn('PR_DESCRIPTION_START', got)
        self.assertNotIn('Abandoned attempt.', got)

    def test_end_before_any_start_is_not_a_close(self):
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_END\n'
            'PR_DESCRIPTION_START\n'
            'The real description.\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual('The real description.\n', got)

    def test_whitespace_only_block_is_rejected(self):
        # As useless to a reader as an empty one, and the caller falls back
        # for both.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            '   \n'
            '\t\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(1, rc)
        self.assertEqual('', got)

    def test_unterminated_block_is_rejected(self):
        # Without this the range runs to end of file and the commit message
        # absorbs the pull request description which follows it.
        rc, got = self._extract('COMMIT_SUMMARY', (
            'COMMIT_SUMMARY_START\n'
            'Fix the thing.\n'
            '\n'
            'Body line.\n'
            'PR_DESCRIPTION_START\n'
            '## What was wrong\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(1, rc)
        self.assertEqual('', got)

    def test_missing_block_is_rejected(self):
        rc, got = self._extract('PR_DESCRIPTION', 'I have nothing to say.\n')
        self.assertEqual(1, rc)
        self.assertEqual('', got)

    def test_empty_block_is_rejected(self):
        # An empty block is a missing block: the caller must fall back rather
        # than publish a description consisting of nothing.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            '\n'
            '\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(1, rc)
        self.assertEqual('', got)

    def test_wrapping_fence_is_stripped(self):
        # The prompt illustrates both blocks inside fences while telling the
        # model not to use them. A model which copies the illustration would
        # otherwise have its whole description rendered as preformatted text.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            '```\n'
            '## What was wrong\n'
            '\n'
            'The root cause.\n'
            '```\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual('## What was wrong\n\nThe root cause.\n', got)

    def test_wrapping_fence_with_a_language_is_stripped(self):
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            '```markdown\n'
            '## What was wrong\n'
            '```\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual('## What was wrong\n', got)

    def test_interior_fenced_code_is_preserved(self):
        # The reason fences are not stripped globally: a description may
        # legitimately quote code, and eating those fences would mangle it.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            '## What changed\n'
            '\n'
            '```python\n'
            'x = 1\n'
            '```\n'
            '\n'
            'And that is all.\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual(
            '## What changed\n\n```python\nx = 1\n```\n\nAnd that is all.\n',
            got)

    def test_marker_named_in_prose_does_not_terminate(self):
        # This workflow is itself a plausible target for an automated fix,
        # and such a fix would want to name these tokens in its description.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            'The PR_DESCRIPTION_END marker needs a line to itself.\n'
            'Still here.\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual(
            'The PR_DESCRIPTION_END marker needs a line to itself.\n'
            'Still here.\n', got)

    def test_marker_beginning_a_line_of_prose_does_not_terminate(self):
        # Stricter than an anchored match: only a line which is nothing but
        # the marker terminates. A fix to this workflow would plausibly
        # start a sentence of its description with the token.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            'PR_DESCRIPTION_END is what terminates the block.\n'
            'This line must survive.\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual(
            'PR_DESCRIPTION_END is what terminates the block.\n'
            'This line must survive.\n', got)

    def test_start_marker_beginning_a_line_of_prose_does_not_restart(self):
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            'The prompt asks for a PR_DESCRIPTION_START block.\n'
            'That sentence is prose, not a marker.\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual(
            'The prompt asks for a PR_DESCRIPTION_START block.\n'
            'That sentence is prose, not a marker.\n', got)

    def test_indented_markers_are_matched(self):
        # Models indent things. Trailing whitespace on a marker line is the
        # same hazard and is handled by the same trim.
        rc, got = self._extract('COMMIT_SUMMARY', (
            '  COMMIT_SUMMARY_START  \n'
            'Fix the thing.\n'
            '\tCOMMIT_SUMMARY_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual('Fix the thing.\n', got)

    def test_leading_and_trailing_blank_lines_are_trimmed(self):
        rc, got = self._extract('COMMIT_SUMMARY', (
            'COMMIT_SUMMARY_START\n'
            '\n'
            'Fix the thing.\n'
            '\n'
            '\n'
            'COMMIT_SUMMARY_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual('Fix the thing.\n', got)

    def test_blocks_do_not_interfere(self):
        # Both blocks come out of one captured output, in either order.
        output = (
            'COMMIT_SUMMARY_START\n'
            'Fix the thing.\n'
            'COMMIT_SUMMARY_END\n'
            'PR_DESCRIPTION_START\n'
            '## What was wrong\n'
            'PR_DESCRIPTION_END\n')
        rc, summary = self._extract('COMMIT_SUMMARY', output)
        self.assertEqual(0, rc)
        self.assertEqual('Fix the thing.\n', summary)
        rc, description = self._extract('PR_DESCRIPTION', output)
        self.assertEqual(0, rc)
        self.assertEqual('## What was wrong\n', description)

    def test_shell_metacharacters_are_not_evaluated(self):
        # The extracted description reaches gh via --body-file, but the
        # extractor itself must not evaluate what it is copying either.
        rc, got = self._extract('PR_DESCRIPTION', (
            'PR_DESCRIPTION_START\n'
            'The fix was $(touch /tmp/PWNED) and `echo also`.\n'
            'PR_DESCRIPTION_END\n'))
        self.assertEqual(0, rc)
        self.assertEqual(
            'The fix was $(touch /tmp/PWNED) and `echo also`.\n', got)
        self.assertFalse(os.path.exists('/tmp/PWNED'))

    def test_missing_input_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            extracted = os.path.join(tempdir, 'block.txt')
            proc = subprocess.run(
                [SCRIPT, 'PR_DESCRIPTION',
                 os.path.join(tempdir, 'nope.txt'), extracted],
                capture_output=True, text=True)
            self.assertEqual(1, proc.returncode)
            self.assertTrue(os.path.exists(extracted))

    def test_wrong_argument_count_is_a_usage_error(self):
        proc = subprocess.run(
            [SCRIPT, 'PR_DESCRIPTION'], capture_output=True, text=True)
        self.assertEqual(2, proc.returncode)
        self.assertIn('usage:', proc.stderr)
