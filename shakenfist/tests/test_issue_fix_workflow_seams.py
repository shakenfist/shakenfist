# Copyright 2026 Michael Still and contributors

"""Tests for how issue-fix.yml wires up its helper scripts.

`tools/extract-model-block.sh` and `tools/neutralise-pr-body.sh` have their
own unit tests, but a tested script the workflow does not call is not an
extraction and not a defence. These pin the seams between the two.

The one worth explaining is the HEAD read. Both scripts are staged outside
the workspace before use, which looks like the copy of
`claude-model-fallback.sh` earlier in the same workflow and is there for the
opposite reason. That one is copied *before* Claude runs, because bash reads
a script lazily as it executes and an edit mid-run would corrupt it. These
are staged *after* Claude has exited, and the hazard is that the fix under
test may have edited them -- the extractor's own header calls that out as a
plausible subject for an automated fix. Copying from the workspace would
have the workflow parse the model's output with the untested version the
model just wrote. Nothing is committed until the "Commit changes" step, so
HEAD is still the pre-fix tree.
"""

import os

from shakenfist.tests import base


def _workflow_path():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, '.github', 'workflows', 'issue-fix.yml')


class IssueFixWorkflowSeamsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        with open(_workflow_path()) as f:
            self.workflow = f.read()

    def test_both_blocks_are_extracted_with_the_tested_script(self):
        for block in ('COMMIT_SUMMARY', 'PR_DESCRIPTION'):
            self.assertIn(
                'extract-model-block.sh %s' % block, self.workflow,
                'the workflow no longer extracts %s with the tested '
                'script. If the extraction was rewritten, rewrite '
                'test_extract_model_block.py against it rather than '
                'deleting it.' % block)

    def test_the_description_is_neutralised(self):
        self.assertIn(
            'neutralise-pr-body.sh \\\n              '
            '${{ runner.temp }}/pr-description.txt', self.workflow)

    def test_the_commit_summary_is_not_neutralised(self):
        # A commit message is not published anywhere GitHub acts on a
        # mention, and the workflow appends its own "Fixes #NNNN" to it
        # deliberately.
        self.assertNotIn(
            'neutralise-pr-body.sh ${{ runner.temp }}/commit-summary.txt',
            self.workflow)

    def test_the_scripts_are_read_from_head_not_the_workspace(self):
        self.assertIn('git show "HEAD:tools/${script}"', self.workflow)
        for script in ('extract-model-block.sh', 'neutralise-pr-body.sh'):
            self.assertNotIn(
                'cp tools/%s' % script, self.workflow,
                '%s is copied from the workspace, which is the tree the '
                'fix under test just edited. Read it from HEAD instead.'
                % script)

    def test_the_issue_reference_precedes_the_model_prose(self):
        # An unbalanced fence in the description renders everything after
        # it as preformatted text, and GitHub does not autolink #NNNN
        # inside a code block -- a trailing reference would silently stop
        # closing the issue.
        # Scope to the brace group which assembles the body, and to
        # nothing else: the commit message step also emits a "Fixes"
        # line, and a window which caught that one would pass whatever
        # order the publish step used.
        end = self.workflow.index('} > ${{ runner.temp }}/pr-body.md')
        start = self.workflow.rindex('\n          {\n', 0, end)
        body = self.workflow[start:end]

        self.assertIn('cat ${{ runner.temp }}/pr-description.txt', body)
        reference = body.index('echo "Fixes #${ISSUE_NUMBER}"')
        description = body.index('cat ${{ runner.temp }}/pr-description.txt')
        self.assertLess(
            reference, description,
            'the appended "Fixes #NNNN" must come before the model '
            'description, not after it')
