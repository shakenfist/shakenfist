# Copyright 2026 Michael Still and contributors

"""No workflow may interpolate a secret into a run: script.

A ``${{ secrets.NAME }}`` expression is substituted into a ``run:``
script's *text* by the expression evaluator, before any shell starts. The
value therefore ends up in two places GitHub does not mask: the script
file the runner writes to disk, and -- if the script passes it as an
argument -- the command line of the process that is started with it.
``/proc/<pid>/cmdline`` is world readable, so on a self-hosted runner
that is every other process on the host, including those of other jobs.
Log masking covers neither.

The same secret passed through a step's or job's ``env:`` block, or
through an action's ``with:`` input, is not substituted into any script
text. It reaches the process as an environment variable, and
``/proc/<pid>/environ`` is readable only by the process owner.

This was one occurrence, not a class: ``release.yml``'s Ansible Galaxy
publish passed ``--api-key "${{ secrets.ANSIBLE_GALAXY_TOKEN }}"`` while
every other secret in the tree already went through ``env:`` or
``with:``. It survived because nothing looked, and it is the release path
-- a job which has never run (issue #4613), so no amount of use would
have surfaced it either.

So the set under test is derived from the tree rather than listed, which
is what makes it a guard rather than a record of one fix: a secret
interpolated into a new workflow's script fails here on the day it is
written.
"""

import os
import re

import yaml

from shakenfist.tests import base


# What the expression evaluator substitutes. Deliberately loose about
# whitespace and about what follows "secrets.", because the point is to
# catch any secret reference at all, not a particular one -- including
# the conditional forms (`cond && secrets.A || secrets.B`) that
# pin-indirect-dependencies.yml uses in its `with:` inputs.
SECRET_EXPRESSION_RE = re.compile(r'\$\{\{[^}]*\bsecrets\.')


# A job-level or step-level `env:` block, and an action's `with:` inputs,
# are the sanctioned destinations. Nothing here reads them -- they are
# listed so the docstring's claim about where a secret may go has a
# single place in the code that says so.
SANCTIONED_KEYS = ('env', 'with')


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _workflow_dir():
    return os.path.join(_repo_root(), '.github', 'workflows')


def _strip_comments(script):
    """Drop whole-line comments from a shell script.

    A comment naming a secret expression is not an interpolation -- the
    shell never sees it and the evaluator does not substitute inside a
    `#` line of a run: block... except that it *does*: expression
    substitution is textual and happens before the shell parses
    anything, so a commented-out `${{ secrets.X }}` really would be
    written into the script file. It is only the command line that it
    would stay out of.

    We strip them anyway, because release.yml's own explanation of this
    rule quotes the pattern it forbids, and a guard which cannot survive
    its own rationale being written down is a guard nobody will keep.
    Inline (trailing) comments are left alone, so a secret smuggled
    after a real command still fails.
    """
    return '\n'.join(line for line in script.splitlines()
                     if not line.lstrip().startswith('#'))


def _steps(workflow):
    """Every step of every job, as (job name, step index, step)."""
    for job_name, job in sorted((workflow.get('jobs') or {}).items()):
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get('steps') or []):
            if isinstance(step, dict):
                yield job_name, index, step


class WorkflowSecretHandlingTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.workflows = {}
        for name in sorted(os.listdir(_workflow_dir())):
            if not name.endswith(('.yml', '.yaml')):
                continue
            with open(os.path.join(_workflow_dir(), name)) as f:
                self.workflows[name] = yaml.safe_load(f)

    def test_the_derivation_finds_workflows(self):
        """A vacuous pass is a failure.

        Every assertion below iterates the parsed workflows, so an empty
        or unparsed directory would pass all of them while checking
        nothing.
        """
        self.assertNotEqual(
            {}, self.workflows,
            'no workflows parsed out of .github/workflows/, so every '
            'other assertion in this file passed without looking at '
            'anything')

        self.assertIn(
            'release.yml', self.workflows,
            'release.yml is the workflow this guard was written for '
            '(issue #4711); if it has been renamed, say so here')

    def test_strip_comments_only_exempts_whole_lines(self):
        """The comment exemption is for whole lines only.

        _strip_comments() exists so that release.yml can quote the
        pattern it forbids in its own explanation. That exemption has to
        be narrow, or it becomes the hiding place: a secret appended to a
        real command, with a `#` earlier on the line, must still be
        found.
        """
        whole_line = '# see ${{ secrets.EXAMPLE }} for why\necho hello\n'
        self.assertIsNone(
            SECRET_EXPRESSION_RE.search(_strip_comments(whole_line)),
            'a whole-line comment naming a secret should be exempt')

        # A '#' earlier in the line, inside a string, is the case that
        # separates "drop comment lines" from "drop everything after a
        # hash": only the former leaves this secret visible.
        after_a_hash = ('echo "# release notes"; publish '
                        '--api-key "${{ secrets.EXAMPLE }}"\n')
        self.assertIsNotNone(
            SECRET_EXPRESSION_RE.search(_strip_comments(after_a_hash)),
            'a secret later on a line which merely contains a hash is '
            'still an interpolation and must not be stripped away')

        indented = '    # ${{ secrets.EXAMPLE }}\n'
        self.assertIsNone(
            SECRET_EXPRESSION_RE.search(_strip_comments(indented)),
            'an indented whole-line comment should be exempt too')

    def test_no_secret_is_interpolated_into_a_run_script(self):
        offenders = []
        for name, workflow in sorted(self.workflows.items()):
            for job_name, index, step in _steps(workflow):
                script = step.get('run')
                if not isinstance(script, str):
                    continue
                if SECRET_EXPRESSION_RE.search(_strip_comments(script)):
                    offenders.append(
                        '%s: job %s, step %d (%s)'
                        % (name, job_name, index,
                           step.get('name', '<unnamed>')))

        self.assertEqual(
            [], offenders,
            'these run: scripts interpolate a secret into their own '
            'text, which puts it in the script file on disk and on the '
            'command line of whatever the script starts. Pass it '
            'through the step\'s env: block instead, or through the '
            'tool\'s own out-of-band configuration: %s'
            % ', '.join(offenders))

    def test_at_least_one_workflow_passes_a_secret_the_sanctioned_way(self):
        """The guard has to be falsifiable in the other direction too.

        If nothing in the tree used a secret at all, the assertion above
        would pass for the wrong reason, and a later change which moved
        every secret into a run: script would be the only thing that
        made it speak. This checks that secrets are genuinely in use and
        genuinely going somewhere else.
        """
        sanctioned = []
        for name, workflow in sorted(self.workflows.items()):
            for job_name, index, step in _steps(workflow):
                for key in SANCTIONED_KEYS:
                    block = step.get(key)
                    if not isinstance(block, dict):
                        continue
                    for value in block.values():
                        if isinstance(value, str) and \
                                SECRET_EXPRESSION_RE.search(value):
                            sanctioned.append('%s:%s' % (name, job_name))

        self.assertNotEqual(
            [], sanctioned,
            'no workflow passes a secret through env: or with:, so the '
            'run:-script assertion in this file is passing vacuously')
