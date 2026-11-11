#!/usr/bin/env python3
# Copyright 2019 Michael Still and contributors
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
# silently revert a concurrent edit to either of the two files it
# touches -- do not run it while something else is working in the same
# worktree. The same hazard was recorded against
# tools/mutate-nested-sweep.sh by an earlier push audit.
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

# (label, file, old, new, test selector, phrase the failure must name)
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
     "                    > config.AGENT_OPERATION_MAX_DEADLINE):",
     "            if False:",
     r'external_api\.test_agent_operation_parameters',
     'settle_ceiling_is_enforced_by_the_handler_in_warn_mode'),
    ('TypeError phrasing', BASE,
     "    if isinstance(error, TypeError):\n        return 'not an object'",
     "    if False:\n        return 'not an object'",
     r'external_api\.test_agent_operation_parameters',
     'does_not_name_an_internal_constructor'),
]


def run(selector):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run(
        ['timeout', '600', '.tox/shared/bin/python', '-m', 'stestr', 'run',
         '--no-subunit-trace', selector],
        cwd=WT, env=env, capture_output=True, text=True)


def main():
    for path in (DEP, BASE):
        shutil.copy2(os.path.join(WT, path),
                     os.path.join(SAVE, os.path.basename(path)))

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
            shutil.copy2(os.path.join(SAVE, os.path.basename(path)), full)

    print()
    for path in (DEP, BASE):
        same = open(os.path.join(WT, path)).read() == open(
            os.path.join(SAVE, os.path.basename(path))).read()
        print('restored %-48s %s' % (path, same))
    print('\n%d/%d mutants killed' % (len(MUTANTS) - len(failures), len(MUTANTS)))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
