#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors
#
# mutate-agent-dependencies.py -- break each guard on an agent
# operation's dependency edges on purpose, and check that the right test
# fails naming the right thing.
#
# Reading a guard cannot distinguish "this holds" from "this cannot
# fail", and a test which passes for the wrong reason is worse than a
# missing one because it reads as evidence. So every refusal the edge
# model and the API helper claim to make is reverted here and the
# failure is asserted. It lives in a script rather than being
# re-improvised so that the set grows as the guards do: a review round
# which adds a guard adds its mutation here, and a round which finds a
# guard with no mutation has found a gap in this file rather than in the
# coverage.
#
# Each entry names the test which must fail. A mutation which leaves the
# tests green means either the guard does nothing or the test cannot
# fail; both are defects and both are reported as LIVE.
#
# This is a developer tool, not a CI job. It EDITS THE TREE IN PLACE and
# restores each file from a copy taken when it starts, so it will
# silently revert a concurrent edit to any of the files it touches --
# do not run it while something else is working in the same worktree,
# and that includes pre-commit, whose stash is repository wide. The
# same hazard was recorded against tools/mutate-nested-sweep.sh by an
# earlier push audit.
#
# Usage:
#
#   python3 tools/mutate-agent-dependencies.py [path-to-worktree]
#
# It needs a built .tox/shared in the worktree, since that is what runs
# the tests.

import os
import shutil
import subprocess
import sys
import tempfile

WT = os.path.abspath(
    sys.argv[1] if len(sys.argv) > 1
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
SAVE = tempfile.mkdtemp(prefix='mutate-agent-dependencies-')
DEP = 'shakenfist/schema/agentoperation_dependency.py'
BASE = 'shakenfist/external_api/base.py'
MARIADB = 'shakenfist/mariadb.py'
MAIN = 'shakenfist/daemons/database/main.py'
AGENTOP = 'shakenfist/operations/agentoperation.py'
SIDECHANNEL = 'shakenfist/daemons/sidechannel/main.py'

# (label, file, old, new, test selector, phrase the failure must name)
#
# The wire guards are here as well as the request guards, because an
# earlier audit of tools/mutate-nested-sweep.sh found its mutation set
# covered the original change but not the guards a review round added --
# which is the gap that makes a growing script worth anything.
FILES = (DEP, BASE, MARIADB, MAIN, AGENTOP, SIDECHANNEL)

MUTANTS = [
    ('finiteness guard', DEP,
     "        if not math.isfinite(v):\n            raise ValueError(\n"
     "                f'settle_seconds must be a finite number of seconds, got {v}')",
     "        if False:\n            raise ValueError('unreachable')",
     r'test_instance\.AgentOperationDependencyTestCase',
     'test_a_non_finite_settle_delay_is_refused'),
    ('bool refusal', DEP,
     "        if isinstance(v, bool):\n            raise ValueError(\n"
     "                'settle_seconds must be a number of seconds, not a boolean')",
     "        if False:\n            raise ValueError('unreachable')",
     r'test_instance\.AgentOperationDependencyTestCase',
     'test_a_boolean_settle_delay_is_refused'),
    ('extra=forbid', DEP,
     "    model_config = ConfigDict(extra='forbid')",
     "    model_config = ConfigDict(extra='ignore')",
     r'test_instance\.AgentOperationDependencyTestCase',
     'test_an_unknown_edge_key_is_refused'),
    ('edge list bound', BASE,
     "        if edges is not None and len(edges) > MAX_AGENT_DEPENDENCIES:",
     "        if False:",
     r'external_api\.test_agent_operation_parameters',
     'over_the_edge_limit'),
    ('non-list guard', BASE,
     "        if edges is not None and not isinstance(edges, list):",
     "        if False:",
     r'external_api\.test_agent_operation_parameters',
     'non_list_edge_parameter'),
    ('settle ceiling', BASE,
     "            if (dependency.settle_seconds is not None\n"
     "                    and dependency.settle_seconds\n"
     "                    > config.AGENT_OPERATION_DEPENDENCY_WAIT):",
     "            if False:",
     r'external_api\.test_agent_operation_parameters',
     'settle_ceiling_is_enforced_by_the_handler_in_warn_mode'),
    ('servicer empty edge', MAIN,
     "        depends_on = (json.loads(d.depends_on_json)\n"
     "                      if d.depends_on_json else [])",
     "        depends_on = json.loads(d.depends_on_json or '[]')\n"
     "        depends_on = json.loads(d.depends_on_json) if True else []",
     r'test_mariadb_agent_operations\.',
     'without_new_fields'),
    ('client empty edge', MARIADB,
     "        depends_on = json.loads(d.depends_on_json) if d.depends_on_json else []",
     "        depends_on = json.loads(d.depends_on_json)",
     r'test_mariadb_agent_operations\.',
     'empty_edge_string'),
    ('failure json None', MARIADB,
     "    if data.dependency_failure is None:\n        return None",
     "    if False:\n        return None",
     r'test_mariadb_agent_operations\.',
     'dependency_failure'),
    # Without the terminal state guard the cascade writes its reason and
    # only then meets the state machine's refusal, which leaves a
    # finished operation publishing a dependency_failure that is not
    # true of it. The expired case is the reachable one, since deadline
    # enforcement moves an operation out of band.
    ('cascade terminal guard', AGENTOP,
     "        if self.state.value in self.TERMINAL_STATES:\n"
     "            add_event_multi(",
     "        if False:\n"
     "            add_event_multi(",
     r'test_agent_operation_dependency_cascade',
     'cascade_of_an_expired_operation_writes_nothing'),
    ('TypeError phrasing', BASE,
     "    if isinstance(error, TypeError):\n        return 'not an object'",
     "    if False:\n        return 'not an object'",
     r'external_api\.test_agent_operation_parameters',
     'does_not_name_an_internal_constructor'),
    # The dispatch evaluator. The asymmetry between the two edge lists
    # gets a mutant per branch, because flattening it is the most
    # likely way this walk regresses: both branches look like the same
    # condition, and only one of them is fate sharing.
    #
    # There is deliberately no mutant for the no-edges early return.
    # With both lists empty each walk iterates zero times, so removing
    # the statement returns the same verdict having read nothing and no
    # test can distinguish it. A mutant which cannot fail reads as
    # coverage without being any.
    ('depends_on failed membership', SIDECHANNEL,
     "            if dep_state in DEPENDENCY_FAILED_STATES:",
     "            if dep_state not in DEPENDENCY_FAILED_STATES:",
     r'test_daemon_sidechannel_dependencies',
     'test_an_errored_dependency_cascades_and_records_the_state'),
    # The sentinel is the only thing which tells a client a dependency
    # went away from one which finished badly, and it is a value no
    # operation holds, so reporting a real state instead is silent.
    ('missing sentinel', SIDECHANNEL,
     "                self._cascade_abort(agentop, dep, DEPENDENCY_MISSING)\n"
     "                return False, 'dependency is missing'",
     "                self._cascade_abort(agentop, dep,\n"
     "                                    AgentOperation.STATE_DELETED)\n"
     "                return False, 'dependency is missing'",
     r'test_daemon_sidechannel_dependencies',
     'test_a_missing_dependency_cascades_as_missing'),
    # The comparison itself now lives in _wait_budget_spent() and is
    # mutated below; what this one breaks is the depends_on pending
    # arm's use of it, so an operation parked behind a dependency which
    # never finishes holds its instance's queue head forever. The
    # comment line is part of the anchor because the runs_after walk's
    # pending arm is otherwise identical, and a mutation set which
    # silently hits the other walk proves nothing about this one.
    ('depends_on wait is unbounded', SIDECHANNEL,
     "                if self._wait_budget_spent(agentop, budget):\n"
     "                    # The analogue of the cluster side's",
     "                if False:\n"
     "                    # The analogue of the cluster side's",
     r'test_daemon_sidechannel_dependencies',
     'test_a_wait_past_the_budget_cascades'),
    ('runs_after wait is unbounded', SIDECHANNEL,
     "                if self._wait_budget_spent(agentop, budget):\n"
     "                    # Ordering again: a missing runs_after edge already",
     "                if False:\n"
     "                    # Ordering again: a missing runs_after edge already",
     r'test_daemon_sidechannel_dependencies',
     'test_a_runs_after_wait_past_the_budget_dispatches'),
    ('settle comparison', SIDECHANNEL,
     "        if time.time() - finished >= settle:\n"
     "            return SETTLE_SATISFIED",
     "        if time.time() - finished < settle:\n"
     "            return SETTLE_SATISFIED",
     r'test_daemon_sidechannel_dependencies',
     'test_a_settle_delay_blocks_until_it_has_elapsed'),
    # The settle clock is the dependency's observed finish, not the wait
    # anchor. Anchoring it on the wait is what this code did first, and
    # it meant a dependency which outran its own settle delay produced
    # no delay at all -- the whole parameter, silently absent, for the
    # commonest shape of dependency there is.
    ('settle anchored on the wait', SIDECHANNEL,
     "        finished = self._dependency_finished_at(dep, dep_state)",
     "        finished = agentop.note_dependency_wait_started()",
     r'test_daemon_sidechannel_dependencies',
     'test_a_settle_does_not_run_down_while_the_dependency_does'),
    # The settle's own exhaustion check. It is measured from the wait
    # anchor, because that is what bounds how long the queue head is
    # held, but extended by the declared settle: the two clocks have
    # different origins, so without the extension a settle inside the
    # API's ceiling is still unsatisfiable whenever the dependency took
    # any time at all to finish.
    ('settle bound drops the settle', SIDECHANNEL,
     "        if self._wait_budget_spent(agentop, budget + settle):\n"
     "            return SETTLE_EXHAUSTED",
     "        if self._wait_budget_spent(agentop, budget):\n"
     "            return SETTLE_EXHAUSTED",
     r'test_daemon_sidechannel_dependencies',
     'test_a_settle_at_the_ceiling_survives_a_slow_dependency'),
    ('settle budget comparison', SIDECHANNEL,
     "        anchor = agentop.note_dependency_wait_started()\n"
     "        return time.time() - anchor > budget",
     "        anchor = agentop.note_dependency_wait_started()\n"
     "        return time.time() - anchor < budget",
     r'test_daemon_sidechannel_dependencies',
     'test_a_wait_past_the_budget_cascades'),
    # Writing the anchor before the settle is known not to have elapsed
    # costs two round trips on a path about to succeed, and falsifies
    # the column's documented meaning: this operation was never blocked.
    ('anchor written for a spent settle', SIDECHANNEL,
     "        finished = self._dependency_finished_at(dep, dep_state)\n"
     "        if time.time() - finished >= settle:\n"
     "            return SETTLE_SATISFIED",
     "        agentop.note_dependency_wait_started()\n"
     "        finished = self._dependency_finished_at(dep, dep_state)\n"
     "        if time.time() - finished >= settle:\n"
     "            return SETTLE_SATISFIED",
     r'test_daemon_sidechannel_dependencies',
     'test_an_elapsed_settle_does_not_write_the_anchor'),
    # A read which failed and a row which is absent are the same None
    # from mariadb.get_state(), and a depends_on edge acts irreversibly
    # on absence. Conflating them lets one database blip abort every
    # queued dependent on the node.
    ('failed read reads as absent', SIDECHANNEL,
     "        if not readable:\n"
     "            return False, None",
     "        if not readable:\n"
     "            return True, None",
     r'test_daemon_sidechannel_dependencies',
     'test_an_unreadable_dependency_does_not_cascade'),
    ('unreadable recorded as missing', SIDECHANNEL,
     "                        agentop, dep, DEPENDENCY_UNREADABLE,\n"
     "                        event=CASCADE_EVENT_GAVE_UP)\n"
     "                    return False, 'gave up trying to read a dependency'",
     "                        agentop, dep, DEPENDENCY_MISSING,\n"
     "                        event=CASCADE_EVENT_GAVE_UP)\n"
     "                    return False, 'gave up trying to read a dependency'",
     r'test_daemon_sidechannel_dependencies',
     'test_an_unreadable_dependency_past_the_budget_says_unreadable'),
    ('unreadable runs_after proceeds', SIDECHANNEL,
     "                return False, 'runs_after dependency state could not be read'",
     "                continue",
     r'test_daemon_sidechannel_dependencies',
     'test_an_unreadable_runs_after_dependency_blocks'),
    # Fail open on an unrecognised state is the one direction a fate
    # sharing edge must never fail in, and the failure is silent.
    ('unknown state satisfies depends_on', SIDECHANNEL,
     "            if dep_state != AgentOperation.STATE_COMPLETE:",
     "            if False:",
     r'test_daemon_sidechannel_dependencies',
     'test_an_unknown_dependency_state_blocks'),
    ('unknown state satisfies runs_after', SIDECHANNEL,
     "            if dep_state not in AgentOperation.TERMINAL_STATES:",
     "            if False:",
     r'test_daemon_sidechannel_dependencies',
     'test_an_unknown_runs_after_state_blocks'),
    # The derived classification test, which is what stops a state
    # added later reaching either arm above unnoticed.
    ('a state classified twice', SIDECHANNEL,
     "DEPENDENCY_PENDING_STATES = (AgentOperation.STATE_INITIAL,",
     "DEPENDENCY_PENDING_STATES = (AgentOperation.STATE_ERROR,\n"
     "                             AgentOperation.STATE_INITIAL,",
     r'test_daemon_sidechannel_dependencies',
     'test_every_agent_operation_state_is_classified'),
    # The only per-pass durable write either walk makes.
    ('warning not deduplicated', SIDECHANNEL,
     "        warned = (str(agentop.uuid), str(dep['op_uuid']), message)\n"
     "        if warned in self.runs_after_warnings:\n"
     "            return",
     "        warned = (str(agentop.uuid), str(dep['op_uuid']), message)\n"
     "        if False:\n"
     "            return",
     r'test_daemon_sidechannel_dependencies',
     'test_a_repeated_runs_after_warning_is_audited_once'),
    ('warning deduplicated per operation', SIDECHANNEL,
     "        warned = (str(agentop.uuid), str(dep['op_uuid']), message)",
     "        warned = (str(agentop.uuid), message)",
     r'test_daemon_sidechannel_dependencies',
     'test_a_second_runs_after_edge_warns_on_its_own_account'),
    # A retry is a new tenant of the queue head. Left in place, the
    # abandoned attempt's anchor is already past the budget.
    ('retry keeps the wait anchor', SIDECHANNEL,
     "    agentop.clear_dependency_wait()",
     "    pass",
     r'test_daemon_sidechannel_dependencies',
     'test_a_requeue_clears_the_wait_anchor'),
    # The dispatch seam. Evaluation is covered thoroughly in isolation;
    # these two are the wiring, which is where a one line regression
    # would not be seen by any test of the walk.
    ('refused verdict dispatches anyway', SIDECHANNEL,
     "                        if not dispatchable:",
     "                        if False:",
     r'test_daemon_sidechannel',
     'test_a_refused_verdict_dispatches_nothing'),
    ('superseded generation evaluates', SIDECHANNEL,
     "                        if generation != self.dispatcher_generation:\n"
     "                            # Replaced mid-pass (see above); the operation",
     "                        if False:\n"
     "                            # Replaced mid-pass (see above); the operation",
     r'test_daemon_sidechannel',
     'test_a_generation_replaced_mid_pass_never_evaluates'),
    # The two runs_after mutants make an ordering edge fate sharing,
    # which is the cluster side's refusal at
    # daemons/queues/workitem.py:175-210 reverted: an ordering hint
    # whose target has gone must not make an object unmanageable.
    ('runs_after missing warns', SIDECHANNEL,
     "                self._warn_runs_after(\n"
     "                    inst, agentop, dep, DEPENDENCY_MISSING,\n"
     "                    'warning, runs_after dependency is missing')\n"
     "                continue",
     "                self._cascade_abort(agentop, dep, DEPENDENCY_MISSING)\n"
     "                return False, 'dependency is missing'",
     r'test_daemon_sidechannel_dependencies',
     'test_a_missing_runs_after_dependency_dispatches_with_a_warning'),
    ('runs_after budget warns', SIDECHANNEL,
     "                    self._warn_runs_after(\n"
     "                        inst, agentop, dep, dep_state,\n"
     "                        'warning, gave up waiting for runs_after dependency')\n"
     "                    continue",
     "                    self._cascade_abort(\n"
     "                        agentop, dep, dep_state,\n"
     "                        event=CASCADE_EVENT_GAVE_UP)\n"
     "                    return False, 'gave up waiting'",
     r'test_daemon_sidechannel_dependencies',
     'test_a_runs_after_wait_past_the_budget_dispatches'),
    # Caching a non-terminal observation is the one way this cache can
    # be wrong: terminal is absorbing and nothing else is, so a cached
    # pending state means a blocked operation never unblocks.
    ('non-terminal caching', SIDECHANNEL,
     "        if value in AgentOperation.TERMINAL_STATES:\n"
     "            self._cache_terminal_dependency(op_uuid, value)",
     "        if True:\n"
     "            self._cache_terminal_dependency(op_uuid, value)",
     r'test_daemon_sidechannel_dependencies',
     'test_a_pending_dependency_is_not_cached'),
    # A blocked operation is re-evaluated every dispatch pass, so an
    # anchor which moved on each of them leaves a budget which can
    # never be exhausted. What this guards is not a lost write.
    ('wait anchor idempotence', AGENTOP,
     "        if attrs.dependency_wait_started is not None:\n"
     "            return attrs.dependency_wait_started",
     "        if False:\n"
     "            return attrs.dependency_wait_started",
     r'test_agent_operation_dependency_cascade',
     'test_the_wait_anchor_is_kept_across_later_calls'),
    # Both writes happen whichever way round they are made, so only
    # the order is observable: delete() removes the attribute rows and
    # only then assigns state, leaving no later moment at which the
    # reason could be filled in for a reader who already sees abort.
    ('cascade write order', AGENTOP,
     "        mariadb.update_agent_operation_attributes(\n"
     "            attrs, fields=['dependency_failure'])\n"
     "\n"
     "        self.state = BaseOperation.STATE_ABORT",
     "        self.state = BaseOperation.STATE_ABORT\n"
     "\n"
     "        mariadb.update_agent_operation_attributes(\n"
     "            attrs, fields=['dependency_failure'])",
     r'test_agent_operation_dependency_cascade',
     'test_a_cascade_writes_the_reason_before_the_state'),
]


def run(selector):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run(
        ['timeout', '600', '.tox/shared/bin/python', '-m', 'stestr', 'run',
         '--no-subunit-trace', selector],
        cwd=WT, env=env, capture_output=True, text=True)


def pristine(path):
    """Where this file's copy lives, named for its whole path.

    The basename is not unique -- a daemon's module is main.py whichever
    daemon it is -- and two files sharing a save name means one of them
    is restored from the other's contents. That happened: the database
    daemon came back holding the sidechannel daemon's module, every
    later mutant failed to import rather than failing a test, and the
    restoration check compared both files against the same copy and so
    reported both restored.
    """
    return os.path.join(SAVE, path.replace(os.sep, '_'))


def main():
    # Checked before anything is copied, because a collision here
    # destroys the copy it collides with.
    if len({pristine(p) for p in FILES}) != len(FILES):
        print('FILES does not map to distinct save names')
        return 1

    for path in FILES:
        shutil.copy2(os.path.join(WT, path), pristine(path))

    failures = []
    for (label, path, old, new, selector, phrase) in MUTANTS:
        full = os.path.join(WT, path)
        text = open(full).read()
        if text.count(old) != 1:
            print('SKIP  %-20s anchor matched %d times'
                  % (label, text.count(old)))
            failures.append(label)
            continue
        open(full, 'w').write(text.replace(old, new))
        try:
            result = run(selector)
            out = result.stdout + result.stderr
            killed = result.returncode != 0
            named = phrase in out
            print('%-5s %-20s rc=%-3s names-%s=%s'
                  % ('KILL' if killed and named else 'LIVE',
                     label, result.returncode, phrase[:34], named))
            if not (killed and named):
                failures.append(label)
        finally:
            shutil.copy2(pristine(path), full)

    print()
    for path in FILES:
        same = open(os.path.join(WT, path)).read() == open(
            pristine(path)).read()
        print('restored %-48s %s' % (path, same))
    print('\n%d/%d mutants killed' % (len(MUTANTS) - len(failures), len(MUTANTS)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
