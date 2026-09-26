# Copyright 2026 Michael Still and contributors

"""Every call of smoke-cluster.yml passes the headroom gate's off switch.

PLAN-ci-cloud-sizing phase 5 made a cluster-wide band violation fail the
cluster job, in `shakenfist/actions`'s smoke-cluster.yml, which this
repository reaches at `@main` with no pin. The recovery for a spurious gate
is therefore not a revert but the CI_HEADROOM_GATE repository variable, and
that only works for a call site which passes it through as `headroom_gate`.
A call site which does not is gated with no way to switch it off, and
nothing about the job would say so until the day the switch was needed.
docs/developer_guide/ci.md states the rule; this is what enforces it.
"""

import glob
import os
import re

import yaml

from shakenfist.tests import base


REUSABLE_WORKFLOW = 'smoke-cluster.yml'
OFF_SWITCH = "${{ vars.CI_HEADROOM_GATE != 'false' }}"


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


class HeadroomGateWorkflowSeamsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        pattern = os.path.join(_repo_root(), '.github', 'workflows', '*.y*ml')
        self.workflows = {}
        for path in sorted(glob.glob(pattern)):
            with open(path) as f:
                text = f.read()
            self.workflows[os.path.basename(path)] = (text, yaml.safe_load(text))

    def _call_sites(self):
        sites = []
        for name, (_, workflow) in self.workflows.items():
            for job_name, job in (workflow.get('jobs') or {}).items():
                uses = job.get('uses') or ''
                if REUSABLE_WORKFLOW in uses:
                    sites.append(('%s:%s' % (name, job_name), job))
        return sites

    def test_every_call_site_passes_the_off_switch(self):
        sites = self._call_sites()
        self.assertTrue(
            sites,
            'No job calls %s, so this test checks nothing. If the reusable '
            'workflow was renamed, update REUSABLE_WORKFLOW.'
            % REUSABLE_WORKFLOW)
        for site, job in sites:
            self.assertEqual(
                OFF_SWITCH, (job.get('with') or {}).get('headroom_gate'),
                '%s calls %s without passing headroom_gate: %s, so the '
                'CI_HEADROOM_GATE repository variable cannot switch the '
                'headroom band gate off for it.'
                % (site, REUSABLE_WORKFLOW, OFF_SWITCH))

    def test_every_textual_reference_is_a_parsed_call_site(self):
        """A reference the job walk above missed would escape the check.

        Counted from the raw text so that a call written somewhere the
        walk does not look -- a step rather than a job, or under a key it
        does not expect -- shows up as a mismatch rather than as silence.
        """
        uses = re.compile(r'^\s*(?:-\s*)?uses:\s*\S*%s'
                          % re.escape(REUSABLE_WORKFLOW), re.MULTILINE)
        textual = sum(len(uses.findall(text))
                      for text, _ in self.workflows.values())
        self.assertEqual(
            textual, len(self._call_sites()),
            'A workflow references %s in a uses: line this test did not '
            'parse as a job-level call, so its headroom_gate is unchecked.'
            % REUSABLE_WORKFLOW)
