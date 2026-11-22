# Copyright 2026 Michael Still and contributors

"""Tests for tools/measure-change-size.sh, the issue-fix size cap.

The issue-fix workflow refuses to publish a fix whose non-test diff exceeds
max_diff_lines. That cap is a safety control on an unattended agent with push
access, so the direction it fails in matters more than its happy path: an
over-count holds a fix back for a human, an under-count publishes one nobody
sized.

The script is a byte for byte copy of the one in shakenfist/development's
templates/issue-fix/, and these tests are ported from that repository's
scripts/tests/test_issue_fix_measurement.py. Each pins one of the rules in the
script's header -- fail closed, measure from the recorded base rather than
HEAD, read .gitattributes from the base, honour both spellings of
linguist-generated, respect linguist's opt-out -- none of which actionlint or
shellcheck can see. The seam between the script and the workflow is pinned in
test_issue_fix_workflow_seams.py.
"""

import os
import shutil
import subprocess
import tempfile

from shakenfist.tests import base


def _script_path():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, 'tools', 'measure-change-size.sh')


SCRIPT = _script_path()
TEST_PATH_PATTERN = '^tests/'

GIT_ENV = {
    'GIT_AUTHOR_NAME': 'test',
    'GIT_AUTHOR_EMAIL': 'test@example.com',
    'GIT_COMMITTER_NAME': 'test',
    'GIT_COMMITTER_EMAIL': 'test@example.com',
    'GIT_CONFIG_GLOBAL': os.devnull,
    'GIT_CONFIG_NOSYSTEM': '1',
}


class MeasureChangeSizeTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.repo = self._tempdir.name
        self.git('init', '-q', '-b', 'main')

    def git(self, *args):
        env = dict(os.environ)
        env.update(GIT_ENV)
        return subprocess.run(
            ['git'] + list(args), cwd=self.repo, env=env, check=True,
            capture_output=True, text=True).stdout.strip()

    def write(self, path, lines):
        """Write a file of the given number of distinct lines."""
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            if isinstance(lines, int):
                f.write(''.join('%s line %d\n' % (path, i)
                                for i in range(lines)))
            else:
                f.write(lines)

    def commit(self, message='base'):
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def measure(self, base, pattern=TEST_PATH_PATTERN, stage=True,
                path=None):
        """Run the measurement as the workflow does.

        Returns (stdout, stderr, exit status).
        """
        if stage:
            self.git('add', '-A')
        env = dict(os.environ)
        env.update(GIT_ENV)
        if path:
            env['PATH'] = path
        proc = subprocess.run(
            [SCRIPT, base, pattern], cwd=self.repo, env=env,
            capture_output=True, text=True, timeout=60)
        return proc.stdout.strip(), proc.stderr, proc.returncode

    def measure_with_git_shim(self, base, rule):
        """Measure with a git which applies rule to each argument first.

        rule is a line of bash run with $arg set to each argument in
        turn, ahead of handing the command to the real git.
        """
        self.git('add', '-A')
        real_git = shutil.which('git')
        with tempfile.TemporaryDirectory() as bindir:
            shim = os.path.join(bindir, 'git')
            with open(shim, 'w') as f:
                f.write('#!/bin/bash\n'
                        'for arg in "$@"; do\n'
                        '  %s\n'
                        'done\n'
                        'exec %s "$@"\n' % (rule, real_git))
            os.chmod(shim, 0o755)
            return self.measure(
                base, stage=False,
                path=bindir + os.pathsep + os.environ['PATH'])

    def count(self, base, **kwargs):
        out, err, status = self.measure(base, **kwargs)
        self.assertEqual(0, status, 'measurement failed: %s' % err)
        return int(out)

    def test_the_script_is_executable(self):
        # The workflow runs its copy directly, and git records the mode.
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_source_lines_are_counted(self):
        base = self.commit()
        self.write('src/a.py', 7)
        self.assertEqual(7, self.count(base))

    def test_additions_and_removals_both_count(self):
        self.write('src/a.py', 10)
        base = self.commit()
        self.write('src/a.py', 4)
        self.write('src/b.py', 3)
        self.assertEqual(6 + 3, self.count(base))

    def test_test_files_are_not_counted(self):
        base = self.commit()
        self.write('src/a.py', 2)
        self.write('tests/test_a.py', 50)
        self.assertEqual(2, self.count(base))

    def test_a_path_with_a_space_is_matched_as_a_whole(self):
        # numstat is tab separated. Splitting on any whitespace reads
        # "src/x tests/y.py" as "src/x", which the pattern below then
        # matches as if it were a test file.
        base = self.commit()
        self.write('src/x tests/y.py', 5)
        self.assertEqual(5, self.count(base, pattern='^tests/|^src/x$'))

    def test_binary_files_are_not_counted(self):
        base = self.commit()
        with open(os.path.join(self.repo, 'blob.bin'), 'wb') as f:
            f.write(b'\x00\x01\x02' * 100)
        self.write('src/a.py', 1)
        self.assertEqual(1, self.count(base))

    def test_no_change_is_zero(self):
        base = self.commit()
        self.assertEqual(0, self.count(base))

    def test_a_bare_linguist_generated_file_is_not_counted(self):
        self.write('.gitattributes', 'gen/* linguist-generated\n')
        base = self.commit()
        self.write('gen/stub_pb2.py', 1800)
        self.write('src/a.py', 3)
        self.assertEqual(3, self.count(base))

    def test_linguist_generated_true_is_not_counted_either(self):
        # GitHub documents the "=true" spelling. In git's pathspec magic
        # that is a string value, a different state from "set", so
        # "attr:linguist-generated" alone does not match it.
        self.write('.gitattributes', 'gen/* linguist-generated=true\n')
        base = self.commit()
        self.write('gen/stub_pb2.py', 1800)
        self.write('src/a.py', 3)
        self.assertEqual(3, self.count(base))

    def test_linguist_generated_false_is_still_counted(self):
        # linguist's opt-out: a file excluded by a broad rule and then
        # explicitly marked not generated is reviewable code.
        self.write('.gitattributes',
                   'gen/* linguist-generated\n'
                   'gen/handwritten.py linguist-generated=false\n')
        base = self.commit()
        self.write('gen/stub_pb2.py', 1800)
        self.write('gen/handwritten.py', 4)
        self.assertEqual(4, self.count(base))

    def test_a_fix_cannot_mark_its_own_code_generated(self):
        # The workflow stages everything with "git add -A", so the
        # model's .gitattributes is in both the working tree and the
        # index. Neither may count: only the base's does.
        base = self.commit()
        self.write('.gitattributes', 'src/* linguist-generated\n')
        self.write('src/big.py', 900)
        self.assertEqual(900 + 1, self.count(base))

    def test_a_fix_cannot_unmark_generated_code_either(self):
        # Unmarking would only over-count, so this is not a bypass, but
        # it shows the base is the only source of attributes: the
        # stubs stay uncounted and only the edit to .gitattributes is.
        self.write('.gitattributes', 'gen/* linguist-generated\n')
        base = self.commit()
        self.write('.gitattributes', '')
        self.write('gen/stub_pb2.py', 10)
        self.assertEqual(1, self.count(base))

    def test_a_commit_by_the_model_is_still_measured(self):
        # The prompt says not to commit; a model which did anyway would
        # have moved HEAD onto its work, and a diff against HEAD would
        # see only whatever it left uncommitted.
        base = self.commit()
        self.write('src/a.py', 300)
        self.commit('the model committed this')
        self.write('src/b.py', 2)
        self.assertEqual(302, self.count(base))

    def test_attributes_committed_by_the_model_are_ignored(self):
        # The same commit could carry a .gitattributes. HEAD would then
        # hold it, which is why the attributes come from the base.
        base = self.commit()
        self.write('.gitattributes', 'src/* linguist-generated\n')
        self.write('src/big.py', 900)
        self.commit('the model committed this')
        self.assertEqual(900 + 1, self.count(base))

    def test_a_git_without_attr_source_counts_generated_files(self):
        # git before 2.41 rejects --attr-source as an unknown option.
        # Falling back to counting generated files is the old behaviour
        # and can only over-count; it must say so rather than fail every
        # run, and must never measure zero.
        self.write('.gitattributes', 'gen/* linguist-generated\n')
        base = self.commit()
        self.write('gen/stub_pb2.py', 40)
        self.write('src/a.py', 3)
        out, err, status = self.measure_with_git_shim(
            base, 'case "$arg" in --attr-source*) exit 129 ;; esac')
        self.assertEqual(0, status, err)
        self.assertEqual('43', out)
        self.assertIn('::warning::', err)
        self.assertIn('2.41', err)

    def test_an_unknown_base_fails_closed(self):
        self.commit()
        self.write('src/a.py', 5)
        out, _, status = self.measure('0' * 40)
        self.assertNotEqual(0, status)
        self.assertEqual('', out, 'a failed measurement printed a count')

    def test_a_failing_git_fails_closed(self):
        # The regression this script exists for: inline, a git diff
        # which failed left awk to print 0 for its empty input, and a
        # fix of any size measured as no change. A shim ahead of the
        # real git on PATH fails "diff" alone, the way an unsupported
        # option or a bad pathspec would.
        base = self.commit()
        self.write('src/a.py', 5)
        out, _, status = self.measure_with_git_shim(
            base, '[ "$arg" = diff ] && exit 128')
        self.assertNotEqual(0, status)
        self.assertEqual('', out, 'a failed measurement printed a count')

    def test_outside_a_repository_fails_closed(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            proc = subprocess.run(
                [SCRIPT, 'HEAD', TEST_PATH_PATTERN], cwd=elsewhere,
                capture_output=True, text=True, timeout=60,
                env=dict(os.environ, GIT_CEILING_DIRECTORIES=elsewhere))
        self.assertNotEqual(0, proc.returncode)
        self.assertEqual('', proc.stdout.strip())

    def test_the_wrong_number_of_arguments_is_an_error(self):
        proc = subprocess.run(
            [SCRIPT, 'HEAD'], cwd=self.repo, capture_output=True,
            text=True, timeout=60)
        self.assertEqual(2, proc.returncode)
        self.assertEqual('', proc.stdout.strip())

    def test_it_measures_from_a_subdirectory(self):
        # The workflow runs from the repository root, but a pathspec of
        # "." would quietly measure only the current directory.
        base = self.commit()
        self.write('src/a.py', 3)
        self.write('docs/b.md', 4)
        self.git('add', '-A')
        env = dict(os.environ)
        env.update(GIT_ENV)
        proc = subprocess.run(
            [SCRIPT, base, TEST_PATH_PATTERN],
            cwd=os.path.join(self.repo, 'src'), env=env,
            capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual('7', proc.stdout.strip())
