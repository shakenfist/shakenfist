# Copyright 2026 Michael Still and contributors

"""The seams between functional-lanes.yml and the workflows which call it.

functional-lanes.yml defines every functional cluster lane once, each as a
job gated on its name appearing in the caller's 'lanes' input. A lane is that
gate and a name, not a particular kind of job: most lanes call the reusable
smoke-cluster workflow and name themselves in its concurrency_key input, and
one runs its own steps and names itself in its own concurrency group.

That shape has one dangerous failure: a caller naming a lane which does not
exist, or a lane whose gate names something other than itself, skips that
lane -- and a called workflow whose jobs all skip succeeds, so "Can merge"
would go green having tested nothing. Nothing at runtime notices. These tests
are what does.

They also hold the property the split exists for: every lane runs somewhere,
either in the merge queue (functional-tests.yml) or weekly
(scheduled-tests.yml), and never in both. And they hold the other half of a
demotion: can_merge accepts a skipped dependency only on a docs-only change,
so a lane which moved to the weekly run and stayed in can_merge's needs would
fail every code-changing merge group.
"""

import glob
import json
import os
import re

import yaml

from shakenfist.tests import base


LANES_WORKFLOW = 'functional-lanes.yml'
LANES_USES = './.github/workflows/' + LANES_WORKFLOW
LANE_GATE = re.compile(r"^contains\(fromJSON\(inputs\.lanes\), '([a-z0-9-]+)'\)$")

# The jobs which call the lanes workflow, and so decide when a lane runs.
MERGE_CALLER = ('functional-tests.yml', 'functional_lanes')
WEEKLY_CALLER = ('scheduled-tests.yml', 'weekly_lanes')


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


class FunctionalLanesWorkflowSeamsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        pattern = os.path.join(_repo_root(), '.github', 'workflows', '*.y*ml')
        self.workflows = {}
        for path in sorted(glob.glob(pattern)):
            with open(path) as f:
                self.workflows[os.path.basename(path)] = yaml.safe_load(f)
        self.lanes_jobs = self.workflows[LANES_WORKFLOW]['jobs']

    def _defined_lanes(self):
        """{lane name: job id} as functional-lanes.yml defines them."""
        lanes = {}
        for job_id, job in self.lanes_jobs.items():
            match = LANE_GATE.match(job.get('if', ''))
            self.assertIsNotNone(
                match,
                '%s job %s is not gated as a lane. Every job there must have '
                "if: contains(fromJSON(inputs.lanes), '<its concurrency_key>')."
                % (LANES_WORKFLOW, job_id))
            lane = match.group(1)
            self.assertNotIn(lane, lanes, 'Two jobs are gated on lane %s.' % lane)
            lanes[lane] = job_id
        return lanes

    def _callers(self):
        """{(workflow, job id): [lane, ...]} for every call of the lanes workflow."""
        callers = {}
        for name, workflow in self.workflows.items():
            for job_id, job in (workflow.get('jobs') or {}).items():
                if job.get('uses') == LANES_USES:
                    lanes = json.loads((job.get('with') or {}).get('lanes', 'null'))
                    self.assertIsInstance(lanes, list, '%s:%s passes no lanes list.' % (name, job_id))
                    callers[(name, job_id)] = lanes
        return callers

    def test_a_lane_is_named_by_its_concurrency_key(self):
        # One name per lane, so the name a caller passes is also the one in
        # the lane's concurrency group and bundle artifact. A lane which
        # calls the reusable smoke-cluster workflow says that name in its
        # concurrency_key input. A lane which runs its own steps has no
        # inputs to say it in, so its own concurrency group carries it, and
        # that group is the only place the name is written down.
        for lane, job_id in self._defined_lanes().items():
            job = self.lanes_jobs[job_id]
            if job.get('uses'):
                key = (job.get('with') or {}).get('concurrency_key')
                self.assertEqual(
                    lane, key,
                    '%s job %s is gated on %r but its concurrency_key is %r.'
                    % (LANES_WORKFLOW, job_id, lane, key))
            else:
                group = (job.get('concurrency') or {}).get('group') or ''
                self.assertIn(
                    lane, group,
                    '%s job %s is gated on %r, but that name is not in its '
                    'concurrency group %r. A lane which runs its own steps '
                    'passes no concurrency_key, so its concurrency group is '
                    'where its name has to appear.'
                    % (LANES_WORKFLOW, job_id, lane, group))

    def test_the_callers_are_the_ones_expected(self):
        # Guards the scan below: a renamed caller job would otherwise drop
        # out of it without failing anything.
        self.assertEqual({MERGE_CALLER, WEEKLY_CALLER}, set(self._callers()))

    def test_every_lane_a_caller_names_exists(self):
        defined = self._defined_lanes()
        for (name, job_id), lanes in self._callers().items():
            self.assertTrue(lanes, '%s:%s selects no lanes, so it tests nothing.' % (name, job_id))
            self.assertEqual(len(lanes), len(set(lanes)), '%s:%s names a lane twice.' % (name, job_id))
            for lane in lanes:
                self.assertIn(
                    lane, defined,
                    '%s:%s selects lane %r, which %s does not define. It would '
                    'skip, and the caller would still succeed.'
                    % (name, job_id, lane, LANES_WORKFLOW))

    def test_every_lane_runs_in_exactly_one_place(self):
        callers = self._callers()
        merge = set(callers[MERGE_CALLER])
        weekly = set(callers[WEEKLY_CALLER])
        self.assertEqual(
            set(), merge & weekly,
            'These lanes run both in the merge queue and weekly; the weekly '
            'run only repeats what every merge already did.')
        self.assertEqual(
            set(), set(self._defined_lanes()) - merge - weekly,
            'These lanes are defined in %s but neither caller selects them, '
            'so they never run.' % LANES_WORKFLOW)

    def test_the_merge_lanes_gate_the_merge(self):
        workflow, job_id = MERGE_CALLER
        needs = self.workflows[workflow]['jobs']['can_merge']['needs']
        self.assertIn(job_id, needs)

    def test_no_weekly_lane_gates_the_merge(self):
        # A demotion is two edits: the lane's name moves between the two
        # 'lanes' lists, and the lane leaves can_merge's needs. can_merge
        # passes a skipped dependency only when check_paths found no code
        # change, so a lane left in that list after it stopped running on
        # merge_group fails every code-changing merge group -- and that run
        # is the one release.yml's CI gate counts.
        merge_workflow, _ = MERGE_CALLER
        needs = self.workflows[merge_workflow]['jobs']['can_merge']['needs']
        job_ids = self._defined_lanes()
        for lane in self._callers()[WEEKLY_CALLER]:
            # Both spellings: the name the caller passes, and the job id,
            # which is what a lane demoted out of the merge workflow was
            # called in that needs: list.
            for name in sorted({lane, job_ids.get(lane, lane)}):
                self.assertNotIn(
                    name, needs,
                    '%s runs weekly, but can_merge still needs %r. It cannot '
                    'run on merge_group, so it skips, and can_merge fails a '
                    'skipped dependency on any change which touches code.'
                    % (lane, name))

    def test_weekly_failures_are_reported(self):
        # The weekly lanes block nothing, so a failure nobody is told about
        # is a lane which has been deleted rather than moved.
        workflow, job_id = WEEKLY_CALLER
        jobs = self.workflows[workflow]['jobs']
        report = jobs['report_failures']
        self.assertEqual([job_id], report['needs'])
        self.assertIn("needs.%s.result == 'failure'" % job_id, report['if'])
        self.assertEqual('write', report['permissions']['issues'])
        self.assertEqual('read', report['permissions']['actions'])
        run = report['steps'][-1]['run']
        self.assertIn('tools/ci_report_scheduled_failures.py', run)
        # The reporter finds a lane's jobs under this name.
        self.assertIn('--caller "%s"' % jobs[job_id]['name'], run)
        self.assertIn('--workflow %s' % workflow, run)

    def test_the_weekly_run_can_be_dispatched_without_inputs(self):
        # private-ci's conductor dispatches workflows with no inputs, and is
        # expected to take this one over from the cron.
        triggers = self.workflows[WEEKLY_CALLER[0]][True]
        self.assertIn('workflow_dispatch', triggers)
        self.assertFalse((triggers['workflow_dispatch'] or {}).get('inputs'))
