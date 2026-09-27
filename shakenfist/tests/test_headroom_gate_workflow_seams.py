# Copyright 2026 Michael Still and contributors

"""Every call of smoke-cluster.yml states its headroom gate policy.

PLAN-ci-cloud-sizing phase 5 made a cluster-wide band violation fail the
cluster job, in `shakenfist/actions`'s smoke-cluster.yml, which this
repository reaches at `@main` with no pin. Two rules follow, and this is
what enforces them; docs/developer_guide/ci.md states them.

A call site which is gated must pass the CI_HEADROOM_GATE repository
variable through as `headroom_gate`, because the recovery for a spurious
gate is that variable rather than a revert. The reusable workflow defaults
to gating, so a call site which passes nothing is gated with no way to
switch it off, and nothing about the job would say so until the day the
switch was needed. Every call site therefore passes `headroom_gate`
explicitly, either as the off switch or as a literal false.

And a call site may only be gated on a job shape a warn window measured.
D7's test for arming the gate was that it would not have failed runs which
were fine, and that test says nothing about a shape it never saw. The
shape is derived from what each call site (and each matrix entry) actually
passes, so a new matrix entry on an unmeasured topology fails here rather
than arming itself.
"""

import glob
import os
import re

import yaml

from shakenfist.tests import base


REUSABLE_WORKFLOW = 'smoke-cluster.yml'
OFF_SWITCH = "${{ vars.CI_HEADROOM_GATE != 'false' }}"

# The inputs which decide a job's shape. An armed call site must pass every
# one of them: their defaults live in smoke-cluster.yml in shakenfist/actions,
# reached @main with no pin, so a shape derived from a default could drift
# from what the job runs without anything here changing.
SHAPE_INPUTS = ('topology', 'tier', 'test_kind', 'stestr_config')

# The shapes phase 5's warn window measured -- every job of the merge
# matrix, ten merge_group runs each, recorded in the 5e Outcome of
# docs/plans/PLAN-ci-cloud-sizing-phase-05-guardrails.md. Adding a shape
# here is the act of arming the gate on it, and needs a window of its own.
# base_image is deliberately not part of the shape: the band measures the
# cloud's committed vCPU against its ledger, not the guest, and the window
# covered two base images (Debian 12 and Ubuntu 24.04) on the same topology
# without the fraction separating them.
MEASURED_SHAPES = {
    ('slim-primary', 'full', 'functional', 'cluster-ci.conf'),
    ('slim-primary', 'full', 'functional', 'guest-ci.conf'),
    ('slim-tier', 'full', 'functional', 'cluster-ci.conf'),
}

MATRIX_REFERENCE = re.compile(r'^\$\{\{\s*matrix\.(\w+)\.(\w+)\s*\}\}$')


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

    def _shapes(self, site, job):
        """The job shape of every run a call site can make.

        One per matrix entry the shape inputs reference, or one for a call
        site with no matrix. An input which is an expression this cannot
        resolve fails the test rather than being skipped: a shape it cannot
        derive is a shape it cannot say was measured.
        """
        inputs = job.get('with') or {}
        matrix = (job.get('strategy') or {}).get('matrix') or {}
        axes = set()
        for key in SHAPE_INPUTS:
            match = MATRIX_REFERENCE.match(str(inputs.get(key, '')))
            if match:
                axes.add(match.group(1))
        self.assertLessEqual(
            len(axes), 1,
            '%s takes its shape from more than one matrix axis (%s), which '
            'this test does not expand.' % (site, sorted(axes)))
        entries = matrix.get(axes.pop()) if axes else [{}]
        self.assertTrue(entries, '%s references a matrix axis with no entries.'
                        % site)

        shapes = []
        for entry in entries:
            shape = []
            for key in SHAPE_INPUTS:
                self.assertIn(
                    key, inputs,
                    '%s arms the headroom band gate without passing %s, so '
                    'its shape would rest on a default which lives in '
                    'another repository. Pass it explicitly.' % (site, key))
                value = inputs[key]
                match = MATRIX_REFERENCE.match(str(value))
                if match:
                    self.assertIn(
                        match.group(2), entry,
                        '%s: matrix entry %r has no %s.'
                        % (site, entry, match.group(2)))
                    value = entry[match.group(2)]
                self.assertNotIn(
                    '${{', str(value),
                    '%s passes %s as %r, which this test cannot resolve to '
                    'a job shape.' % (site, key, value))
                shape.append(value)
            shapes.append(tuple(shape))
        return shapes

    def test_every_call_site_states_its_gate(self):
        sites = self._call_sites()
        self.assertTrue(
            sites,
            'No job calls %s, so this test checks nothing. If the reusable '
            'workflow was renamed, update REUSABLE_WORKFLOW.'
            % REUSABLE_WORKFLOW)
        for site, job in sites:
            gate = (job.get('with') or {}).get('headroom_gate')
            self.assertIn(
                gate, (OFF_SWITCH, False),
                '%s calls %s with headroom_gate: %r. It must pass either %s, '
                'so the CI_HEADROOM_GATE repository variable can switch the '
                'headroom band gate off for it, or false. Passing nothing '
                'leaves the policy to the reusable workflow\'s default, which '
                'lives in another repository at @main and can change without '
                'anything here changing.'
                % (site, REUSABLE_WORKFLOW, gate, OFF_SWITCH))

    def test_only_measured_shapes_are_gated(self):
        armed = 0
        for site, job in self._call_sites():
            if (job.get('with') or {}).get('headroom_gate') is False:
                continue
            armed += 1
            for shape in self._shapes(site, job):
                self.assertIn(
                    shape, MEASURED_SHAPES,
                    '%s arms the headroom band gate on %s (topology, tier, '
                    'test_kind, stestr_config), which no warn window has '
                    'measured. Pass headroom_gate: false until one has.'
                    % (site, shape))
        self.assertGreater(
            armed, 0,
            'No call site arms the headroom band gate, so this test checks '
            'nothing about which shapes are gated.')

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
