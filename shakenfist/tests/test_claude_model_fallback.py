# Copyright 2026 Michael Still and contributors

"""Tests for tools/claude-model-fallback.sh, the headless model wrapper.

Only the prompt delivery is tested here, because that is the part with a
failure mode that is silent in both directions. A prompt passed as a claude
argument is capped by the kernel at MAX_ARG_STRLEN -- a hard 128KiB no ulimit
raises -- and an argument over it does not truncate: the exec fails with
E2BIG and no model runs at all. The caller sees an empty answer and blames
the model. `--prompt-file` hands the prompt to claude on stdin instead, and
the second silent failure lives there: this wrapper may run claude more than
once as it falls back through the model list, and a prompt delivered as a
stream rather than a path is drained by the first attempt, leaving the
fallback model with nothing.

The wrapper is run for real with `claude` replaced by a stub on PATH which
records the arguments and the stdin it was given.

Needs jq and bash, which both runner flavours install as base packages.
"""

import json
import os
import shutil
import subprocess
import tempfile

from shakenfist.tests import base


# A stub claude. Records how it was called, answers in the JSON shape the
# wrapper parses, and reports the requested models as out of subscription
# credit -- an HTTP 429 -- when OUT_OF_CREDIT names them.
CLAUDE_STUB = '''#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
model = args[args.index('--model') + 1]
prompt = sys.stdin.read()

with open(os.environ['STUB_RECORD'], 'a') as f:
    f.write(json.dumps({'model': model, 'args': args, 'stdin': prompt}) + '\\n')

if model in os.environ.get('OUT_OF_CREDIT', '').split(','):
    print(json.dumps({
        'is_error': True, 'total_cost_usd': 0, 'api_error_status': 429,
        'result': "You've reached your %s limit." % model}))
    sys.exit(0)

print(json.dumps({
    'is_error': False, 'api_error_status': None,
    'result': 'the model saw %d bytes' % len(prompt)}))
'''

PROMPT = 'Triage this failure.\n' + ('x' * 200000)


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


class ClaudeModelFallbackTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.assertIsNotNone(
            shutil.which('jq'),
            'these tests run the model wrapper, which needs jq; both runner '
            'flavours install it as a base package')

        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)

        self.wrapper = os.path.join(
            _repo_root(), 'tools', 'claude-model-fallback.sh')
        self.record = os.path.join(self.tempdir, 'calls.jsonl')
        self.prompt_file = os.path.join(self.tempdir, 'prompt.txt')
        with open(self.prompt_file, 'w') as f:
            f.write(PROMPT)

        self.bin = os.path.join(self.tempdir, 'bin')
        os.makedirs(self.bin)
        stub = os.path.join(self.bin, 'claude')
        with open(stub, 'w') as f:
            f.write(CLAUDE_STUB)
        os.chmod(stub, 0o755)

    def run_wrapper(self, args, out_of_credit=''):
        environment = dict(os.environ)
        environment.update({
            'PATH': '%s:%s' % (self.bin, os.environ['PATH']),
            'STUB_RECORD': self.record,
            'OUT_OF_CREDIT': out_of_credit
        })
        return subprocess.run(
            [self.wrapper] + args, capture_output=True, text=True,
            env=environment, stdin=subprocess.DEVNULL)

    def calls(self):
        if not os.path.exists(self.record):
            return []
        with open(self.record) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_a_prompt_file_reaches_the_model_on_stdin(self):
        # And not in the argument vector, which is where the kernel's 128KiB
        # cap on a single argument applies.
        proc = self.run_wrapper([
            '--models', 'model-a', '--prompt-file', self.prompt_file,
            '--', '--output-format', 'text'])
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn('the model saw %d bytes' % len(PROMPT), proc.stdout)

        call = self.calls()[0]
        self.assertEqual(PROMPT, call['stdin'])
        for arg in call['args']:
            self.assertNotIn('Triage this failure', arg)

    def test_the_fallback_model_gets_the_prompt_too(self):
        # The reason the option takes a path and the redirect is made again
        # for each attempt: one stream shared by the whole loop is drained by
        # the first model, and the fallback then runs against an empty prompt
        # and answers confidently about nothing.
        proc = self.run_wrapper(
            ['--models', 'model-a,model-b', '--prompt-file', self.prompt_file,
             '--quiet', '--', '--output-format', 'text'],
            out_of_credit='model-a')
        self.assertEqual(0, proc.returncode, proc.stderr)

        calls = self.calls()
        self.assertEqual(['model-a', 'model-b'], [c['model'] for c in calls])
        for call in calls:
            self.assertEqual(PROMPT, call['stdin'])

    def test_an_unreadable_prompt_file_is_a_usage_error(self):
        # Rather than a model run with an empty prompt, which is the failure
        # this option exists to prevent.
        proc = self.run_wrapper([
            '--models', 'model-a', '--prompt-file',
            os.path.join(self.tempdir, 'no-such-file'),
            '--', '--output-format', 'text'])
        self.assertEqual(2, proc.returncode)
        self.assertIn('cannot be read', proc.stderr)
        self.assertEqual([], self.calls())
