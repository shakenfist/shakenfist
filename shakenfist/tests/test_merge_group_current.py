# Copyright 2026 Michael Still and contributors

"""Tests for tools/ci-merge-group-current.sh and how the workflow calls it.

The script is what stops a superseded merge group run cancelling the live
one (issue #3998). Its two failure directions are not equally bad -- calling
a live run superseded ejects a pull request untested, which is the bug -- so
which way each case falls is tested rather than read off the source. The
script is run for real with `git` and `gh` replaced by stubs on PATH.

The workflow half pins the seams that make the check worth anything: it is
the last step of the job every queue job waits for, so nothing enters a
merge-group-keyed concurrency group before it has run, and its job holds the
token scope the cancellation needs.
"""

import os
import shutil
import subprocess
import tempfile

import yaml

from shakenfist.tests import base


BASE_REF = 'refs/heads/develop'
BASE_SHA = '80292df08060be0cfcbc26a98470151df0f9c63d'
NEWER_SHA = 'deb25fc78e1b0f6f7c9a6f1a2b3c4d5e6f708192'
RUN_ID = '37264092929'

# Records its arguments, then answers ls-remote from fixtures: the line to
# print, and the exit code to print it with.
GIT_STUB = """#!/bin/bash
echo "$@" >> "${STUB_LOG}/git.log"
if [ -e "${STUB_LOG}/ls-remote.out" ]; then
    cat "${STUB_LOG}/ls-remote.out"
fi
exit "$(cat "${STUB_LOG}/ls-remote.rc" 2>/dev/null || echo 0)"
"""

GH_STUB = """#!/bin/bash
echo "$@" >> "${STUB_LOG}/gh.log"
exit "$(cat "${STUB_LOG}/gh.rc" 2>/dev/null || echo 0)"
"""


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


class MergeGroupCurrentScriptTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.script = os.path.join(
            _repo_root(), 'tools', 'ci-merge-group-current.sh')
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.bin = os.path.join(self.tempdir, 'bin')
        self.log = os.path.join(self.tempdir, 'log')
        os.makedirs(self.bin)
        os.makedirs(self.log)
        for name, content in [('git', GIT_STUB), ('gh', GH_STUB)]:
            path = os.path.join(self.bin, name)
            with open(path, 'w') as f:
                f.write(content)
            os.chmod(path, 0o755)

    def _fixture(self, name, content):
        with open(os.path.join(self.log, name), 'w') as f:
            f.write(content)

    def _read(self, name):
        path = os.path.join(self.log, name)
        if not os.path.exists(path):
            return ''
        with open(path) as f:
            return f.read()

    def remote_head(self, sha, rc=0):
        if sha is not None:
            self._fixture('ls-remote.out', '%s\t%s\n' % (sha, BASE_REF))
        self._fixture('ls-remote.rc', str(rc))

    def run_check(self):
        environment = dict(os.environ)
        environment.update({
            'PATH': '%s:%s' % (self.bin, os.environ['PATH']),
            'STUB_LOG': self.log,
            'BASE_REF': BASE_REF,
            'BASE_SHA': BASE_SHA,
            'RUN_ID': RUN_ID,
            'CANCEL_WAIT_SECONDS': '0',
        })
        return subprocess.run(
            [self.script], capture_output=True, text=True, env=environment)

    def test_a_current_merge_group_carries_on(self):
        self.remote_head(BASE_SHA)
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual('', self._read('gh.log'))

    def test_the_base_ref_from_the_event_is_what_is_read(self):
        self.remote_head(BASE_SHA)
        self.run_check()
        self.assertEqual(
            'ls-remote --exit-code origin %s\n' % BASE_REF,
            self._read('git.log'))

    def test_a_superseded_merge_group_cancels_its_own_run(self):
        self.remote_head(NEWER_SHA)
        proc = self.run_check()
        self.assertEqual('run cancel %s\n' % RUN_ID, self._read('gh.log'))
        self.assertIn('superseded', proc.stdout)

    def test_a_superseded_run_still_running_after_cancelling_fails(self):
        # Exiting zero here would let every queue job start on a stale base.
        self.remote_head(NEWER_SHA)
        proc = self.run_check()
        self.assertNotEqual(0, proc.returncode)

    def test_a_superseded_run_that_cannot_cancel_fails(self):
        self.remote_head(NEWER_SHA)
        self._fixture('gh.rc', '1')
        proc = self.run_check()
        self.assertNotEqual(0, proc.returncode)
        self.assertIn('Could not cancel', proc.stdout)

    def test_an_unreadable_base_branch_is_assumed_current(self):
        # Calling a live run superseded is the bug, so not knowing must not
        # cancel anything.
        self.remote_head(None, rc=128)
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual('', self._read('gh.log'))
        self.assertIn('::warning::', proc.stdout)

    def test_a_missing_base_branch_is_assumed_current(self):
        # ls-remote --exit-code exits 2, printing nothing, for a ref which
        # does not exist.
        self.remote_head(None, rc=2)
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual('', self._read('gh.log'))

    def test_an_empty_answer_is_assumed_current(self):
        self._fixture('ls-remote.out', '')
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual('', self._read('gh.log'))


class MergeGroupCurrentWorkflowSeamsTestCase(base.ShakenFistTestCase):
    STEP_NAME = 'Cancel this run if the merge group is superseded'

    def setUp(self):
        super().setUp()
        path = os.path.join(_repo_root(), '.github', 'workflows',
                            'functional-tests.yml')
        with open(path) as f:
            self.workflow = yaml.safe_load(f)
        self.jobs = self.workflow['jobs']
        self.check_paths = self.jobs['check_paths']

    def _step(self):
        for step in self.check_paths['steps']:
            if step.get('name') == self.STEP_NAME:
                return step
        self.fail('check_paths has no step named %r' % self.STEP_NAME)

    def test_the_check_is_the_last_step_of_check_paths(self):
        # Every step after it widens the window in which a run it passed
        # can be superseded before its jobs enter their groups.
        self.assertEqual(self.STEP_NAME,
                         self.check_paths['steps'][-1].get('name'))

    def test_the_check_runs_only_on_merge_group(self):
        self.assertEqual("github.event_name == 'merge_group'",
                         self._step()['if'])

    def test_the_check_calls_the_script_with_the_event_fields(self):
        step = self._step()
        self.assertEqual('tools/ci-merge-group-current.sh', step['run'])
        env = step['env']
        self.assertEqual('${{ github.event.merge_group.base_ref }}',
                         env['BASE_REF'])
        self.assertEqual('${{ github.event.merge_group.base_sha }}',
                         env['BASE_SHA'])
        self.assertEqual('${{ github.run_id }}', env['RUN_ID'])
        self.assertIn('GH_TOKEN', env)

    def test_check_paths_can_cancel_its_own_run_and_still_checkout(self):
        permissions = self.check_paths['permissions']
        self.assertEqual('write', permissions.get('actions'))
        self.assertEqual('read', permissions.get('contents'))

    def test_every_merge_group_keyed_job_waits_for_the_check(self):
        # A queue job which does not need check_paths could enter its
        # concurrency group before a superseded run has cancelled itself,
        # and win the race this check exists to remove.
        keyed = []
        for name, job in self.jobs.items():
            group = (job.get('concurrency') or {}).get('group', '')
            if 'merge_group.base_ref' in group or job.get('uses', '').endswith(
                    'smoke-cluster.yml@main'):
                keyed.append(name)
        # Guards the scan itself: these are the jobs the issue names.
        for name in ['functional_matrix_merge_collection',
                     'ansible_modules_collection',
                     'node_lifecycle_collection', 'schema_enum_widening']:
            self.assertIn(name, keyed)

        for name in keyed:
            needs = self._all_needs(name)
            self.assertIn('check_paths', needs,
                          '%s can run in the queue without waiting for '
                          'check_paths' % name)

    def _all_needs(self, name):
        """Every job name reaches through needs:, transitively."""
        seen = set()
        pending = [name]
        while pending:
            needs = self.jobs[pending.pop()].get('needs', [])
            if isinstance(needs, str):
                needs = [needs]
            for need in needs:
                if need not in seen:
                    seen.add(need)
                    pending.append(need)
        return seen
