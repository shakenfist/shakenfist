#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors

"""Read, validate and render the automated merge CI triage verdict.

The merge failure triage job asks Claude Code for one small JSON object
describing whether a merge queue failure was the pull request's fault. Three
things then need doing to it, and each is here rather than in
tools/merge-ci-triage.sh because each has edge cases worth testing:

    merge-triage.py extract <response> <envelope> <output>
    merge-triage.py render <triage> <output>
    merge-triage.py validate <triage>

**extract** pulls the object out of the model's captured stdout and merges it
into an envelope the workflow built from GitHub's own data. The envelope always
wins: the run id, the pull request number and the repository are facts we
already hold, and a model which misremembers one of them must not be able to
mislabel somebody else's failure. Only the fields in MODEL_FIELDS are taken
from the response at all.

A response which holds no usable object is not an error condition to be
retried. It becomes a document with a verdict of `unknown` and an `error`
saying why, because the conductor tracking these needs a record that triage
ran and reached nothing far more than it needs a missing file. That is also
why there is no truncation salvage here of the sort
`review-pr-with-claude/extract-review-json.py` carries: a review is hundreds of
lines and worth rescuing halfway, a triage verdict is fifteen and a half one
is not a verdict.

**render** produces the comment posted on the pull request: human readable
markdown with the JSON embedded in a collapsed details section, exactly as the
automated reviewer does it, so the verdict can be read back out of the comment
by machine as well as fetched from the run's artifact.

**validate** checks a document against tools/merge-triage-schema.json.
"""

import datetime
import json
import os
import sys


# jsonschema is not in the runtime dependencies, and this script runs on a CI
# runner where it may or may not be installed. Validation degrades to the
# structural checks in _validate_basic() rather than failing, which follows
# what review-pr-with-claude/render-review.py does with the review schema.
try:
    import jsonschema
except ImportError:
    jsonschema = None


SCHEMA_VERSION = 1
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'merge-triage-schema.json')

# The only keys taken from the model's response. Everything else in the
# document is envelope, written from what GitHub told us.
MODEL_FIELDS = [
    'verdict', 'confidence', 'summary', 'failing_job', 'failing_step',
    'failure_signature', 'recommendation', 'tracking_issue',
    'tracking_issue_action', 'evidence'
]

VERDICTS = ['pr_caused', 'systemic', 'ambiguous', 'unknown']
RECOMMENDATIONS = ['requeue', 'fix_first', 'investigate']
CONFIDENCES = ['high', 'medium', 'low']
ISSUE_ACTIONS = ['commented', 'created', 'none']

# What to do about a pull request, when the model gave a verdict but no usable
# recommendation. These are the only sensible pairings, so deriving one is
# better than discarding an otherwise good verdict.
DEFAULT_RECOMMENDATION = {
    'pr_caused': 'fix_first',
    'systemic': 'requeue',
    'ambiguous': 'investigate',
    'unknown': 'investigate'
}

VERDICT_HEADLINE = {
    'pr_caused': 'This pull request caused the failure',
    'systemic': 'Systemic failure, not caused by this pull request',
    'ambiguous': 'Ambiguous -- the evidence points both ways',
    'unknown': 'Triage did not reach a verdict'
}

# How a cited tracking issue is described in the comment. A cited issue with
# an action of "none" is a reference the reader may find useful, not a record
# that the occurrence was filed anywhere -- and the two must not read alike.
ISSUE_ACTION_TEXT = {
    'commented': 'occurrence recorded',
    'created': 'issue created',
    'none': 'referenced only, nothing recorded'
}

RECOMMENDATION_TEXT = {
    'requeue': 'Re-queue as-is.',
    'fix_first': 'Fix the pull request before re-queueing.',
    'investigate': 'Needs a human look before re-queueing.'
}


def _find_json_object(text):
    """Return the last complete JSON object in text, or None.

    Candidate start positions are tried newest first because a model which
    illustrates the format before filling it in emits the real answer last.
    raw_decode() stops at the end of the first complete value, so trailing
    prose after the object, and a closing code fence, are both tolerated.
    """
    decoder = json.JSONDecoder()
    starts = [i for i, char in enumerate(text) if char == '{']
    for start in reversed(starts):
        try:
            value, _ = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(value, dict) and any(field in value for field in MODEL_FIELDS):
            return value
    return None


def _coerce_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).lstrip('#'))
    except (TypeError, ValueError):
        return None


def _coerce_str(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def _clean_model_fields(raw):
    """Normalise the model's object into the fields the schema allows."""
    cleaned = {}

    verdict = _coerce_str(raw.get('verdict'))
    if verdict is not None:
        verdict = verdict.lower().replace('-', '_').replace(' ', '_')
    if verdict not in VERDICTS or verdict == 'unknown':
        # An absent, misspelled or self-declared-unknown verdict is not a
        # verdict. The caller turns this into the fallback document, so that
        # the reason lands in the error field rather than being silently
        # rendered as a confident "unknown".
        return None
    cleaned['verdict'] = verdict

    confidence = _coerce_str(raw.get('confidence'))
    if confidence is not None and confidence.lower() in CONFIDENCES:
        cleaned['confidence'] = confidence.lower()

    recommendation = _coerce_str(raw.get('recommendation'))
    if recommendation is not None:
        recommendation = recommendation.lower().replace('-', '_').replace(' ', '_')
    if recommendation not in RECOMMENDATIONS:
        recommendation = DEFAULT_RECOMMENDATION[verdict]
    cleaned['recommendation'] = recommendation

    for field in ['summary', 'failing_job', 'failing_step', 'failure_signature']:
        cleaned[field] = _coerce_str(raw.get(field))

    cleaned['tracking_issue'] = _coerce_int(raw.get('tracking_issue'))

    action = _coerce_str(raw.get('tracking_issue_action'))
    if action is not None:
        action = action.lower()
    if action not in ISSUE_ACTIONS:
        action = 'commented' if cleaned['tracking_issue'] else 'none'
    cleaned['tracking_issue_action'] = action

    evidence = raw.get('evidence')
    if isinstance(evidence, list):
        cleaned['evidence'] = [_coerce_str(item) for item in evidence if _coerce_str(item)]
    elif _coerce_str(evidence):
        cleaned['evidence'] = [_coerce_str(evidence)]
    else:
        cleaned['evidence'] = []

    return cleaned


def _fallback(envelope, error):
    document = dict(envelope)
    document.update({
        'verdict': 'unknown',
        'confidence': 'low',
        'recommendation': 'investigate',
        'summary': 'Automated triage ran but did not produce a usable verdict.',
        'failing_job': None,
        'failing_step': None,
        'failure_signature': None,
        'tracking_issue': None,
        'tracking_issue_action': 'none',
        'evidence': [],
        'error': error
    })
    return document


def _envelope_defaults(envelope):
    envelope = dict(envelope)
    envelope['schema_version'] = SCHEMA_VERSION
    envelope.setdefault('triaged_at',
                        datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat())
    return envelope


def do_extract(response_path, envelope_path, output_path):
    with open(envelope_path) as f:
        envelope = _envelope_defaults(json.load(f))

    with open(response_path) as f:
        response = f.read()

    raw = _find_json_object(response)
    if raw is None:
        document = _fallback(envelope, 'The triage response contained no JSON verdict object.')
        used_model = False
    else:
        cleaned = _clean_model_fields(raw)
        if cleaned is None:
            document = _fallback(
                envelope, 'The triage response held a JSON object with no usable verdict field.')
            used_model = False
        else:
            document = dict(cleaned)
            document['error'] = None
            # The envelope is applied last and wins every collision.
            document.update(envelope)
            used_model = True

    with open(output_path, 'w') as f:
        json.dump(document, f, indent=2, sort_keys=True)
        f.write('\n')

    if not used_model:
        sys.stderr.write('merge-triage: no verdict in the model response, wrote a fallback document\n')
        return 1
    return 0


def _validate_basic(document):
    """Structural validation for when jsonschema is not installed."""
    errors = []
    for field in ['schema_version', 'repository', 'run_id', 'run_url', 'verdict',
                  'recommendation', 'triaged_at']:
        if document.get(field) in (None, ''):
            errors.append('missing required field: %s' % field)
    if document.get('verdict') not in VERDICTS:
        errors.append('verdict is not one of %s' % VERDICTS)
    if document.get('recommendation') not in RECOMMENDATIONS:
        errors.append('recommendation is not one of %s' % RECOMMENDATIONS)
    if document.get('tracking_issue_action') not in ISSUE_ACTIONS:
        errors.append('tracking_issue_action is not one of %s' % ISSUE_ACTIONS)
    return errors


def do_validate(path):
    with open(path) as f:
        document = json.load(f)

    errors = _validate_basic(document)
    if errors:
        for error in errors:
            sys.stderr.write('merge-triage: %s\n' % error)
        return 1

    if jsonschema is None:
        sys.stderr.write('merge-triage: jsonschema not installed, basic validation only\n')
        return 0

    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    try:
        jsonschema.validate(document, schema)
    except jsonschema.ValidationError as e:
        sys.stderr.write('merge-triage: schema validation failed: %s\n' % e.message)
        return 1
    return 0


def render_markdown(document):
    verdict = document.get('verdict', 'unknown')
    lines = ['## Automated merge CI triage', '']
    lines.append('**%s**' % VERDICT_HEADLINE.get(verdict, VERDICT_HEADLINE['unknown']))
    lines.append('')

    if document.get('summary'):
        lines.append(document['summary'])
        lines.append('')

    facts = []
    if document.get('run_url'):
        facts.append('| Failed run | %s |' % document['run_url'])
    if document.get('failing_job'):
        facts.append('| First failing job | `%s` |' % document['failing_job'])
    if document.get('failing_step'):
        facts.append('| First failing step | `%s` |' % document['failing_step'])
    if document.get('failure_signature'):
        facts.append('| Signature | `%s` |' % document['failure_signature'])
    if document.get('confidence'):
        facts.append('| Confidence | %s |' % document['confidence'])
    if document.get('tracking_issue'):
        facts.append('| Tracking issue | #%d (%s) |' % (
            document['tracking_issue'],
            ISSUE_ACTION_TEXT.get(document.get('tracking_issue_action'), ISSUE_ACTION_TEXT['none'])))
    if document.get('triage_run_url'):
        facts.append('| Triaged by | %s |' % document['triage_run_url'])
    if facts:
        lines.append('| | |')
        lines.append('|---|---|')
        lines.extend(facts)
        lines.append('')

    evidence = document.get('evidence') or []
    if evidence:
        lines.append('### Evidence')
        lines.append('')
        for item in evidence:
            lines.append('- %s' % item)
        lines.append('')

    lines.append('### Recommendation')
    lines.append('')
    lines.append(RECOMMENDATION_TEXT.get(document.get('recommendation'), RECOMMENDATION_TEXT['investigate']))
    lines.append('')

    if document.get('error'):
        lines.append('> Triage did not complete: %s' % document['error'])
        lines.append('')

    lines.append('This verdict is automated and is not a substitute for reading the run. '
                 'The triage skill it follows is documented in `docs/developer_guide/ci.md`.')
    lines.append('')
    lines.append('<details>')
    lines.append('<summary>Machine-readable triage data (for automation)</summary>')
    lines.append('')
    lines.append('```json')
    lines.append(json.dumps(document, indent=2, sort_keys=True))
    lines.append('```')
    lines.append('')
    lines.append('</details>')

    return '\n'.join(lines) + '\n'


def do_render(input_path, output_path):
    with open(input_path) as f:
        document = json.load(f)
    with open(output_path, 'w') as f:
        f.write(render_markdown(document))
    return 0


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2

    command = argv[1]
    args = argv[2:]

    if command == 'extract' and len(args) == 3:
        return do_extract(*args)
    if command == 'render' and len(args) == 2:
        return do_render(*args)
    if command == 'validate' and len(args) == 1:
        return do_validate(*args)

    sys.stderr.write(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
