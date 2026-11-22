#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors

"""Give stestr the functional suites' test durations before it partitions them.

stestr splits a suite across its workers once, up front, and never moves a
test afterwards. When its repository holds a duration for every test it
deals them longest first onto the least loaded worker, which keeps the
workers within a test or two of each other. When it holds none -- and a CI
node's repository is always empty, because the node is built for the job --
it deals them round robin in name order instead, so every worker gets the
same number of tests whatever they cost. The functional suites are dominated
by a handful of tests which take minutes each, and round robin can hand all
of them to one worker: shakenfist/shakenfist#5010 found the cluster suite
with 136 minutes of work across five workers, one of which drew 55 of them
and ran the step out of its 60 minute budget while the others sat idle.

The durations are kept in shakenfist/deploy/stestr-times.json, next to the
stestr configurations, and this tool has two jobs:

    tools/ci_stestr_times.py seed [--times FILE] [--repo-url DIR]

(--repo-url means what it does to stestr itself: the directory which holds
the .stestr repository, by default the current one.)

runs on the CI node, from shakenfist/deploy, immediately before stestr run.
It creates the stestr repository and writes the durations into it, then
reads them back through stestr's own API to prove stestr will see them.
smoke-cluster.yml in shakenfist/actions calls it, and treats a failure as a
warning: an unseeded run is slower to finish, not wrong.

    tools/ci_stestr_times.py harvest [--repo OWNER/REPO] [--runs N]

runs on a developer's machine with gh. It reads the per-test durations the
functional jobs of recent CI runs printed, takes the median of each test's
observations, and rewrites the times file. Rerun it when the suites change
shape; until then a test the file does not name is still run, stestr just
deals it round robin after placing the ones it knows.

Seeding writes the durations into the repository's times database rather
than loading a made up run. A loaded run would become the run stestr
failing and stestr last report on whenever the real one never commits --
which is precisely what a timed out step leaves behind -- and they would
then report a run which never happened, every test passing.
"""

import argparse
import collections
import datetime
import json
import os
import re
import statistics
import subprocess
import sys


TIMES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'shakenfist', 'deploy', 'stestr-times.json')

# The workflows whose runs carry the functional suites. A merge group runs
# the cluster lanes, a pull request runs only the smoke tier, and the guest
# suite runs in the weekly lanes, so all three suites need both files.
WORKFLOWS = ['functional-tests.yml', 'scheduled-tests.yml']

# subunit-trace's line for a passing test, as the job log carries it after
# the timestamp GitHub prefixes:
#   {2} shakenfist_ci.smoke_ci_tests.test_x.TestX.test_y [12.345678s] ... ok
RESULT_LINE = re.compile(
    r'\{\d+\} (shakenfist_ci\.[\w.]+) \[([0-9]+(?:\.[0-9]+)?)s\] \.\.\. ok\s*$')


class TimesError(Exception):
    pass


def load_times(path):
    """Return the test id to seconds map a times file holds, validated."""
    try:
        with open(path) as f:
            document = json.load(f)
    except (OSError, ValueError) as e:
        raise TimesError('cannot read %s: %s' % (path, e))

    times = document.get('times') if isinstance(document, dict) else None
    if not isinstance(times, dict) or not times:
        raise TimesError('%s has no "times" map of test id to seconds' % path)
    for test_id, seconds in times.items():
        if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                or seconds < 0):
            raise TimesError('%s gives %s a duration of %r, which is not a '
                             'number of seconds' % (path, test_id, seconds))
    return {test_id: float(seconds) for test_id, seconds in times.items()}


def seed(times, repo_url):
    """Write durations into a stestr repository, creating it if need be.

    Returns how many of the durations stestr itself then reports knowing.
    """
    # Imported here so that harvest, which runs where stestr may not be
    # installed, does not need it. The CI node runs seed inside the suite's
    # venv, which installs stestr from pyproject.toml.
    from dbm import dumb
    from stestr import utils
    from stestr.repository import util as repo_util

    if os.path.isdir(os.path.join(repo_url, '.stestr')):
        repo = repo_util.get_repo_open(repo_url=repo_url)
    else:
        repo = repo_util.get_repo_initialise(repo_url=repo_url)

    # The file repository keeps durations in a dumb dbm called times.dbm,
    # keyed by the cleaned up test id, each value the seconds as a string.
    # That is what its own inserter writes at the end of a run, and what
    # partitioning reads back through get_test_times below.
    db = dumb.open(os.path.join(repo.base, 'times.dbm'), 'c')
    try:
        for test_id, seconds in times.items():
            db[utils.cleanup_test_name(test_id)] = str(seconds)
    finally:
        db.close()

    return len(repo.get_test_times(list(times))['known'])


def parse_log(text):
    """Return (test id, seconds) for each passing test a job log reports."""
    for line in text.splitlines():
        m = RESULT_LINE.search(line)
        if m:
            yield m.group(1), float(m.group(2))


def gh_json(*args):
    out = subprocess.run(['gh'] + list(args), check=True,
                         stdout=subprocess.PIPE, text=True).stdout
    return json.loads(out)


def gh_text(*args):
    return subprocess.run(['gh'] + list(args), check=True,
                          stdout=subprocess.PIPE, text=True).stdout


def harvest(repo, runs_per_workflow, since):
    """Collect every passing test's durations from recent functional runs."""
    observations = collections.defaultdict(list)
    run_ids = []
    for workflow in WORKFLOWS:
        runs = gh_json('run', 'list', '--repo', repo, '--workflow', workflow,
                       '--status', 'completed', '--limit',
                       str(runs_per_workflow), '--created', '>=%s' % since,
                       '--json', 'databaseId,conclusion')
        for run in runs:
            # A cancelled run's jobs were cut off mid-test and say little;
            # a failed run's passing tests are as well timed as any.
            if run['conclusion'] not in ('success', 'failure'):
                continue
            jobs = gh_json('api', '--paginate',
                           'repos/%s/actions/runs/%d/jobs' % (repo, run['databaseId']),
                           '--jq', '[.jobs[] | {id, name, conclusion}]')
            seen = False
            for job in jobs:
                if 'Smoke tests' not in job['name']:
                    continue
                if job['conclusion'] not in ('success', 'failure'):
                    continue
                # Job logs carry the ANSI colour codes the steps printed, and
                # gh refuses to write those to a pipe unless told it may.
                try:
                    log = gh_text('api', '--allow-escape-sequences',
                                  'repos/%s/actions/jobs/%d/logs' % (repo, job['id']))
                except subprocess.CalledProcessError:
                    # Logs expire, and one unreadable job should not cost the
                    # rest of the harvest.
                    print('job %d: log unavailable, skipped' % job['id'], file=sys.stderr)
                    continue
                for test_id, seconds in parse_log(log):
                    observations[test_id].append(seconds)
                    seen = True
            if seen:
                run_ids.append(run['databaseId'])
            print('run %d: %d tests observed so far'
                  % (run['databaseId'], len(observations)), file=sys.stderr)
    return observations, sorted(run_ids)


def render(observations, run_ids, today):
    times = {test_id: round(statistics.median(seconds), 1)
             for test_id, seconds in sorted(observations.items())}
    document = {
        'about': ('Median duration in seconds of each functional test across '
                  'recent CI runs, which tools/ci_stestr_times.py seed writes '
                  'into the stestr repository so that stestr balances its '
                  'workers by time rather than by count. Regenerate with '
                  'tools/ci_stestr_times.py harvest; see '
                  'docs/developer_guide/ci.md.'),
        'harvested': today,
        'runs': run_ids,
        'times': times,
    }
    return json.dumps(document, indent=2) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)

    p_seed = sub.add_parser('seed', help='seed a stestr repository from the times file')
    p_seed.add_argument('--times', default='stestr-times.json',
                        help='the times file (default: %(default)s)')
    p_seed.add_argument('--repo-url', default='.',
                        help='the directory holding the .stestr repository, as '
                             'stestr\'s own --repo-url (default: %(default)s)')

    p_harvest = sub.add_parser('harvest', help='rewrite the times file from recent CI runs')
    p_harvest.add_argument('--repo', default='shakenfist/shakenfist')
    p_harvest.add_argument('--runs', type=int, default=15,
                           help='completed runs to read per workflow (default: %(default)s)')
    p_harvest.add_argument('--days', type=int, default=30,
                           help='ignore runs older than this many days (default: %(default)s)')
    p_harvest.add_argument('--output', default=TIMES_FILE)

    args = parser.parse_args(argv)

    if args.command == 'seed':
        try:
            times = load_times(args.times)
        except TimesError as e:
            print('Not seeding stestr: %s' % e, file=sys.stderr)
            return 1
        known = seed(times, args.repo_url)
        print('Seeded stestr with the durations of %d tests; it reports knowing %d.'
              % (len(times), known))
        if known != len(times):
            print('stestr does not see every duration written, so its partitions '
                  'will be only partly balanced by time.', file=sys.stderr)
            return 1
        return 0

    since = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
    observations, run_ids = harvest(args.repo, args.runs, since)
    if not observations:
        print('No passing tests found in the runs read; not writing %s.'
              % args.output, file=sys.stderr)
        return 1
    with open(args.output, 'w') as f:
        f.write(render(observations, run_ids,
                       datetime.date.today().isoformat()))
    print('Wrote durations for %d tests from %d runs to %s.'
          % (len(observations), len(run_ids), args.output))
    return 0


if __name__ == '__main__':
    sys.exit(main())
