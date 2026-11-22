# Copyright 2026 Michael Still and contributors

"""Tests for how issue-fix.yml wires up its helper scripts.

`tools/extract-model-block.sh`, `tools/neutralise-pr-body.sh` and
`tools/measure-change-size.sh` have their own unit tests, but a tested script
the workflow does not call is not an extraction, a defence or a size cap.
These pin the seams between the two, and that .gitattributes still exempts
every generated protobuf stub from the cap.

The one worth explaining is the base commit read. The scripts are staged outside
the workspace before use, which looks like the copy of
`claude-model-fallback.sh` earlier in the same workflow and is there for the
opposite reason. That one is copied *before* Claude runs, because bash reads
a script lazily as it executes and an edit mid-run would corrupt it. These
are staged *after* Claude has exited, and the hazard is that the fix under
test may have edited them -- the extractor's own header calls that out as a
plausible subject for an automated fix. Copying from the workspace would
have the workflow parse the model's output with the untested version the
model just wrote. They are read from the commit recorded in "Create working
branch" rather than from HEAD: the prompt tells the model not to commit, but a
model which did would have moved HEAD onto its own edits.
"""

import fnmatch
import os

from shakenfist.tests import base


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _workflow_path():
    return os.path.join(_repo_root(), '.github', 'workflows', 'issue-fix.yml')


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

    def test_the_scripts_are_read_from_the_base_not_the_workspace(self):
        self.assertIn('git show "${BASE_SHA}:tools/${script}"', self.workflow)
        self.assertIn(
            'git show "${BASE_SHA}:tools/measure-change-size.sh"',
            self.workflow)
        self.assertNotIn(':tools/', self.workflow.replace('${BASE_SHA}:tools/', ''),
                         'a helper script is read from a commit other than '
                         'the recorded base')
        for script in ('extract-model-block.sh', 'neutralise-pr-body.sh',
                       'measure-change-size.sh'):
            self.assertNotIn(
                'cp tools/%s' % script, self.workflow,
                '%s is copied from the workspace, which is the tree the '
                'fix under test just edited. Read it from the base commit '
                'instead.' % script)

    def test_the_base_is_recorded_before_the_model_runs(self):
        recorded = self.workflow.index(
            'echo "base_sha=$(git rev-parse HEAD)" >> $GITHUB_OUTPUT')
        model = self.workflow.index(
            '- name: Run Claude Code to propose a fix')
        self.assertLess(recorded, model)
        self.assertEqual(
            self.workflow.count('${{ steps.branch.outputs.base_sha }}'), 2,
            'both the extraction and the measurement should read the '
            'recorded base')

    def test_the_size_cap_is_measured_with_the_tested_script(self):
        self.assertIn(
            '${{ runner.temp }}/measure-change-size.sh \\\n'
            '            "${BASE_SHA}" "${TEST_PATH_PATTERN}"',
            self.workflow,
            'the workflow no longer measures with the tested script. If '
            'the measurement was rewritten, rewrite '
            'test_measure_change_size.py against it rather than deleting '
            'it.')
        # The regression the script exists for: an inline "git diff | awk"
        # without pipefail measures a failing git as zero lines.
        self.assertNotIn('--numstat', self.workflow)

    def test_a_non_numeric_count_stops_the_step(self):
        # Belt and braces behind the script's own failure: the step must
        # not compare an empty or garbled count against the cap.
        self.assertIn("''|*[!0-9]*)", self.workflow)

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


class GeneratedFilesExemptionTestCase(base.ShakenFistTestCase):
    """The size cap exempts what .gitattributes marks linguist-generated.

    A stub family the patterns miss counts against the cap again, which is the
    1800 line fix this exemption was added for; a pattern which catches a
    hand-written file lets real code through unmeasured. Both are checked
    against the files actually in the tree, so a new family written by
    "tox -e genprotos" fails here rather than on the next automated fix.
    """

    def setUp(self):
        super().setUp()
        self.patterns = []
        with open(os.path.join(_repo_root(), '.gitattributes')) as f:
            for line in f:
                fields = line.split()
                if not fields or fields[0].startswith('#'):
                    continue
                # Either spelling is honoured by measure-change-size.sh.
                if ({'linguist-generated', 'linguist-generated=true'}
                        & set(fields[1:])):
                    self.patterns.append(fields[0])

    def _exempt(self, path):
        # No pattern uses "**", and fnmatch letting "*" cross a "/" can
        # only make a pattern look broader than git reads it.
        return any(fnmatch.fnmatchcase(path, p) for p in self.patterns)

    def test_every_generated_stub_is_exempt(self):
        protos = os.path.join(_repo_root(), 'shakenfist', 'protos')
        stubs = sorted(n for n in os.listdir(protos) if n != '__init__.py' and
                       os.path.isfile(os.path.join(protos, n)))
        self.assertNotEqual([], stubs)
        for name in stubs:
            path = 'shakenfist/protos/%s' % name
            self.assertTrue(
                self._exempt(path),
                '%s is not marked linguist-generated in .gitattributes, so '
                'regenerating it counts against the issue-fix size cap' % path)

    def test_the_generated_enums_proto_is_exempt(self):
        self.assertTrue(self._exempt('protos/shakenfist_enums.proto'))

    def test_hand_written_protos_are_counted(self):
        protos = os.path.join(_repo_root(), 'protos')
        for name in sorted(os.listdir(protos)):
            if name == 'shakenfist_enums.proto':
                continue
            path = 'protos/%s' % name
            self.assertFalse(
                self._exempt(path),
                '%s is hand-written but .gitattributes exempts it from the '
                'issue-fix size cap' % path)
