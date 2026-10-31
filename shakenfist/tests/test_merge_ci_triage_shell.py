# Copyright 2026 Michael Still and contributors

"""Tests for tools/merge-ci-triage.sh, the merge failure triage driver.

tools/merge-triage.py is what builds the verdict document, and it has its own
unit tests. The driver is what makes the document *true*: it is the half that
checks the model's claim to have recorded an occurrence, that forces the
document to say nothing was written when nothing was written, that decides
which part of a multi-megabyte log a model gets to see, and that decides
whether the comment is posted at all. docs/developer_guide/ci.md tells a
consumer to trust exactly those properties, so they are tested here rather
than read off the source.

The driver is run for real, with `gh` replaced by a stub on PATH and the
model wrapper replaced by one that emits a canned response. That is the only
arrangement in which the interesting cases can be reached at all: an issue
which cannot be read, a run GitHub gave no URL for, a neutralisation which
fails. Everything the script executes is staged into the temporary directory
first, exactly as the workflow stages it, so a test can substitute one helper
without touching the tree.

Needs jq and bash, which both runner flavours install as base packages.
"""

import json
import os
import shutil
import subprocess
import tempfile

from shakenfist.tests import base


REPO = 'shakenfist/shakenfist'
RUN_ID = '33952027147'
PR_NUMBER = 4080
RUN_URL = 'https://github.com/%s/actions/runs/%s' % (REPO, RUN_ID)

# The tools the driver reaches for beside itself. The workflow stages the same
# list; test_merge_triage_workflow_seams.py is what keeps the two in step.
STAGED_TOOLS = [
    'merge-ci-triage.sh',
    'merge-triage.py',
    'merge-triage-schema.json',
    'neutralise-pr-body.sh',
]

RUN_JSON = {
    'databaseId': int(RUN_ID),
    'event': 'merge_group',
    'conclusion': 'failure',
    'headBranch': 'gh-readonly-queue/develop/pr-%d-abcdef0' % PR_NUMBER,
    'headSha': 'abcdef0',
    'url': RUN_URL,
    'attempt': 1,
    'workflowName': 'Functional tests',
    'createdAt': '2026-09-05T00:00:00Z'
}

FAILED_JOBS = [{
    'job': 'develop debian 12 cluster (collection)',
    'url': '%s/job/1' % RUN_URL,
    'failed_steps': ['Build the smoke cluster']
}]

VERDICT = {
    'verdict': 'systemic',
    'confidence': 'high',
    'summary': 'The cluster build failed before any test ran.',
    'failing_job': 'develop debian 12 cluster (collection)',
    'failing_step': 'Build the smoke cluster',
    'failure_signature': '507 sufficient_idle_cpu',
    'recommendation': 'requeue',
    'tracking_issue': 3772,
    'tracking_issue_action': 'commented',
    'evidence': ['Three sibling merge groups failed the same way.']
}

# What "gh run view --json jobs" returns for a run the private-ci conductor
# cut short: the job it saw fail, the aggregator that always fails after a
# cancel, and the jobs that were still running and concluded cancelled.
CUT_SHORT_JOB = 'Sanity checks'
CUT_SHORT_JOBS = {'jobs': [
    {'name': CUT_SHORT_JOB, 'conclusion': 'failure',
     'url': '%s/job/1' % RUN_URL,
     'steps': [{'name': 'Run flake8', 'conclusion': 'failure'}]},
    {'name': 'develop debian 12 cluster (collection)', 'conclusion': 'cancelled',
     'url': '%s/job/2' % RUN_URL,
     'steps': [{'name': 'Build the smoke cluster', 'conclusion': 'cancelled'}]},
    {'name': 'Can merge', 'conclusion': 'failure',
     'url': '%s/job/3' % RUN_URL,
     'steps': [{'name': 'Check every job passed', 'conclusion': 'failure'}]},
]}

# A stub gh. Dispatches on the same argument shapes the driver uses and reads
# its answers out of a fixture directory, so a test says what GitHub knows by
# writing files. Every invocation is appended to gh.log, which is how the
# tests that care about *which* API was called assert it.
#
# "run view --json jobs" is asked two ways. Without --jq it is the cut short
# check reading the raw jobs, answered from jobs.json. With --jq it is the
# evidence gathering, answered by running the driver's own filter over
# jobs.json through jq when that fixture exists -- so a test of which jobs are
# selected tests the real filter -- and otherwise from failed-jobs.json, which
# holds what the filter would already have returned.
GH_STUB = '''#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys

args = sys.argv[1:]
fixtures = os.environ['GH_FIXTURES']

with open(os.path.join(fixtures, 'gh.log'), 'a') as f:
    f.write(' '.join(args) + '\\n')


def emit(name, missing_is_failure=True):
    path = os.path.join(fixtures, name)
    if not os.path.exists(path):
        if missing_is_failure:
            # gh prints the error body of a 404 on stdout, not stderr, which
            # is why the driver tests the exit status rather than the output.
            sys.stdout.write('{"message": "Not Found"}\\n')
            return 1
        return 0
    with open(path) as f:
        sys.stdout.write(f.read())
    return 0


if args[:2] == ['run', 'view']:
    if '--log-failed' in args:
        sys.exit(emit('failed-logs.txt', missing_is_failure=False))
    if 'jobs' in args:
        if '--jq' not in args:
            sys.exit(emit('jobs.json'))
        if os.path.exists(os.path.join(fixtures, 'jobs.json')):
            sys.exit(subprocess.run(
                ['jq', args[args.index('--jq') + 1],
                 os.path.join(fixtures, 'jobs.json')]).returncode)
        sys.exit(emit('failed-jobs.json'))
    sys.exit(emit('run.json'))

if args[:2] == ['run', 'list']:
    sys.exit(emit('sibling-runs.json'))

if args[:2] == ['pr', 'view']:
    sys.exit(emit('pr.json'))

if args[:2] == ['pr', 'comment']:
    if os.path.exists(os.path.join(fixtures, 'comment-fails')):
        sys.stderr.write('stub gh: could not post the comment\\n')
        sys.exit(1)
    body = args[args.index('--body-file') + 1]
    shutil.copy(body, os.path.join(fixtures, 'posted-comment.md'))
    sys.stdout.write('https://github.com/%s/pull/%s#issuecomment-1\\n'
                     % (os.environ['REPO'], args[2]))
    sys.exit(0)

if args[:2] == ['issue', 'view']:
    sys.exit(emit('issue-%s.body' % args[2]))

if args[0] == 'api':
    path = [a for a in args if '/issues/' in a][0]
    number = path.split('/issues/')[1].split('/')[0]
    sys.exit(emit('issue-%s.comments' % number, missing_is_failure=False))

sys.stderr.write('stub gh: unexpected invocation: %s\\n' % ' '.join(args))
sys.exit(3)
'''

# A stub model wrapper. The driver hands it the prompt and reads its stdout,
# so a test says what the model answered by writing CLAUDE_RESPONSE. Its
# argument vector is recorded, because how the prompt gets to the model is
# itself a property under test: as a file, never as an argument.
CLAUDE_STUB = '''#!/bin/bash
printf '%s\n' "$@" > "${GH_FIXTURES}/claude-args"
cat "${CLAUDE_RESPONSE}"
'''

# A model wrapper that could not run a model at all -- every model out of
# subscription credit is its documented exit 1, and an exec that never
# happened looks the same from here.
BROKEN_CLAUDE_STUB = '''#!/bin/bash
echo "claude-model-fallback: every model in is out of credit" >&2
exit 1
'''

# A neutraliser which fails, for the test that publication is gated on it.
BROKEN_NEUTRALISER = '''#!/bin/bash
echo "stub neutraliser: could not rewrite $1" >&2
exit 1
'''


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


class MergeCiTriageShellTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.root = _repo_root()
        # A missing jq means every jq read in the driver returns nothing and
        # the assertions below fail in a way that says nothing useful, so say
        # it here instead.
        self.assertIsNotNone(
            shutil.which('jq'),
            'these tests run the triage driver, which needs jq; both runner '
            'flavours install it as a base package')

        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)

        self.tools = os.path.join(self.tempdir, 'tools')
        self.bin = os.path.join(self.tempdir, 'bin')
        self.fixtures = os.path.join(self.tempdir, 'fixtures')
        self.output = os.path.join(self.tempdir, 'output')
        for path in [self.tools, self.bin, self.fixtures, self.output]:
            os.makedirs(path)

        for tool in STAGED_TOOLS:
            shutil.copy(os.path.join(self.root, 'tools', tool), self.tools)
        self._write_executable(
            os.path.join(self.tools, 'claude-model-fallback.sh'), CLAUDE_STUB)
        self._write_executable(os.path.join(self.bin, 'gh'), GH_STUB)

        # The defaults, which individual tests overwrite. A run that can be
        # read, one failed job, a short log and a pull request.
        self.write_fixture('run.json', json.dumps(RUN_JSON))
        self.write_fixture('failed-jobs.json', json.dumps(FAILED_JOBS))
        self.write_fixture('failed-logs.txt', 'TASK [build the cluster]\nfatal: 507\n')
        self.write_fixture('sibling-runs.json', json.dumps([]))
        self.write_fixture('pr.json', json.dumps(
            {'number': PR_NUMBER, 'title': 'A pull request', 'files': [], 'body': ''}))
        self.response = VERDICT

    def _write_executable(self, path, content):
        with open(path, 'w') as f:
            f.write(content)
        os.chmod(path, 0o755)

    def write_fixture(self, name, content):
        with open(os.path.join(self.fixtures, name), 'w') as f:
            f.write(content)

    def read_fixture(self, name):
        path = os.path.join(self.fixtures, name)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read()

    def run_triage(self, dry_run=False, env=None, response=None):
        """Run the driver, returning (exit code, document or None, stderr)."""
        response_path = os.path.join(self.tempdir, 'response.txt')
        with open(response_path, 'w') as f:
            if response is None:
                f.write('```json\n%s\n```\n' % json.dumps(self.response))
            else:
                f.write(response)

        environment = dict(os.environ)
        environment.update({
            'PATH': '%s:%s' % (self.bin, os.environ['PATH']),
            'REPO': REPO,
            'OUTPUT_DIR': self.output,
            'DRY_RUN': 'true' if dry_run else 'false',
            'GH_FIXTURES': self.fixtures,
            'CLAUDE_RESPONSE': response_path,
            'TRIAGE_RUN_URL': 'https://github.com/%s/actions/runs/1' % REPO
        })
        environment.update(env or {})

        proc = subprocess.run(
            [os.path.join(self.tools, 'merge-ci-triage.sh'), RUN_ID],
            capture_output=True, text=True, env=environment)

        document = None
        path = os.path.join(self.output, 'triage.json')
        if os.path.exists(path):
            with open(path) as f:
                document = json.load(f)
        return proc.returncode, document, proc.stderr

    def gh_log(self):
        return self.read_fixture('gh.log') or ''

    def claude_args(self):
        """The argument vector the model wrapper was called with."""
        recorded = self.read_fixture('claude-args')
        if recorded is None:
            return None
        return recorded.split('\n')[:-1]

    def prompt(self):
        with open(os.path.join(self.output, 'prompt.txt')) as f:
            return f.read()

    # -- the citation check ------------------------------------------------

    def test_a_citation_the_issue_references_survives(self):
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertEqual(3772, document['tracking_issue'])
        self.assertEqual('commented', document['tracking_issue_action'])
        self.assertIsNotNone(self.read_fixture('posted-comment.md'))

    def test_an_issue_that_does_not_mention_the_run_loses_the_claim(self):
        # The issue exists, so the number is still a useful pointer for a
        # reader. What fails is the assertion that the occurrence was recorded
        # there, and that is what the conductor reads, so that is what goes.
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen in some other run.\n')

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertEqual(3772, document['tracking_issue'])
        self.assertEqual('none', document['tracking_issue_action'])
        self.assertIn('kept as a reference only', ' '.join(document['evidence']))

    def test_an_unreadable_issue_loses_the_citation_entirely(self):
        # No fixture, so the stub 404s. An issue which cannot be read is not
        # even a pointer.
        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertIsNone(document['tracking_issue'])
        self.assertEqual('none', document['tracking_issue_action'])
        self.assertIn('could not be read', ' '.join(document['evidence']))

    def test_a_run_with_no_url_does_not_verify_a_citation_vacuously(self):
        # "grep -qF ''" matches every non-empty input, so a run GitHub gave no
        # URL for would turn the check into a rubber stamp: any issue at all
        # would appear to carry the reference. The driver falls back to the
        # URL the run must have, and the prompt gets the same value.
        run = dict(RUN_JSON)
        del run['url']
        self.write_fixture('run.json', json.dumps(run))
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Nothing about this run.\n')

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertEqual('none', document['tracking_issue_action'])
        self.assertIn(RUN_URL, ' '.join(document['evidence']))

        with open(os.path.join(self.output, 'prompt.txt')) as f:
            self.assertIn(RUN_URL, f.read())

    def test_a_citation_already_claiming_nothing_is_not_checked(self):
        # An action of "none" says nothing was written, so nothing will
        # reference this run and checking anyway would drop every
        # reference-only citation ever made.
        self.response = dict(VERDICT, tracking_issue_action='none')
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertEqual(3772, document['tracking_issue'])
        self.assertEqual('none', document['tracking_issue_action'])
        self.assertNotIn('/issues/3772/comments', self.gh_log())

    # -- dry runs ----------------------------------------------------------

    def test_a_dry_run_records_nothing_and_says_so(self):
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, document, _ = self.run_triage(dry_run=True)
        self.assertEqual(0, code)
        self.assertEqual('none', document['tracking_issue_action'])
        self.assertIn('This was a dry run', ' '.join(document['evidence']))
        self.assertIsNone(self.read_fixture('posted-comment.md'))

    def test_a_dry_run_still_checks_the_claim_it_overwrote(self):
        # The claimed action is read before the dry run forces it to "none",
        # because gating the check on the value left in the document would
        # mean a dry run never exercises this path at all -- and a dry run is
        # how the driver is tested by hand.
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Nothing about this run.\n')

        code, document, _ = self.run_triage(dry_run=True)
        self.assertEqual(0, code)
        # In the words of a dry run, not those of a claim that did not check
        # out: on this path nothing was written, so the missing reference is
        # certain and says nothing about the model. Reporting it as a caught
        # lie is how a maintainer stops trusting the tool that does the
        # catching.
        evidence = ' '.join(document['evidence'])
        self.assertIn('Nothing was written, so the citation is unverified',
                      evidence)
        self.assertNotIn('kept as a reference only', evidence)
        # The citation itself survives a dry run: the issue triage would have
        # used is the useful half of a dry run's output.
        self.assertEqual(3772, document['tracking_issue'])

    def test_a_dry_run_drops_an_unreadable_citation_too(self):
        # The two failures are not interchangeable and the order of the
        # branches decides which one wins. An issue that cannot be read is not
        # a useful pointer whichever mode produced the citation, and testing
        # the dry run first would keep the number here -- inverting the
        # documented contract on exactly the path a maintainer uses to inspect
        # this behaviour by hand.
        code, document, _ = self.run_triage(dry_run=True)
        self.assertEqual(0, code)
        self.assertIsNone(document['tracking_issue'])
        self.assertEqual('none', document['tracking_issue_action'])
        self.assertIn('could not be read', ' '.join(document['evidence']))

    # -- what the model is shown -------------------------------------------

    def test_long_logs_are_cut_from_the_middle(self):
        # Both ends are kept, and the marker says how much went, because a
        # head-only cut elides the last thing a failed step emits -- which is
        # the message that says what broke.
        self.write_fixture(
            'failed-logs.txt', 'HEADMARK' + ('x' * 1000) + 'TAILMARK')
        code, _, _ = self.run_triage(
            env={'LOG_HEAD_BYTES': '100', 'LOG_TAIL_BYTES': '200'})
        self.assertEqual(0, code)

        with open(os.path.join(self.output, 'failed-logs.txt')) as f:
            logs = f.read()
        self.assertTrue(logs.startswith('HEADMARK'))
        self.assertTrue(logs.endswith('TAILMARK'))
        # 1016 bytes in, 300 of them kept.
        self.assertIn('[... 716 bytes elided by triage: this is the first 100 '
                      'and the last 200 bytes of the failed step logs ...]', logs)

    def test_the_prompt_reaches_the_model_as_a_file(self):
        # Never as an argument. Linux caps a single argv element at
        # MAX_ARG_STRLEN, a hard 128KiB no ulimit raises, and an argument over
        # it does not truncate -- the exec fails with E2BIG and no model runs.
        code, _, _ = self.run_triage()
        self.assertEqual(0, code)

        args = self.claude_args()
        self.assertIn('--prompt-file', args)
        self.assertEqual(os.path.join(self.output, 'prompt.txt'),
                         args[args.index('--prompt-file') + 1])
        for arg in args:
            self.assertNotIn(
                'You are triaging a failed merge queue CI run', arg,
                'the prompt is being passed as an argument again')

    def test_a_prompt_over_the_argv_limit_still_reaches_the_model(self):
        # The regression test for the bug above, at the size that produces it:
        # a failed Ansible cluster build emits megabytes, the budget keeps
        # 160KB of it here, and an argv element cannot exceed 131072 bytes.
        # Passed as an argument this run reaches no model at all and publishes
        # "unknown" -- on precisely the failure class the triage exists for.
        self.write_fixture('failed-logs.txt', 'HEADMARK' + ('x' * 4000000) + 'TAILMARK')
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, document, _ = self.run_triage(
            env={'LOG_HEAD_BYTES': '60000', 'LOG_TAIL_BYTES': '100000'})
        self.assertEqual(0, code)
        self.assertGreater(
            os.path.getsize(os.path.join(self.output, 'prompt.txt')), 131072,
            'this test no longer builds a prompt over the argv limit, so it '
            'no longer tests anything')
        self.assertEqual('systemic', document['verdict'])

    def test_a_model_that_could_not_be_run_is_not_a_model_that_rambled(self):
        # Every model out of subscription credit, a crashed CLI, an exec that
        # never happened: all of them exit non-zero and produce no verdict.
        # Reporting them as "the response contained no JSON verdict object"
        # sends a maintainer to read a response file that explains nothing,
        # and the whole point of always publishing a document is that it says
        # what happened.
        self._write_executable(
            os.path.join(self.tools, 'claude-model-fallback.sh'),
            BROKEN_CLAUDE_STUB)

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertEqual('unknown', document['verdict'])
        self.assertIn('the model wrapper exited 1', document['error'])
        self.assertIn('the model wrapper exited 1', ' '.join(document['evidence']))

    def test_the_sibling_runs_are_scoped_to_the_workflow_that_failed(self):
        # "--event merge_group" on its own is every workflow in the
        # repository, and these twenty slots exist to answer "is this
        # happening to everybody?" about this suite. The run being triaged is
        # not its own sibling either.
        self.write_fixture('sibling-runs.json', json.dumps([
            {'databaseId': int(RUN_ID), 'conclusion': 'failure',
             'headBranch': 'gh-readonly-queue/develop/pr-4080-abcdef0',
             'createdAt': '2026-09-05T00:00:00Z', 'url': RUN_URL},
            {'databaseId': 33952027000, 'conclusion': 'failure',
             'headBranch': 'gh-readonly-queue/develop/pr-4079-abcdef1',
             'createdAt': '2026-09-04T00:00:00Z',
             'url': 'https://github.com/%s/actions/runs/33952027000' % REPO}]))

        code, _, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertIn("--workflow Functional tests", self.gh_log())

        siblings = self.prompt().split('# Other recent merge_group runs')[1]
        siblings = siblings.split('# The pull request')[0]
        self.assertIn('33952027000', siblings)
        self.assertNotIn(RUN_ID, siblings)

    def test_a_log_cut_inside_a_character_leaves_valid_utf8(self):
        # The budget is in bytes, which is the right unit -- one wrapped
        # ansible line can be thousands of characters -- but a byte offset can
        # land in the middle of one. What is left over is invalid UTF-8 at the
        # join, and it travels into the prompt and into the run's artifact.
        with open(os.path.join(self.fixtures, 'failed-logs.txt'), 'wb') as f:
            # The cuts at 101 and 201 bytes both land inside a three-byte
            # character.
            f.write(b'x' * 100 + '\u4e2d'.encode('utf-8') * 40 + b'y' * 100)

        code, _, _ = self.run_triage(
            env={'LOG_HEAD_BYTES': '101', 'LOG_TAIL_BYTES': '101'})
        self.assertEqual(0, code)

        with open(os.path.join(self.output, 'failed-logs.txt'), 'rb') as f:
            logs = f.read()
        # Decodes, rather than raising UnicodeDecodeError on the join.
        logs.decode('utf-8')

    def test_evidence_that_could_not_be_gathered_travels_with_the_verdict(self):
        # A model handed an empty log still produces a verdict, and the
        # prompt's own heuristics push an evidence-free run towards "systemic,
        # re-queue". A reader has to be told which pieces were missing.
        os.unlink(os.path.join(self.fixtures, 'sibling-runs.json'))
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertIn('no correlation was possible', ' '.join(document['evidence']))

    def test_nothing_readable_at_all_does_not_reach_a_model(self):
        self.write_fixture('failed-jobs.json', json.dumps([]))
        self.write_fixture('failed-logs.txt', '')

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertEqual('unknown', document['verdict'])
        self.assertFalse(os.path.exists(os.path.join(self.output, 'prompt.txt')))

    # -- publication -------------------------------------------------------

    def test_the_comment_is_not_posted_if_it_cannot_be_neutralised(self):
        # The neutralisation rewrites the file in place, so a failure inside
        # it leaves the original, un-neutralised body exactly where "gh pr
        # comment" would read it from -- and this script does not run under
        # "set -e". A triage nobody sees beats one that fires an @mention.
        self._write_executable(
            os.path.join(self.tools, 'neutralise-pr-body.sh'), BROKEN_NEUTRALISER)
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, _, stderr = self.run_triage()
        self.assertEqual(1, code)
        self.assertIn('could not be neutralised', stderr)
        self.assertIsNone(self.read_fixture('posted-comment.md'))

    def test_the_posted_comment_is_neutralised(self):
        self.response = dict(
            VERDICT, summary='Reported by @mikal, fixes #1234, in run 33952027147.')
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, _, _ = self.run_triage()
        self.assertEqual(0, code)
        # The prose half. The embedded JSON keeps what the model said
        # verbatim, which is the point of embedding it: the neutraliser leaves
        # fenced regions alone because GitHub does not linkify inside one.
        prose = self.read_fixture('posted-comment.md').split('```json')[0]
        self.assertIn('mikal', prose)
        self.assertNotIn('@mikal', prose)
        self.assertNotIn('fixes #1234', prose)

    def test_the_dedup_marker_survives_neutralisation(self):
        # The marker is prepended to the comment body and the body is then
        # rewritten by neutralise-pr-body.sh, so the marker a later run greps
        # for is whatever came out of that pass. It has to be byte identical
        # to the one written, or a re-delivered workflow_run posts the same
        # verdict a second time.
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, _, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertTrue(
            self.read_fixture('posted-comment.md').startswith(
                '<!-- merge-triage run:%s -->' % RUN_ID),
            'the dedup marker did not survive the neutralisation pass')

    def test_a_comment_that_cannot_be_posted_fails_the_run(self):
        # A green run with nothing on the pull request is indistinguishable
        # from a triage that never ran, which is the ambiguity this design
        # exists to remove. The verdict is in the artifact either way, so what
        # is missing is the signal, and the run itself has to carry it.
        self.write_fixture('comment-fails', '')
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, document, stderr = self.run_triage()
        self.assertEqual(1, code)
        self.assertIn('could not comment on #%d' % PR_NUMBER, stderr)
        # The verdict itself is unharmed: it is the publication that failed.
        self.assertEqual('systemic', document['verdict'])

    def test_an_already_triaged_run_is_not_commented_on_twice(self):
        # The dedup search reads the pull request's comments through the
        # paginated issues API rather than "gh pr view --json comments", whose
        # GraphQL layer stops at a hundred: on a long-lived pull request the
        # marker would fall off the end and a re-delivered workflow_run would
        # post the same verdict again.
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)
        self.write_fixture(
            'issue-%d.comments' % PR_NUMBER,
            '<!-- merge-triage run:%s -->\nAn earlier verdict.\n' % RUN_ID)

        code, _, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertIsNone(self.read_fixture('posted-comment.md'))
        self.assertIn('--paginate repos/%s/issues/%d/comments' % (REPO, PR_NUMBER),
                      self.gh_log())

    # -- the document is always written ------------------------------------

    def test_a_run_that_cannot_be_read_still_publishes_a_document(self):
        # A missing document is indistinguishable from a triage that never
        # ran, which is the ambiguity the whole design exists to remove.
        os.unlink(os.path.join(self.fixtures, 'run.json'))

        code, document, _ = self.run_triage()
        self.assertEqual(1, code)
        self.assertEqual('unknown', document['verdict'])
        self.assertIn('could not be read', document['error'])

    def test_a_run_that_is_not_a_failed_merge_group_publishes_nothing(self):
        # The one exception: nothing was triaged, so there is no verdict to
        # file about it.
        self.write_fixture('run.json', json.dumps(dict(RUN_JSON, event='pull_request')))

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertIsNone(document)

    # -- runs the conductor cut short --------------------------------------

    def cut_short_run(self, job=CUT_SHORT_JOB, flag=True):
        """A cancelled merge group run, and the environment of its dispatch."""
        self.write_fixture('run.json', json.dumps(dict(RUN_JSON, conclusion='cancelled')))
        self.write_fixture('jobs.json', json.dumps(CUT_SHORT_JOBS))
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)
        env = {'CUT_SHORT_JOB': job}
        if flag:
            env['CUT_SHORT'] = 'true'
        return env

    def assert_nothing_triaged(self, code, document, expected_code):
        self.assertEqual(expected_code, code)
        self.assertIsNone(document)
        self.assertFalse(os.path.exists(os.path.join(self.output, 'prompt.txt')))
        self.assertIsNone(self.claude_args())
        self.assertIsNone(self.read_fixture('posted-comment.md'))

    def test_a_cut_short_run_naming_a_failed_job_is_triaged(self):
        code, document, _ = self.run_triage(env=self.cut_short_run())
        self.assertEqual(0, code)
        self.assertEqual('systemic', document['verdict'])
        self.assertIs(True, document['cut_short'])
        self.assertEqual(CUT_SHORT_JOB, document['cut_short_job'])

        prompt = self.prompt()
        self.assertIn('- Cut short: yes, after "%s" failed' % CUT_SHORT_JOB, prompt)
        self.assertIn('This run did not finish on its own. After the job "%s" failed,'
                      % CUT_SHORT_JOB, prompt)
        self.assertIn('were stopped deliberately, and are not\nevidence of anything',
                      prompt)
        # The bullet is in the list and the paragraph follows it, both before
        # the instructions.
        self.assertLess(prompt.index('- Base branch:'), prompt.index('- Cut short:'))
        self.assertLess(prompt.index('- Cut short:'),
                        prompt.index('This run did not finish on its own.'))
        self.assertLess(prompt.index('This run did not finish on its own.'),
                        prompt.index('Your job, in order:'))

        self.assertIn('This run was cut short by the merge queue fail-fast after `%s` failed.'
                      % CUT_SHORT_JOB, self.read_fixture('posted-comment.md'))

    def test_a_cut_short_run_shows_the_model_no_cancelled_job(self):
        # The jobs still running when the run was cancelled stopped part way
        # through for no reason of their own. They are not evidence.
        code, _, _ = self.run_triage(env=self.cut_short_run())
        self.assertEqual(0, code)
        with open(os.path.join(self.output, 'failed-jobs.json')) as f:
            selected = [job['job'] for job in json.load(f)]
        self.assertEqual([CUT_SHORT_JOB, 'Can merge'], selected)

    def test_a_cancelled_run_without_the_flag_is_refused(self):
        # A merge group superseded by a newer one, which is what the guard
        # was first written to refuse. Not a broken dispatch, so not red.
        code, document, _ = self.run_triage(env=self.cut_short_run(flag=False))
        self.assert_nothing_triaged(code, document, 0)

    def test_the_flag_without_a_job_name_fails_the_run(self):
        # Every cancelled run's aggregator fails, so a flag with no job named
        # would check nothing. Only a broken dispatch gets here, and a red
        # triage run is how somebody notices.
        code, document, stderr = self.run_triage(env=self.cut_short_run(job=''))
        self.assert_nothing_triaged(code, document, 1)
        self.assertIn('cut_short needs cut_short_job', stderr)

    def test_the_flag_naming_a_job_that_did_not_fail_fails_the_run(self):
        # Cancelled is not failed, and a job GitHub has never heard of has not
        # failed either.
        for job in ['develop debian 12 cluster (collection)', 'No such job',
                    CUT_SHORT_JOB.lower()]:
            self.write_fixture('gh.log', '')
            code, document, stderr = self.run_triage(env=self.cut_short_run(job=job))
            self.assert_nothing_triaged(code, document, 1)
            self.assertIn('did not fail in run %s' % RUN_ID, stderr)
            self.assertIn('run view %s --repo %s --json jobs' % (RUN_ID, REPO),
                          self.gh_log())

    def test_the_flag_fails_the_run_when_the_jobs_cannot_be_read(self):
        env = self.cut_short_run()
        os.unlink(os.path.join(self.fixtures, 'jobs.json'))
        code, document, _ = self.run_triage(env=env)
        self.assert_nothing_triaged(code, document, 1)

    def test_a_failed_run_without_the_flag_is_unchanged(self):
        # The workflow_run path. The document says so explicitly rather than
        # by omission, and the prompt is the one it has always been.
        self.write_fixture('issue-3772.body', 'The 507 flake.\n')
        self.write_fixture('issue-3772.comments', 'Seen again in %s\n' % RUN_URL)

        code, document, _ = self.run_triage()
        self.assertEqual(0, code)
        self.assertEqual('systemic', document['verdict'])
        self.assertIs(False, document['cut_short'])
        self.assertIsNone(document['cut_short_job'])
        self.assertNotIn('Cut short', self.prompt())
        self.assertNotIn('did not finish on its own', self.prompt())
        self.assertIn('- Base branch: develop\n\nYour job, in order:', self.prompt())
        self.assertNotIn('cut short', self.read_fixture('posted-comment.md'))
        # No second read of the jobs on this path.
        self.assertNotIn('--json jobs\n', self.gh_log())

    def test_a_failed_run_with_the_flag_is_refused(self):
        # The cancel lost a race and the run ended on its own, so
        # workflow_run owns its triage. Triaging it here too would see the
        # same run twice.
        env = self.cut_short_run()
        self.write_fixture('run.json', json.dumps(RUN_JSON))
        code, document, _ = self.run_triage(env=env)
        self.assert_nothing_triaged(code, document, 0)

    def test_timed_out_jobs_are_selected(self):
        # On every run, not only one that was cut short: a job which timed out
        # is as much evidence as one which failed.
        self.write_fixture('jobs.json', json.dumps({'jobs': [
            {'name': 'slow cluster', 'conclusion': 'timed_out',
             'url': '%s/job/1' % RUN_URL, 'steps': []},
            {'name': 'stopped lane', 'conclusion': 'cancelled',
             'url': '%s/job/2' % RUN_URL, 'steps': []},
            {'name': 'green lane', 'conclusion': 'success',
             'url': '%s/job/3' % RUN_URL, 'steps': []},
        ]}))
        code, _, _ = self.run_triage()
        self.assertEqual(0, code)
        with open(os.path.join(self.output, 'failed-jobs.json')) as f:
            self.assertEqual(['slow cluster'], [job['job'] for job in json.load(f)])

    def test_a_cut_short_run_naming_a_timed_out_job_is_triaged(self):
        env = self.cut_short_run()
        jobs = json.loads(json.dumps(CUT_SHORT_JOBS))
        jobs['jobs'][0]['conclusion'] = 'timed_out'
        self.write_fixture('jobs.json', json.dumps(jobs))
        code, document, _ = self.run_triage(env=env)
        self.assertEqual(0, code)
        self.assertIs(True, document['cut_short'])
        self.assertEqual(CUT_SHORT_JOB, document['cut_short_job'])

    def test_a_job_name_is_data_not_code(self):
        # The name is free text from a dispatch. It is compared by jq with
        # --arg and reaches the prompt through a variable, so nothing in it is
        # evaluated by the shell or by jq.
        job = 'Lint $(touch %s/pwned) `id` "quoted" \\ and | pipes' % self.tempdir
        jobs = {'jobs': [{'name': job, 'conclusion': 'failure',
                          'url': '%s/job/1' % RUN_URL, 'steps': []}]}
        env = self.cut_short_run(job=job)
        self.write_fixture('jobs.json', json.dumps(jobs))

        code, document, _ = self.run_triage(env=env)
        self.assertEqual(0, code)
        self.assertEqual(job, document['cut_short_job'])
        self.assertIn('- Cut short: yes, after "%s" failed' % job, self.prompt())
        self.assertFalse(os.path.exists(os.path.join(self.tempdir, 'pwned')))
