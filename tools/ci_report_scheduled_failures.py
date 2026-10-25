#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors

"""File or update a GitHub issue for each failed lane of a scheduled run.

The lanes scheduled-tests.yml runs used to gate every merge. They were moved
to a weekly run to take their nested clusters off the merge queue's share of
the sfcbr under-cloud, and that trade is only honest if a red weekly run
reaches a person. Nobody opens the Actions tab on a morning when nothing
seems wrong, so filing the issue is part of the workflow rather than a
follow-up.

One open issue per lane. A lane which stays red collects each later run as a
comment, rather than a new issue a week, and a second, unrelated failure on
the same lane arrives as a comment on the first -- visible, and the cheaper
direction. Each report names the commit tested and the last commit the lane
passed on, with a compare link between them, because "which merge broke it"
is the first question and a weekly run spans many merges.

A lane is identified from the job name the jobs API reports, which for a job
called through two reusable workflows is '<caller job> / <lane> / <called
job>'. A failed job which is not under the caller job -- an expression error,
say -- is reported on an issue of its own rather than dropped, so a red run
can never come back without an issue.

Usage:
    tools/ci_report_scheduled_failures.py --repo OWNER/REPO --run-id ID \\
        --run-attempt N --workflow scheduled-tests.yml --caller 'Weekly lanes' \\
        [--dry-run]

--dry-run reads the run from GitHub but prints the issues it would file or
comment on instead of writing anything. A run of any branch but the default
branch is always reported that way: the docs suggest dispatching the weekly
lanes on a branch to try them, and that branch's failures are its own, not
the lane's, so they must not land on the issue which tracks develop.
"""

import argparse
import json
import subprocess
import sys
import tempfile


LABEL = 'ci-failure'
FAILED_CONCLUSIONS = ('failure', 'timed_out')

# Not a lane: the key for failed jobs which are not under the caller job.
UNATTRIBUTED = 'the scheduled run itself'

# How many earlier runs to read looking for a lane's last green commit. The
# run is weekly, so this is a quarter of a year; a lane red for longer than
# that has bigger problems than a missing compare link.
GREEN_SEARCH_RUNS = 12


class GitHubCLI:
    """The calls this tool makes, through gh, which carries the auth."""

    def __init__(self, repo, gh='gh'):
        self.repo = repo
        self.gh = gh

    def _run(self, args, stdin=None):
        completed = subprocess.run(
            [self.gh] + args, input=stdin, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError('gh %s failed: %s' % (' '.join(args), completed.stderr.strip()))
        return completed.stdout

    def api(self, path):
        return json.loads(self._run(['api', 'repos/%s/%s' % (self.repo, path)]))

    def run(self, run_id):
        return self.api('actions/runs/%s' % run_id)

    def default_branch(self):
        return json.loads(self._run(['api', 'repos/%s' % self.repo]))['default_branch']

    def jobs(self, run_id, attempt=None):
        if attempt:
            base = 'actions/runs/%s/attempts/%s/jobs' % (run_id, attempt)
        else:
            base = 'actions/runs/%s/jobs' % run_id
        jobs = []
        page = 1
        while True:
            payload = self.api('%s?per_page=100&page=%d' % (base, page))
            jobs.extend(payload.get('jobs', []))
            if len(payload.get('jobs', [])) < 100:
                return jobs
            page += 1

    def earlier_runs(self, workflow, branch):
        payload = self.api('actions/workflows/%s/runs?status=completed&branch=%s&per_page=%d'
                           % (workflow, branch, GREEN_SEARCH_RUNS + 1))
        return payload.get('workflow_runs', [])

    def open_issues(self):
        return json.loads(self._run([
            'issue', 'list', '--repo', self.repo, '--label', LABEL, '--state', 'open',
            '--json', 'number,body', '--limit', '200']))

    def comment(self, number, body):
        self._with_body_file(['issue', 'comment', str(number), '--repo', self.repo], body)

    def create(self, title, body):
        self._with_body_file(['issue', 'create', '--repo', self.repo, '--label', LABEL,
                              '--title', title], body)

    def _with_body_file(self, args, body):
        with tempfile.NamedTemporaryFile('w', suffix='.md') as f:
            f.write(body)
            f.flush()
            self._run(args + ['--body-file', f.name])


def lane_of(job_name, caller):
    """The lane a job belongs to, or UNATTRIBUTED if it is not under caller."""
    segments = job_name.split(' / ')
    if len(segments) >= 2 and segments[0] == caller:
        return segments[1]
    return UNATTRIBUTED


def lane_results(jobs, caller):
    """{lane: [job, ...]} for every job in a run, in the order GitHub lists them."""
    lanes = {}
    for job in jobs:
        lanes.setdefault(lane_of(job.get('name') or '', caller), []).append(job)
    return lanes


def failed_lanes(jobs, caller):
    """{lane: [failed job, ...]}, only for lanes with at least one failed job."""
    failed = {}
    for lane, lane_jobs in lane_results(jobs, caller).items():
        bad = [job for job in lane_jobs if job.get('conclusion') in FAILED_CONCLUSIONS]
        if bad:
            failed[lane] = bad
    return failed


def lane_passed(jobs, caller, lane):
    """Whether a run ran this lane and every one of its jobs succeeded."""
    lane_jobs = lane_results(jobs, caller).get(lane, [])
    return bool(lane_jobs) and all(job.get('conclusion') == 'success' for job in lane_jobs)


def last_green_sha(github, workflow, branch, run_id, caller, lane):
    """The head SHA of the newest earlier run in which this lane passed, or None."""
    for run in github.earlier_runs(workflow, branch):
        if run['id'] >= int(run_id):
            continue
        if lane_passed(github.jobs(run['id']), caller, lane):
            return run['head_sha']
    return None


def dedup_key(lane):
    return '<!-- scheduled-lane: %s -->' % lane


def title_for(lane):
    if lane == UNATTRIBUTED:
        return 'Scheduled tests failed outside any lane'
    return 'Scheduled lane failed: %s' % lane


def failure_lines(repo, run, failed_jobs, green_sha):
    """The facts a report and a follow-up comment both carry."""
    sha = run['head_sha']
    lines = ['Run: %s' % run['html_url'],
             'Commit tested: https://github.com/%s/commit/%s' % (repo, sha)]
    if green_sha:
        lines.append('Last passed on: https://github.com/%s/commit/%s' % (repo, green_sha))
        lines.append('Changes since: https://github.com/%s/compare/%s...%s' % (repo, green_sha[:12], sha[:12]))
    else:
        lines.append('Last passed on: not within the last %d runs.' % GREEN_SEARCH_RUNS)
    lines.append('')
    lines.append('Failed jobs:')
    lines.append('')
    for job in failed_jobs:
        steps = [step['name'] for step in job.get('steps') or []
                 if step.get('conclusion') in FAILED_CONCLUSIONS]
        detail = ', failed at: %s' % '; '.join(steps) if steps else ''
        lines.append('- [%s](%s) (%s%s)' % (job.get('name'), job.get('html_url'), job.get('conclusion'), detail))
    return lines


def issue_body(repo, run, lane, failed_jobs, green_sha):
    if lane == UNATTRIBUTED:
        opening = ('A scheduled-tests.yml run failed in a job which is not one of its lanes, so '
                   'no lane issue could be filed for it. Check the workflow itself first.')
    else:
        opening = ('The weekly **%s** lane failed. This lane does not gate the merge queue, so '
                   'nothing else will report it: it runs weekly from scheduled-tests.yml, and '
                   'what it is defined to run is in functional-lanes.yml. Each later run which '
                   'fails this lane adds a comment here.' % lane)
    lines = [opening, ''] + failure_lines(repo, run, failed_jobs, green_sha)
    lines += ['', 'Reported by `tools/ci_report_scheduled_failures.py`.', '', dedup_key(lane)]
    return '\n'.join(lines) + '\n'


def comment_body(repo, run, failed_jobs, green_sha):
    lines = ['Failed again.', ''] + failure_lines(repo, run, failed_jobs, green_sha)
    return '\n'.join(lines) + '\n'


def existing_issue(issues, lane):
    key = dedup_key(lane)
    for issue in issues:
        if key in (issue.get('body') or ''):
            return issue['number']
    return None


def report(github, workflow, run_id, run_attempt, caller, dry_run=False, out=sys.stdout):
    """File or comment on one issue per failed lane. Returns how many it reported."""
    run = github.run(run_id)
    default_branch = github.default_branch()
    if run['head_branch'] != default_branch and not dry_run:
        out.write('This run tested %s, not the default branch %s. Its failures are the branch\'s own, so '
                  'this prints what it would have filed instead of filing it.\n'
                  % (run['head_branch'], default_branch))
        dry_run = True

    failed = failed_lanes(github.jobs(run_id, run_attempt), caller)
    if not failed:
        # The caller only runs this when the lanes job failed, so a run with
        # no failed job is itself worth an issue rather than a quiet exit.
        failed = {UNATTRIBUTED: []}

    issues = github.open_issues()
    for lane, failed_jobs in failed.items():
        green = None
        if lane != UNATTRIBUTED:
            green = last_green_sha(github, workflow, run['head_branch'], run_id, caller, lane)
        number = existing_issue(issues, lane)
        if number:
            body = comment_body(github.repo, run, failed_jobs, green)
            out.write('%s: comment on #%s\n' % (lane, number))
            if dry_run:
                out.write(body)
            else:
                github.comment(number, body)
        else:
            title = title_for(lane)
            body = issue_body(github.repo, run, lane, failed_jobs, green)
            out.write('%s: new issue %r\n' % (lane, title))
            if dry_run:
                out.write(body)
            else:
                github.create(title, body)
    return len(failed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--repo', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--run-attempt', default=None)
    parser.add_argument('--workflow', required=True,
                        help='The workflow file, used to find earlier runs.')
    parser.add_argument('--caller', required=True,
                        help='The name of the job which calls functional-lanes.yml.')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    report(GitHubCLI(args.repo), args.workflow, args.run_id, args.run_attempt, args.caller,
           dry_run=args.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
