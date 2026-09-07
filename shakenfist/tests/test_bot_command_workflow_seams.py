# Copyright 2026 Michael Still and contributors

"""Tests for the workflows an authorized user starts by commenting on a PR.

There are three of them and they are near-copies of each other, which is the
whole problem: a guard added to one is not added to the others, and nothing
fails when it is missed. That is not hypothetical. `pr-fix-tests.yml` was
the workflow which did *not* get the bot-comment exclusion when the other
two did -- and it is the one holding `contents: write`, so its
self-retrigger writes commits rather than burning a runner. These tests are
what would have caught it.

So the set under test is derived from the tree (every workflow triggered by
`issue_comment`) rather than listed, and `KNOWN_BOT_COMMAND_WORKFLOWS` is a
floor rather than the set: a fourth bot command is covered the day it is
added, and a derivation which silently finds nothing still fails.

The other seam is `pr-retest.yml`'s dispatch. It names its target workflow
as a literal string in a `gh workflow run` argument and relies on that file
carrying a `workflow_dispatch` trigger. Neither is checked anywhere else;
the workflow anticipates the failure in its own error message, and the
error message is what a requester sees instead of a test run.
"""

import os
import re

import yaml

from shakenfist.tests import base


# Every workflow a PR comment can start. Not the set -- the set is derived
# from the tree below -- but the floor beneath it, so a derivation which
# matches nothing fails instead of passing vacuously.
KNOWN_BOT_COMMAND_WORKFLOWS = {
    'pr-re-review.yml',
    'pr-retest.yml',
    'pr-fix-tests.yml',
}


TRIGGER_ACTION = 'shakenfist/actions/pr-bot-trigger@main'


# What pr-bot-trigger's "Check trigger phrase" step actually matches:
# "@shakenfist-bot " followed by the trigger-phrase input. The workflow's
# own contains() guard has to agree with it exactly, or the two disagree
# about what fires.
MENTION = '@shakenfist-bot '


# The bot-comment exclusion. The automated review is model-generated text
# posted to the pull request by a bot, and contains() does not care that a
# quoted trigger phrase sits inside a code fence.
BOT_EXCLUSION = "github.event.comment.user.type != 'Bot'"


CONTAINS_RE = re.compile(
    r"contains\(\s*github\.event\.comment\.body\s*,\s*'([^']*)'\s*\)")


# Where the bot commands are written down for people. Every mention in
# it is inside backticks, which is what makes them findable.
CI_DOC = os.path.join('docs', 'developer_guide', 'ci.md')


DOCUMENTED_RE = re.compile(r'`@shakenfist-bot ([^`]+)`')


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _workflow_dir():
    return os.path.join(_repo_root(), '.github', 'workflows')


def _triggers(workflow):
    """The workflow's trigger events, always as a dict keyed by event name.

    Two normalisations, and both of them are the difference between this
    file testing the tree and this file testing three files it already
    knows about.

    YAML 1.1 reads a bare "on" as the boolean true, which is what
    yaml.safe_load implements and what GitHub's own parser does not.

    And "on:" takes three shapes, not one. "on: push" is a string,
    "on: [push, issue_comment]" is a list, and only the block form is a
    dict. Returning them unnormalised meant the caller's isinstance
    check dropped the other two silently -- so a fourth bot command
    written "on: [issue_comment]" would have been excluded from every
    assertion here, with nothing failing to say so. That is precisely
    the workflow this file exists to cover.
    """
    triggers = workflow.get('on', workflow.get(True))
    if isinstance(triggers, str):
        return {triggers: None}
    if isinstance(triggers, list):
        return {trigger: None for trigger in triggers}
    return triggers or {}


class BotCommandWorkflowSeamsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.root = _repo_root()
        self.workflows = {}
        for name in sorted(os.listdir(_workflow_dir())):
            if not name.endswith(('.yml', '.yaml')):
                continue
            with open(os.path.join(_workflow_dir(), name)) as f:
                parsed = yaml.safe_load(f)
            if 'issue_comment' in _triggers(parsed):
                self.workflows[name] = parsed

    def _trigger_job(self, name):
        """The job which runs pr-bot-trigger, found rather than named."""
        for job_name, job in self.workflows[name]['jobs'].items():
            for step in job.get('steps', []):
                if step.get('uses') == TRIGGER_ACTION:
                    return job_name, job, step
        self.fail(
            '%s is started by a pull request comment but no job in it uses '
            '%s, so it hand-rolls the phrase match, the collaborator check '
            'and the fork guard -- and does not inherit fixes to any of '
            'them.' % (name, TRIGGER_ACTION))

    def test_every_known_bot_command_workflow_was_found(self):
        # The derivation above is the thing every other test in this file
        # rests on, so it is checked rather than trusted. A rename, a
        # rewritten "on:" block, or a yaml.safe_load quirk that made
        # _triggers() return None would otherwise leave every test below
        # iterating an empty dict and passing.
        missing = KNOWN_BOT_COMMAND_WORKFLOWS - set(self.workflows)
        self.assertEqual(
            set(), missing,
            'these workflows are no longer detected as issue_comment '
            'triggered, so nothing below is testing them: %s'
            % ', '.join(sorted(missing)))

    def test_every_bot_command_uses_the_shared_trigger_action(self):
        for name in self.workflows:
            self._trigger_job(name)

    def test_every_bot_command_ignores_comments_written_by_a_bot(self):
        for name in self.workflows:
            job_name, job, _ = self._trigger_job(name)
            self.assertIn(
                BOT_EXCLUSION, job.get('if', ''),
                'the %s job in %s does not exclude comments posted by a '
                'bot, so model-generated text quoting its trigger phrase '
                're-fires it. This is the exact gap pr-fix-tests.yml had.'
                % (job_name, name))

    def test_every_bot_command_only_fires_on_pull_requests(self):
        # issue_comment covers issues as well as pull requests, and the
        # downstream jobs all read github.event.issue.number as a PR number.
        for name in self.workflows:
            job_name, job, _ = self._trigger_job(name)
            self.assertIn(
                'github.event.issue.pull_request', job.get('if', ''),
                'the %s job in %s fires on issue comments as well as pull '
                'request comments' % (job_name, name))

    def test_the_guard_phrase_matches_the_action_phrase(self):
        # The job's contains() decides whether a runner is claimed at all;
        # the action's trigger-phrase decides whether anything happens once
        # it is. If the two drift apart the workflow either burns a runner
        # to be refused by its own action, or claims one for a phrase the
        # action never advertised.
        for name in self.workflows:
            job_name, job, step = self._trigger_job(name)
            found = CONTAINS_RE.findall(job.get('if', ''))
            self.assertEqual(
                1, len(found),
                'the %s job in %s does not guard on exactly one comment '
                'phrase, so which phrase claims a runner is unclear'
                % (job_name, name))
            self.assertEqual(
                MENTION + step['with']['trigger-phrase'], found[0],
                'the %s job in %s claims a runner for %r while '
                'pr-bot-trigger matches %r'
                % (job_name, name, found[0],
                   MENTION + step['with']['trigger-phrase']))

    def test_retest_dispatches_a_workflow_which_exists_and_accepts_dispatch(self):
        with open(os.path.join(_workflow_dir(), 'pr-retest.yml')) as f:
            text = f.read()
        dispatched = re.findall(r'gh workflow run\s+(\S+)', text)
        self.assertEqual(
            1, len(dispatched),
            'pr-retest.yml no longer dispatches exactly one workflow, so '
            'this test does not know what to check')

        target = os.path.join(_workflow_dir(), dispatched[0])
        self.assertTrue(
            os.path.exists(target),
            'pr-retest.yml dispatches %s, which does not exist. The failure '
            'is a comment on the pull request telling the requester to '
            'check exactly this, which is not the same as anything '
            'noticing.' % dispatched[0])

        with open(target) as f:
            triggers = _triggers(yaml.safe_load(f))
        self.assertIn(
            'workflow_dispatch', triggers,
            '%s has no workflow_dispatch trigger, so "please retest" '
            'cannot start it' % dispatched[0])

    def test_the_documented_commands_are_the_implemented_ones(self):
        # The drift this repairs is the one that produced the finding:
        # ci.md advertised a bot command whose workflow had been retired,
        # so a reader was told to type a phrase nothing listened for.
        # Both halves matter -- an undocumented command is a feature
        # nobody can find, and a documented one that does not exist is
        # an instruction that fails silently -- so this compares the two
        # sets rather than checking one direction.
        with open(os.path.join(self.root, CI_DOC)) as f:
            documented = set(DOCUMENTED_RE.findall(f.read()))

        implemented = set()
        for name in self.workflows:
            _, _, step = self._trigger_job(name)
            implemented.add(step['with']['trigger-phrase'])

        self.assertEqual(
            implemented, documented,
            'the bot commands in %s and the trigger phrases in '
            '.github/workflows/ disagree. Documented but not '
            'implemented: %s. Implemented but not documented: %s.'
            % (CI_DOC,
               ', '.join(sorted(documented - implemented)) or 'none',
               ', '.join(sorted(implemented - documented)) or 'none'))
