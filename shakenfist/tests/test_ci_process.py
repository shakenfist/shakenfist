# Copyright 2026 Michael Still and contributors
import importlib.util
import os

from shakenfist.tests import base


# The functional CI suite is a client of the cluster and is not
# importable from here, but process.py deliberately imports nothing
# beyond the standard library so its logic can be loaded from source and
# covered by the unit suite -- the same arrangement safe_headers uses.
MODULE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'shakenfist_ci',
    'process.py'))


def _load_process():
    spec = importlib.util.spec_from_file_location('ci_process', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CiProcessTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.process = _load_process()

    def test_a_command_returns_its_output(self):
        out, err = self.process.execute('echo', 'hello')
        self.assertEqual('hello\n', out)
        self.assertEqual('', err)

    def test_arguments_are_not_shell_interpreted(self):
        """Without shell=True the argv is passed through untouched."""
        out, _ = self.process.execute('echo', 'a b; echo c')
        self.assertEqual('a b; echo c\n', out)

    def test_a_shell_command_is_interpreted(self):
        out, _ = self.process.execute('echo one && echo two', shell=True)
        self.assertEqual('one\ntwo\n', out)

    def test_shell_refuses_more_than_one_argument(self):
        self.assertRaises(ValueError, self.process.execute,
                          'echo', 'hello', shell=True)

    def test_stderr_is_captured_separately(self):
        out, err = self.process.execute(
            'sh', '-c', 'echo to-out; echo to-err >&2')
        self.assertEqual('to-out\n', out)
        self.assertEqual('to-err\n', err)

    def test_output_is_not_stripped(self):
        """Callers split on newlines and parse JSON; trailing bytes matter."""
        out, _ = self.process.execute('printf', 'a\nb\n')
        self.assertEqual('a\nb\n', out)

    def test_undecodable_output_does_not_raise(self):
        """A stray byte must not lose the test to a UnicodeDecodeError."""
        out, _ = self.process.execute('sh', '-c', "printf '\\377'")
        # os.fsdecode maps the undecodable byte to a surrogate rather
        # than raising, which is what a strict decode would have done.
        self.assertEqual('\udcff', out)

    def test_a_nonzero_exit_raises(self):
        e = self.assertRaises(
            self.process.ProcessExecutionError,
            self.process.execute, 'sh', '-c', 'exit 7')
        self.assertEqual(7, e.exit_code)

    def test_an_accepted_exit_code_does_not_raise(self):
        out, _ = self.process.execute(
            'sh', '-c', 'echo hi; exit 1', check_exit_code=[0, 1])
        self.assertEqual('hi\n', out)

    def test_check_exit_code_false_accepts_anything(self):
        _, _ = self.process.execute(
            'sh', '-c', 'exit 42', check_exit_code=False)

    def test_the_error_carries_the_output(self):
        e = self.assertRaises(
            self.process.ProcessExecutionError, self.process.execute,
            'sh', '-c', 'echo out; echo err >&2; exit 2')
        self.assertEqual('out\n', e.stdout)
        self.assertEqual('err\n', e.stderr)
        self.assertIn('Exit code: 2', str(e))

    def test_stdin_is_not_the_test_runners(self):
        """ssh reads stdin; inheriting the runner's would block."""
        out, _ = self.process.execute('cat')
        self.assertEqual('', out)

    def test_a_credential_flag_is_masked_in_the_error(self):
        """The backstop for a credential which reached a command line."""
        e = self.assertRaises(
            self.process.ProcessExecutionError, self.process.execute,
            'sf-client --key SUPERSECRET network create; exit 1', shell=True)
        self.assertNotIn('SUPERSECRET', str(e))
        self.assertIn('--key ***', str(e))

    def test_masking_covers_the_usual_flags(self):
        for flag in ('--key', '--password', '--passwd', '--token', '--secret'):
            for form in ('%s VALUE', '%s=VALUE'):
                rendered = self.process.mask_secrets('cmd ' + form % flag)
                self.assertNotIn('VALUE', rendered, rendered)

    def test_masking_leaves_ordinary_arguments_alone(self):
        self.assertEqual(
            'cmd --apiurl http://x network create',
            self.process.mask_secrets('cmd --apiurl http://x network create'))

    def test_masking_covers_hyphenated_and_prefixed_flags(self):
        """--ssh-key and friends are the shape a caller actually writes."""
        for flag in ('--ssh-key', '--api-key', '--namespace-key',
                     '--auth-token', '--client-secret'):
            for form in ('%s VALUE', '%s=VALUE'):
                rendered = self.process.mask_secrets('cmd ' + form % flag)
                self.assertNotIn('VALUE', rendered, rendered)

    def test_masking_does_not_cover_short_options(self):
        """The documented hole, pinned so it is not mistaken for coverage.

        A single-letter option carries no evidence that its value is a
        credential -- -k is --insecure to curl. This is why the namespace
        key goes to sf-client through the environment rather than argv,
        and why mask_secrets() is described as a backstop.
        """
        self.assertEqual('cmd -k SECRET',
                         self.process.mask_secrets('cmd -k SECRET'))

    def test_the_error_masks_the_output_streams_too(self):
        """A command which echoes its own argv must not undo the masking."""
        e = self.assertRaises(
            self.process.ProcessExecutionError, self.process.execute,
            'echo "ran --token SUPERSECRET" >&2; exit 1', shell=True)
        self.assertNotIn('SUPERSECRET', e.stderr)
        self.assertIn('--token ***', e.stderr)

    def test_check_exit_code_accepts_a_bare_int(self):
        """oslo took an int here, so a caller eventually will too."""
        out, _ = self.process.execute(
            'sh', '-c', 'echo hi; exit 3', check_exit_code=3)
        self.assertEqual('hi\n', out)

    def test_a_bare_int_still_rejects_other_codes(self):
        e = self.assertRaises(
            self.process.ProcessExecutionError, self.process.execute,
            'sh', '-c', 'exit 4', check_exit_code=3)
        self.assertEqual(4, e.exit_code)

    def test_env_is_laid_over_the_environment_not_a_replacement(self):
        """The commands run here need PATH; only the additions are new."""
        out, _ = self.process.execute(
            'sh', '-c', 'echo "$SF_TEST_VALUE"; echo "${PATH:+haspath}"',
            env={'SF_TEST_VALUE': 'from-env'})
        self.assertEqual('from-env\nhaspath\n', out)

    def test_env_does_not_leak_into_this_process(self):
        self.process.execute('true', env={'SF_TEST_VALUE': 'from-env'})
        self.assertIsNone(os.environ.get('SF_TEST_VALUE'))

    def test_a_command_which_overruns_its_timeout_is_killed(self):
        e = self.assertRaises(
            self.process.ProcessTimeoutError, self.process.execute,
            'sleep', '30', timeout=0.2)
        self.assertIsNone(e.exit_code)
        self.assertIn('did not complete within', str(e))

    def test_a_timeout_is_a_process_execution_error(self):
        """So the handlers which already skip on exec failure catch it."""
        self.assertTrue(issubclass(self.process.ProcessTimeoutError,
                                   self.process.ProcessExecutionError))

    def test_a_timeout_fires_regardless_of_check_exit_code(self):
        """There is no exit code to accept when the command was killed."""
        self.assertRaises(
            self.process.ProcessTimeoutError, self.process.execute,
            'sleep', '30', timeout=0.2, check_exit_code=False)
