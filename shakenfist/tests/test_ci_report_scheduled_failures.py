# Copyright 2026 Michael Still and contributors

"""Tests for tools/ci_report_scheduled_failures.py.

The weekly lanes block no merge, so this reporter is the only thing which
tells anyone they failed. What it must get right: one issue per failed lane
rather than one per run, a comment rather than a duplicate when that lane's
issue is still open, the last commit the lane passed on, and an issue even
when no lane can be blamed -- a red run must never come back silent.

GitHub is faked: the real thing is gh on a subprocess. The tool is loaded by
path because tools/ is not a package.
"""

import importlib.util
import io
import os

from shakenfist.tests import base


TOOL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'tools', 'ci_report_scheduled_failures.py')

CALLER = 'Weekly lanes'
UBUNTU = 'Ubuntu 24.04 cluster (collection)'
TIER = 'Debian 13 tier (collection)'
SHA = 'a' * 40
GREEN = 'b' * 40


def _load():
    spec = importlib.util.spec_from_file_location('ci_report_scheduled_failures', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report_tool = _load()


def _job(lane, conclusion, steps=None):
    return {'name': '%s / %s / Smoke tests (collection)' % (CALLER, lane),
            'conclusion': conclusion, 'html_url': 'https://example.com/job',
            'steps': steps or []}


class FakeGitHub:
    repo = 'shakenfist/shakenfist'

    def __init__(self, jobs, earlier=None, issues=None):
        self._jobs = {100: jobs}
        self._earlier = []
        for run_id, sha, run_jobs in earlier or []:
            self._earlier.append({'id': run_id, 'head_sha': sha})
            self._jobs[run_id] = run_jobs
        self._issues = issues or []
        self.comments = []
        self.created = []

    def run(self, run_id):
        return {'id': 100, 'head_sha': SHA, 'head_branch': 'develop',
                'html_url': 'https://example.com/run/100'}

    def jobs(self, run_id, attempt=None):
        return self._jobs[int(run_id)]

    def earlier_runs(self, workflow, branch):
        # Newest first, and including the run being reported, as the API does.
        return [{'id': 100, 'head_sha': SHA}] + self._earlier

    def open_issues(self):
        return self._issues

    def comment(self, number, body):
        self.comments.append((number, body))

    def create(self, title, body):
        self.created.append((title, body))


class ReportTestCase(base.ShakenFistTestCase):
    def _report(self, github):
        return report_tool.report(github, 'scheduled-tests.yml', '100', '1', CALLER, out=io.StringIO())

    def test_one_issue_per_failed_lane(self):
        github = FakeGitHub([_job(UBUNTU, 'failure'), _job(TIER, 'failure'),
                             _job('Debian 13 single machine (collection)', 'success')])
        self.assertEqual(2, self._report(github))
        self.assertEqual(
            ['Scheduled lane failed: %s' % UBUNTU, 'Scheduled lane failed: %s' % TIER],
            [title for title, _ in github.created])
        self.assertEqual([], github.comments)

    def test_an_open_issue_for_the_lane_gets_a_comment_instead(self):
        issues = [{'number': 7, 'body': 'x\n%s\n' % report_tool.dedup_key(UBUNTU)},
                  {'number': 8, 'body': 'x\n%s\n' % report_tool.dedup_key(TIER)}]
        github = FakeGitHub([_job(UBUNTU, 'failure')], issues=issues)
        self._report(github)
        self.assertEqual([], github.created)
        self.assertEqual([7], [number for number, _ in github.comments])

    def test_the_issue_carries_its_own_dedup_key(self):
        # Otherwise the next failure could not find it, and would file again.
        github = FakeGitHub([_job(UBUNTU, 'failure')])
        self._report(github)
        _, body = github.created[0]
        self.assertEqual(7, report_tool.existing_issue([{'number': 7, 'body': body}], UBUNTU))
        self.assertIsNone(report_tool.existing_issue([{'number': 7, 'body': body}], TIER))

    def test_the_last_green_commit_skips_runs_where_the_lane_failed_or_did_not_run(self):
        earlier = [
            (99, 'c' * 40, [_job(UBUNTU, 'failure')]),
            (98, 'd' * 40, [_job(TIER, 'success')]),
            (97, GREEN, [_job(UBUNTU, 'success')]),
        ]
        github = FakeGitHub([_job(UBUNTU, 'failure')], earlier=earlier)
        self._report(github)
        _, body = github.created[0]
        self.assertIn('commit/%s' % GREEN, body)
        self.assertIn('compare/%s...%s' % (GREEN[:12], SHA[:12]), body)

    def test_no_green_commit_is_said_rather_than_guessed(self):
        github = FakeGitHub([_job(UBUNTU, 'failure')], earlier=[(99, 'c' * 40, [_job(UBUNTU, 'failure')])])
        self._report(github)
        _, body = github.created[0]
        self.assertIn('Last passed on: not within', body)
        self.assertNotIn('compare/', body)

    def test_failed_steps_are_named(self):
        steps = [{'name': 'Build the smoke cluster', 'conclusion': 'success'},
                 {'name': 'Run functional tests', 'conclusion': 'failure'}]
        github = FakeGitHub([_job(UBUNTU, 'failure', steps)])
        self._report(github)
        _, body = github.created[0]
        self.assertIn('failed at: Run functional tests', body)
        self.assertNotIn('Build the smoke cluster', body)

    def test_a_failure_outside_any_lane_is_still_reported(self):
        github = FakeGitHub([{'name': 'Something else', 'conclusion': 'failure', 'steps': []},
                             _job(UBUNTU, 'success')])
        self.assertEqual(1, self._report(github))
        self.assertEqual([report_tool.title_for(report_tool.UNATTRIBUTED)],
                         [title for title, _ in github.created])

    def test_a_red_run_with_no_failed_job_is_still_reported(self):
        # The workflow only calls this when the lanes job failed. If the jobs
        # listing cannot say why, that is itself worth an issue.
        github = FakeGitHub([_job(UBUNTU, 'success')])
        self.assertEqual(1, self._report(github))
        self.assertEqual(1, len(github.created))

    def test_dry_run_writes_nothing(self):
        github = FakeGitHub([_job(UBUNTU, 'failure')])
        out = io.StringIO()
        report_tool.report(github, 'scheduled-tests.yml', '100', '1', CALLER, dry_run=True, out=out)
        self.assertEqual([], github.created)
        self.assertIn('new issue', out.getvalue())


class LaneOfTestCase(base.ShakenFistTestCase):
    def test_the_lane_is_the_segment_under_the_caller(self):
        self.assertEqual(UBUNTU, report_tool.lane_of('%s / %s / Smoke tests (collection)' % (CALLER, UBUNTU), CALLER))
        # A skipped lane is reported without the called workflow's job.
        self.assertEqual(UBUNTU, report_tool.lane_of('%s / %s' % (CALLER, UBUNTU), CALLER))

    def test_a_job_under_another_caller_is_not_a_lane(self):
        self.assertEqual(report_tool.UNATTRIBUTED,
                         report_tool.lane_of('Functional lanes / %s / Smoke tests (collection)' % UBUNTU, CALLER))
        self.assertEqual(report_tool.UNATTRIBUTED, report_tool.lane_of('Report failures', CALLER))
