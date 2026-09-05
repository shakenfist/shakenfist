# Copyright 2026 Michael Still and contributors

"""Tests for how merge-failure-triage.yml wires up its helper scripts.

`tools/merge-triage.py` has its own unit tests, but a tested script the
workflow does not call is not an extraction and not a defence -- the same
argument test_issue_fix_workflow_seams.py makes. These pin the seams between
the workflow and the shell.

The one worth explaining is the staging. The model runs with
`--dangerously-skip-permissions` and the checkout as its working directory,
and the checkout holds a copy of every script this job executes. So the whole
set is copied into `runner.temp` before the model starts and run from there,
which closes two different holes at once: bash reads a script lazily as it
executes, so an edit to the running driver would corrupt it mid-run, and the
extractor and the neutraliser run *after* the model exits, so reading them
from the workspace would mean parsing and defusing model output with a copy
the model could have rewritten. `issue-fix.yml` stages the same set for the
same two reasons.

The other seams are cheap to get wrong and silent when wrong: an OUTPUT_DIR
which no longer matches the artifact paths uploads nothing (`if-no-files-found:
warn`, not `error`), and a permissions block which stops naming `actions: read`
403s on every piece of evidence the triage gathers, because naming any scope
sets every unnamed one to `none`.
"""

import os
import re

import yaml

from shakenfist.tests import base


# Everything tools/merge-ci-triage.sh reaches for through TOOLS_DIR, plus the
# script itself. Kept here rather than derived, so that adding a helper to the
# shell without staging it fails this test rather than failing in production
# with the workspace copy.
STAGED_TOOLS = [
    'merge-ci-triage.sh',
    'merge-triage.py',
    'merge-triage-schema.json',
    'claude-model-fallback.sh',
    'neutralise-pr-body.sh',
]

STAGE_DIR = '${{ runner.temp }}/merge-triage-tools'
OUTPUT_DIR = '${{ runner.temp }}/merge-triage'


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


class MergeTriageWorkflowSeamsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.root = _repo_root()
        path = os.path.join(self.root, '.github', 'workflows',
                            'merge-failure-triage.yml')
        with open(path) as f:
            self.text = f.read()
        self.workflow = yaml.safe_load(self.text)
        self.steps = self.workflow['jobs']['triage']['steps']

    def _step(self, name):
        for step in self.steps:
            if step.get('name') == name:
                return step
        self.fail('no step named %r in the triage job' % name)

    def test_every_tool_the_shell_uses_is_staged(self):
        stage = self._step('Stage the triage tools outside the workspace')
        self.assertEqual(STAGE_DIR, stage['env']['STAGE_DIR'])
        for tool in STAGED_TOOLS:
            self.assertTrue(
                os.path.exists(os.path.join(self.root, 'tools', tool)),
                'tools/%s does not exist, so staging it will fail the job' % tool)
            self.assertIn(
                'tools/%s' % tool, stage['run'],
                'tools/%s is used by the triage but is not staged out of the '
                'workspace, so the model could rewrite the copy that runs.'
                % tool)

    def test_the_staging_happens_before_the_model_runs(self):
        names = [step.get('name') for step in self.steps]
        self.assertLess(
            names.index('Stage the triage tools outside the workspace'),
            names.index('Triage the failure'),
            'staging after the triage step stages nothing useful: the model '
            'has already run by then.')

    def test_the_triage_runs_the_staged_driver_not_the_workspace_one(self):
        triage = self._step('Triage the failure')
        self.assertIn('%s/merge-ci-triage.sh' % STAGE_DIR, triage['run'])
        # The script finds its helpers beside itself, so running the workspace
        # copy would pull every one of them back out of the tree the model can
        # write to -- which is the whole point of the staging step.
        self.assertFalse(
            re.search(r'(^|\s)tools/merge-ci-triage\.sh', triage['run']),
            'the triage runs the workspace copy of the driver')

    def test_the_output_directory_is_what_the_artifact_uploads(self):
        triage = self._step('Triage the failure')
        self.assertEqual(OUTPUT_DIR, triage['env']['OUTPUT_DIR'])

        publish = self._step('Publish the verdict')
        paths = [p for p in publish['with']['path'].split('\n') if p.strip()]
        self.assertIn('%s/triage.json' % OUTPUT_DIR, paths,
                      'the verdict document is not uploaded, and '
                      'if-no-files-found is warn, so this fails silently.')
        # Only written when the document failed our own schema, which is a
        # bug in this tooling rather than a triage outcome. Leaving it behind
        # on the runner discards the only evidence of that bug.
        self.assertIn('%s/triage.invalid.json' % OUTPUT_DIR, paths,
                      'a document which failed its own schema is not '
                      'uploaded, so the bug that produced it is unreadable.')
        for path in paths:
            self.assertTrue(
                path.startswith(OUTPUT_DIR + '/'),
                '%s is not under OUTPUT_DIR, so nothing writes it' % path)

    def test_scratch_files_never_land_in_the_workspace(self):
        # runner.temp, not the checkout: the workspace is what the model is
        # pointed at, and the evidence and the prompt are inputs to the
        # verdict.
        self.assertTrue(OUTPUT_DIR.startswith('${{ runner.temp }}/'))
        self.assertTrue(STAGE_DIR.startswith('${{ runner.temp }}/'))

    def test_actions_read_is_declared(self):
        # Naming any scope in a permissions block sets every unnamed scope to
        # none, and every piece of evidence this workflow gathers -- the run,
        # its jobs, its failed step logs, the sibling merge group runs -- is an
        # Actions API read. Without this the job reads nothing and triages an
        # empty run.
        self.assertEqual('read', self.workflow['permissions'].get('actions'))
        self.assertEqual('write', self.workflow['permissions'].get('issues'))
        self.assertEqual('write', self.workflow['permissions'].get('pull-requests'))

    def test_the_comment_body_is_neutralised_by_the_shell(self):
        # The workflow does not post the comment itself, so the seam that
        # matters is in the driver: model prose reaches "gh pr comment" only
        # through neutralise-pr-body.sh, and only if that succeeded. The
        # driver runs without "set -e", so an unchecked call here fails open
        # -- it leaves the un-neutralised body exactly where the comment step
        # reads it from. test_merge_ci_triage_shell.py tests the behaviour by
        # running the driver with a neutraliser that fails; this pins the
        # ordering, which that test cannot see.
        with open(os.path.join(self.root, 'tools', 'merge-ci-triage.sh')) as f:
            script = f.read()
        # The invocations, not the prose about them: both are named in
        # comments above the code that runs them.
        neutralise = re.search(
            r'(?m)^if ! "\$\{TOOLS_DIR\}/neutralise-pr-body\.sh"', script)
        comment = re.search(r'(?m)^gh pr comment ', script)
        self.assertIsNotNone(
            neutralise,
            'the neutralisation result is discarded, so a failure inside it '
            'publishes the un-neutralised body.')
        self.assertIsNotNone(comment, 'the driver no longer posts a comment')
        self.assertLess(
            neutralise.start(), comment.start(),
            'the comment body is posted before it is neutralised, so an '
            '@mention or an issue-closing keyword fires on publication.')
