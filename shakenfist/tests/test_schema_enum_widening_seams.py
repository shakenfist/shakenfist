# Copyright 2026 Michael Still and contributors

"""The seams around the Schema ENUM widening job.

That job is the only place the live MariaDB modules
(``shakenfist/tests/test_mariadb_*_live.py``) run, because they need a real
MariaDB server rather than the sqlite engine the unit suite uses. It used to
run on ``merge_group`` and ``workflow_dispatch`` only, which meant a change to
a schema migration met a real server for the first time *after* it had been
reviewed, approved and queued: #4954 added a UNIQUE constraint over a column a
pre-existing live test drops to roll its table back, passed its pull request
CI, and failed the merge group -- ejecting the entry and cancelling every
functional lane in the group (issue #5005).

It now also runs on a pull request whose diff touches what those modules
cover. That makes the ``live_mariadb`` paths filter load bearing in a way a
paths filter usually is not: a live module the filter does not match is a
module whose pull request CI is silently back to not running it, and the
symptom is an ejected merge group weeks later rather than an error. These
tests hold the filter to the files the job actually runs, and hold the gate's
two arms apart -- the pull request arm is deliberately narrower than the whole
code change, and the merge arm must never be.
"""

import ast
import glob
import os
import re

import yaml

from shakenfist.tests import base


WORKFLOW = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    '.github', 'workflows', 'functional-tests.yml')

JOB = 'schema_enum_widening'
FILTER_STEP = 'live_filter'
FILTER_NAME = 'live_mariadb'
OUTPUT = 'live_mariadb_changed'

# The repository-relative paths of everything the job runs which is not a
# live test module: the two scripts and the bootstrap SQL its steps name.
# Kept explicit rather than scraped from the step text, so that moving one
# fails here with the path in the message.
JOB_SUPPORT_FILES = (
    'tools/ci-enum-widening-test.sh',
    'tools/ci-install-mariadb.sh',
    'tools/bootstrap-mariadb.sql',
)

# In-repo modules the live modules import which the filter deliberately does
# not match, each with the reason. Every other in-repo import must be
# matched: a shared helper changes what a live test asserts as surely as the
# test module does.
DELIBERATELY_UNFILTERED = {
    # The generic unit test harness. It changes often for reasons unrelated
    # to MariaDB, and would run the job on most test-touching pull requests.
    'shakenfist/tests/base.py': 'generic test harness',
    # Plain constant and exception definitions; a change which alters live
    # behaviour through one of these also touches mariadb.py or schema/.
    'shakenfist/constants.py': 'constants',
    'shakenfist/exceptions.py': 'exception classes',
}

# The glob the live test script hands stestr, as a shell path rather than as
# the module regex 'shakenfist\.tests\.test_mariadb_.*_live\.' it is written
# as in tools/ci-enum-widening-test.sh. Change them together.
LIVE_MODULE_GLOB = 'shakenfist/tests/test_mariadb_*_live.py'


def _as_regex(pattern):
    """A dorny/paths-filter glob as a regex over a repository-relative path.

    Only the two constructs the filter uses: '**' crosses directory
    separators and '*' does not. Written out rather than reached for through
    fnmatch, which does not distinguish the two, or PurePath.full_match,
    which is 3.13 and later.
    """
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith('**', i):
            out.append('.*')
            i += 2
        elif pattern[i] == '*':
            out.append('[^/]*')
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile('^%s$' % ''.join(out))


class SchemaEnumWideningSeamsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        with open(WORKFLOW) as f:
            self.workflow = yaml.safe_load(f)
        self.jobs = self.workflow['jobs']
        # .github/workflows/functional-tests.yml, three levels up.
        self.repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(WORKFLOW)))

    def _filter_patterns(self):
        steps = self.jobs['check_paths']['steps']
        step = [s for s in steps if s.get('id') == FILTER_STEP]
        self.assertEqual(
            1, len(step),
            'check_paths has no step with id %r, so nothing computes the '
            '%s filter the %s gate reads' % (FILTER_STEP, FILTER_NAME, JOB))
        filters = yaml.safe_load(step[0]['with']['filters'])
        self.assertIn(FILTER_NAME, filters)
        return filters[FILTER_NAME]

    def test_the_filter_matches_every_live_test_module(self):
        # The property the gate rests on. A live module the filter does not
        # match runs in the merge queue only, which is the state #5005 was
        # filed about, and nothing else would notice.
        patterns = [_as_regex(p) for p in self._filter_patterns()]
        modules = sorted(
            os.path.relpath(p, self.repo_root)
            for p in glob.glob(os.path.join(self.repo_root, LIVE_MODULE_GLOB)))
        self.assertNotEqual(
            [], modules,
            'No live test modules found at %s, so this test is vacuous'
            % LIVE_MODULE_GLOB)
        unmatched = [m for m in modules
                     if not any(r.match(m) for r in patterns)]
        self.assertEqual(
            [], unmatched,
            'These live MariaDB test modules are not matched by the %s paths '
            'filter, so a pull request changing one would not run them: %s'
            % (FILTER_NAME, ', '.join(unmatched)))

    def test_the_filter_matches_the_files_the_job_runs(self):
        patterns = [_as_regex(p) for p in self._filter_patterns()]
        for path in JOB_SUPPORT_FILES:
            self.assertTrue(
                os.path.exists(os.path.join(self.repo_root, path)),
                '%s no longer exists, so the %s filter names a path which '
                'cannot match' % (path, FILTER_NAME))
            self.assertTrue(
                any(r.match(path) for r in patterns),
                '%s is run by the %s job but is not matched by the %s paths '
                'filter, so changing it would not run the job'
                % (path, JOB, FILTER_NAME))

    def _live_module_imports(self):
        """Every in-repo module a live module imports, as a repository path.

        ``from shakenfist.x import y`` resolves to ``shakenfist/x/y.py`` when
        that is a module and to ``shakenfist/x.py`` (or its package
        ``__init__.py``) when ``y`` is a name inside it.
        """
        def module_path(dotted):
            base_path = os.path.join(*dotted.split('.'))
            for candidate in (base_path + '.py',
                              os.path.join(base_path, '__init__.py')):
                if os.path.exists(os.path.join(self.repo_root, candidate)):
                    return candidate
            return None

        found = {}
        for path in glob.glob(os.path.join(self.repo_root, LIVE_MODULE_GLOB)):
            relative = os.path.relpath(path, self.repo_root)
            with open(path) as f:
                tree = ast.parse(f.read(), filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and \
                        node.module.split('.')[0] == 'shakenfist':
                    for alias in node.names:
                        target = (module_path('%s.%s' % (node.module, alias.name))
                                  or module_path(node.module))
                        self.assertIsNotNone(
                            target, '%s imports %s from %s, which does not '
                            'resolve to a file' % (relative, alias.name, node.module))
                        found.setdefault(target, set()).add(relative)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split('.')[0] == 'shakenfist':
                            target = module_path(alias.name)
                            self.assertIsNotNone(target, alias.name)
                            found.setdefault(target, set()).add(relative)
        return found

    def test_the_filter_matches_every_in_repo_module_the_live_tests_import(self):
        # A shared helper such as hard_delete_cases.py decides what a live
        # test asserts. One outside the filter is the silent class this file
        # exists to close: changing it would not run the tests it changes.
        patterns = [_as_regex(p) for p in self._filter_patterns()]
        imports = self._live_module_imports()
        self.assertIn(
            'shakenfist/mariadb.py', imports,
            'The import scrape found no live module importing mariadb.py, '
            'so it is probably not reading the imports at all')
        unmatched = sorted(
            '%s (imported by %s)' % (target, ', '.join(sorted(importers)))
            for target, importers in imports.items()
            if target not in DELIBERATELY_UNFILTERED
            and not any(r.match(target) for r in patterns))
        self.assertEqual(
            [], unmatched,
            'These in-repo modules are imported by live MariaDB tests but '
            'are not matched by the %s paths filter, so a pull request '
            'changing one would not run the tests it affects. Add each to '
            'the filter, or to DELIBERATELY_UNFILTERED with the reason: %s'
            % (FILTER_NAME, '; '.join(unmatched)))

    def test_every_deliberate_exclusion_is_still_imported(self):
        # An exclusion for a module nothing imports any more is a stale
        # reason waiting to excuse something else.
        imports = self._live_module_imports()
        stale = sorted(set(DELIBERATELY_UNFILTERED) - set(imports))
        self.assertEqual(
            [], stale,
            'DELIBERATELY_UNFILTERED names modules no live test imports: %s'
            % ', '.join(stale))

    def test_the_pull_request_run_gates_enqueue(self):
        # The pull request arm only helps if its result stops the pull
        # request being enqueued. can_enqueue is what the ruleset requires,
        # so the job has to be among the things it consumes; otherwise a red
        # run is a visible signal and nothing more, and the merge group
        # still ejects the entry (issue #5005).
        self.assertIn(
            JOB, self.jobs['can_enqueue']['needs'],
            'can_enqueue does not need %s, so a failing pull request run of '
            'it does not block enqueue' % JOB)

    def test_check_paths_exports_the_filter(self):
        outputs = self.jobs['check_paths']['outputs']
        self.assertIn(
            OUTPUT, outputs,
            'check_paths does not export %s, so the %s gate reads an empty '
            'string and never runs on a pull request' % (OUTPUT, JOB))
        self.assertIn(
            '%s.outputs.%s' % (FILTER_STEP, FILTER_NAME), outputs[OUTPUT],
            'check_paths exports %s from something other than the %s step'
            % (OUTPUT, FILTER_STEP))
        # The fallback every other output in this job carries: the filter
        # step does not run on workflow_dispatch, where there is no diff to
        # read, and an unset output must mean "run it" rather than "skip it".
        self.assertIn("|| 'true'", outputs[OUTPUT])

    def test_the_gate_runs_on_a_pull_request_touching_the_live_tests(self):
        gate = ' '.join(self.jobs[JOB]['if'].split())
        self.assertIn(
            "github.event_name == 'pull_request'", gate,
            'The %s gate has no pull_request arm, so the live MariaDB tests '
            'first run in the merge queue (issue #5005)' % JOB)
        self.assertIn(
            'needs.check_paths.outputs.%s' % OUTPUT, gate,
            'The %s gate does not consult %s, so it would run on every pull '
            'request or on none' % (JOB, OUTPUT))

    def test_the_merge_arm_is_not_narrowed_by_the_filter(self):
        # can_merge excuses a skipped dependency only when check_paths found
        # no code change at all, so gating the merge arm on the narrower
        # filter would fail every code-changing merge group which did not
        # touch the schema. The merge arm stays on code_changed.
        gate = ' '.join(self.jobs[JOB]['if'].split())
        merge_arm = gate.split("|| (github.event_name == 'pull_request'")[0]
        self.assertIn("github.event_name == 'merge_group'", merge_arm)
        self.assertIn('needs.check_paths.outputs.code_changed', merge_arm)
        self.assertNotIn(
            OUTPUT, merge_arm,
            'The merge_group arm of the %s gate consults %s. The merge gate '
            'must not be narrower than the whole code change' % (JOB, OUTPUT))

    def test_the_glob_translation_distinguishes_a_star_from_two(self):
        # _as_regex is the only thing standing between these tests and a
        # pattern they silently mis-read, so it is held directly.
        self.assertTrue(_as_regex('shakenfist/schema/**').match(
            'shakenfist/schema/operations/create.py'))
        self.assertTrue(_as_regex('shakenfist/mariadb_*.py').match(
            'shakenfist/mariadb_hard_delete.py'))
        self.assertFalse(_as_regex('shakenfist/mariadb_*.py').match(
            'shakenfist/sub/mariadb_hard_delete.py'))
        self.assertFalse(_as_regex('shakenfist/schema/*').match(
            'shakenfist/schema/operations/create.py'))
        self.assertFalse(_as_regex('tools/ci-install-mariadb.sh').match(
            'tools/ci-install-mariadb-x.sh'))
