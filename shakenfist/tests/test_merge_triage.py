# Copyright 2026 Michael Still and contributors

"""Tests for tools/merge-triage.py.

The merge failure triage job produces one small JSON document per failed merge
group run, and two consumers depend on it being right. A human reads the
rendered comment on the ejected pull request, and the private-ci conductor
reads the document itself to track which merge failures have been triaged and
which of them blamed the pull request. Both are worse off with a confidently
wrong document than with no document, which is what these tests pin.

The cases below are the ones where a naive implementation gets it wrong: a
model which illustrates the format before filling it in (so the last object
wins, not the first), a model which puts its own idea of the run id in the
document (so the envelope wins, always), a model which answers in prose (so a
fallback verdict is written rather than nothing at all), and the several ways
a field arrives in nearly but not quite the right shape.
"""

import json
import os
import subprocess
import sys
import tempfile

from shakenfist.tests import base


def _script_path():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, 'tools', 'merge-triage.py')


SCRIPT = _script_path()

ENVELOPE = {
    'repository': 'shakenfist/shakenfist',
    'run_id': 12345,
    'run_url': 'https://github.com/shakenfist/shakenfist/actions/runs/12345',
    'run_attempt': 1,
    'head_branch': 'gh-readonly-queue/develop/pr-4067-abcdef0',
    'head_sha': 'abcdef0',
    'base_branch': 'develop',
    'pull_request': 4067,
    'triage_run_url': 'https://github.com/shakenfist/shakenfist/actions/runs/12346'
}

VERDICT = {
    'verdict': 'systemic',
    'confidence': 'high',
    'summary': 'The cluster build failed before any test ran.',
    'failing_job': 'develop debian 12 cluster (collection)',
    'failing_step': 'Build the smoke cluster',
    'failure_signature': 'no SSH prompt from sf2',
    'recommendation': 'requeue',
    'tracking_issue': 3813,
    'tracking_issue_action': 'commented',
    'evidence': ['The pull request touches only docs/.']
}


class MergeTriageTestCase(base.ShakenFistTestCase):
    def _extract(self, response, envelope=None):
        """Run the extractor over a response, returning (exit code, document)."""
        with tempfile.TemporaryDirectory() as tempdir:
            response_path = os.path.join(tempdir, 'response.txt')
            envelope_path = os.path.join(tempdir, 'envelope.json')
            output_path = os.path.join(tempdir, 'triage.json')

            with open(response_path, 'w') as f:
                f.write(response)
            with open(envelope_path, 'w') as f:
                json.dump(envelope if envelope is not None else ENVELOPE, f)

            proc = subprocess.run(
                [sys.executable, SCRIPT, 'extract', response_path, envelope_path, output_path],
                capture_output=True, text=True)

            # The document is always written, because a triage which reached
            # nothing still has to be distinguishable from a triage which never
            # ran.
            self.assertTrue(os.path.exists(output_path), 'no document was written')
            with open(output_path) as f:
                return proc.returncode, json.load(f)

    def _validate(self, document):
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, 'triage.json')
            with open(path, 'w') as f:
                json.dump(document, f)
            return subprocess.run(
                [sys.executable, SCRIPT, 'validate', path],
                capture_output=True, text=True).returncode

    def _render(self, document):
        with tempfile.TemporaryDirectory() as tempdir:
            source = os.path.join(tempdir, 'triage.json')
            rendered = os.path.join(tempdir, 'comment.md')
            with open(source, 'w') as f:
                json.dump(document, f)
            proc = subprocess.run(
                [sys.executable, SCRIPT, 'render', source, rendered],
                capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, proc.stderr)
            with open(rendered) as f:
                return f.read()

    def test_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_fenced_verdict_is_extracted(self):
        code, document = self._extract(
            'Here is what I found.\n\n```json\n%s\n```\n' % json.dumps(VERDICT))
        self.assertEqual(0, code)
        self.assertEqual('systemic', document['verdict'])
        self.assertEqual(3813, document['tracking_issue'])
        self.assertEqual(0, self._validate(document))

    def test_unfenced_verdict_is_extracted(self):
        # Models drop the fence often enough that requiring it would throw
        # away good verdicts.
        code, document = self._extract('My verdict:\n\n%s\n' % json.dumps(VERDICT))
        self.assertEqual(0, code)
        self.assertEqual('systemic', document['verdict'])

    def test_last_object_wins(self):
        # The prompt shows the format before asking for it, and a model which
        # echoes the illustration puts the real answer last.
        illustration = dict(VERDICT, verdict='pr_caused', summary='illustration')
        code, document = self._extract(
            'Format:\n```json\n%s\n```\nAnswer:\n```json\n%s\n```\n'
            % (json.dumps(illustration), json.dumps(VERDICT)))
        self.assertEqual(0, code)
        self.assertEqual('systemic', document['verdict'])
        self.assertEqual('The cluster build failed before any test ran.', document['summary'])

    def test_envelope_always_wins(self):
        # A model which misremembers the run id or the pull request number must
        # not be able to file a verdict against somebody else's failure.
        lying = dict(VERDICT, repository='someone/else', run_id=1, pull_request=1,
                     run_url='https://example.com/', schema_version=99,
                     invented_field='this should not survive')
        code, document = self._extract('```json\n%s\n```' % json.dumps(lying))
        self.assertEqual(0, code)
        self.assertEqual('shakenfist/shakenfist', document['repository'])
        self.assertEqual(12345, document['run_id'])
        self.assertEqual(4067, document['pull_request'])
        self.assertEqual(ENVELOPE['run_url'], document['run_url'])

        # Two separate mechanisms defend that, and this pins the other one:
        # only the fields in MODEL_FIELDS are taken from the response at all,
        # so a key the schema has never heard of cannot ride along into a
        # document the conductor parses.
        self.assertEqual(1, document['schema_version'])
        self.assertNotIn('invented_field', document)

    def test_prose_answer_becomes_an_unknown_verdict(self):
        code, document = self._extract(
            'I think the cluster fell over, but I could not read the logs.')
        self.assertEqual(1, code)
        self.assertEqual('unknown', document['verdict'])
        self.assertEqual('investigate', document['recommendation'])
        self.assertIsNotNone(document['error'])
        self.assertEqual(12345, document['run_id'])
        self.assertEqual(0, self._validate(document))

    def test_unusable_verdict_value_becomes_unknown(self):
        # "maybe" is not one of the verdicts, and guessing which one it meant
        # is how a wrong verdict gets published with confidence.
        code, document = self._extract(
            '```json\n%s\n```' % json.dumps(dict(VERDICT, verdict='maybe')))
        self.assertEqual(1, code)
        self.assertEqual('unknown', document['verdict'])
        self.assertIn('no usable verdict', document['error'])

    def test_self_declared_unknown_is_a_fallback_not_a_verdict(self):
        code, document = self._extract(
            '```json\n%s\n```' % json.dumps(dict(VERDICT, verdict='unknown')))
        self.assertEqual(1, code)
        self.assertEqual('unknown', document['verdict'])
        self.assertIsNotNone(document['error'])

    def test_missing_recommendation_is_derived(self):
        verdict = dict(VERDICT, verdict='pr_caused')
        del verdict['recommendation']
        code, document = self._extract('```json\n%s\n```' % json.dumps(verdict))
        self.assertEqual(0, code)
        self.assertEqual('fix_first', document['recommendation'])

    def test_field_shapes_are_coerced(self):
        # Each of these has turned up in real model output for the reviewer:
        # an issue reference written the way a human writes it, a single
        # evidence string rather than a list, and a capitalised enum.
        code, document = self._extract('```json\n%s\n```' % json.dumps(dict(
            VERDICT, tracking_issue='#3813', evidence='One observation.',
            confidence='High')))
        self.assertEqual(0, code)
        self.assertEqual(3813, document['tracking_issue'])
        self.assertEqual(['One observation.'], document['evidence'])
        self.assertEqual('high', document['confidence'])

    def test_rendered_comment_embeds_the_document(self):
        code, document = self._extract('```json\n%s\n```' % json.dumps(VERDICT))
        self.assertEqual(0, code)

        rendered = self._render(document)
        self.assertIn('Systemic failure', rendered)
        self.assertIn('Re-queue as-is.', rendered)
        self.assertIn('#3813', rendered)

        # The conductor can read the verdict back out of the posted comment,
        # not only out of the run artifact, so the embedded copy has to be the
        # whole document and has to parse.
        embedded = rendered.split('```json\n')[-1].split('```')[0]
        self.assertEqual(document, json.loads(embedded))

    def test_validate_rejects_a_broken_document(self):
        self.assertNotEqual(0, self._validate(dict(ENVELOPE, verdict='nonsense',
                                                   recommendation='requeue',
                                                   schema_version=1,
                                                   triaged_at='2026-09-05T00:00:00+00:00')))
