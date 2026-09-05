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

# A stub gh. Dispatches on the same argument shapes the driver uses and reads
# its answers out of a fixture directory, so a test says what GitHub knows by
# writing files. Every invocation is appended to gh.log, which is how the
# tests that care about *which* API was called assert it.
GH_STUB = '''#!/usr/bin/env python3
import os
import shutil
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
        sys.exit(emit('failed-jobs.json'))
    sys.exit(emit('run.json'))

if args[:2] == ['run', 'list']:
    sys.exit(emit('sibling-runs.json'))

if args[:2] == ['pr', 'view']:
    sys.exit(emit('pr.json'))

if args[:2] == ['pr', 'comment']:
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
# so a test says what the model answered by writing CLAUDE_RESPONSE.
CLAUDE_STUB = '''#!/bin/bash
cat "${CLAUDE_RESPONSE}"
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
        self.assertIn('kept as a reference only', ' '.join(document['evidence']))
        # The citation itself survives a dry run: the issue triage would have
        # used is the useful half of a dry run's output.
        self.assertEqual(3772, document['tracking_issue'])

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
