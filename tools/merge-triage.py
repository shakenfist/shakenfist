#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors

"""Read, validate and render the automated merge CI triage verdict.

The merge failure triage job asks Claude Code for one small JSON object
describing whether a merge queue failure was the pull request's fault. Three
things then need doing to it, and each is here rather than in
tools/merge-ci-triage.sh because each has edge cases worth testing:

    merge-triage.py envelope <repository> <run json> <output> [triage run url]
    merge-triage.py extract <response> <envelope> <output>
    merge-triage.py fallback <envelope> <output> <error>
    merge-triage.py render <triage> <output>
    merge-triage.py validate <triage>

**envelope** builds the facts half of the document out of what GitHub said
about the run, including picking the pull request number out of the merge
group ref. It is here rather than in the shell because the ref shapes worth
getting right -- a base branch with a slash in it, a ref that is not a queue
ref at all -- are worth a test each, and jq is a poor place to keep them.

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

**fallback** writes the no-verdict document directly, for the paths in
tools/merge-ci-triage.sh which fail before a model is ever run. The promise
made to consumers is that a triage which happened is always visible as a
document, and a triage that fell over reading the run is exactly the case
where that matters.

**validate** checks a document against tools/merge-triage-schema.json.
"""

import datetime
import json
import os
import re
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

# gh-readonly-queue/<base>/pr-<number>-<sha>. The base branch is greedy
# because it may itself contain slashes (release/1.0), and the pull request
# number is the last "pr-<digits>-" before the merge commit sha.
QUEUE_REF_RE = re.compile(
    r'^gh-readonly-queue/(?P<base>.+)/pr-(?P<number>\d+)-(?P<sha>[0-9a-fA-F]+)$')

# Markup that would break the comment the document is embedded in: a fence
# ends the ```json block a consumer reads the verdict out of, and a stray
# details tag ends the collapsed section early -- or opens a second one the
# real closing tag then closes, leaving the section unclosed. Both also stop
# neutralise-pr-body.sh defusing mentions on every line after them, because it
# tracks fenced regions. Model text is not trusted to avoid either.
#
# Both delimiters have more than one spelling and the narrow forms were not
# enough: ~~~ is a GitHub fence too, and a details tag may carry attributes
# (<details open>) or whitespace before the close. Match the tag by name and
# swallow to the closing angle bracket.
UNSAFE_MARKUP_RE = re.compile(r'```+|~~~+|</?details\b[^>]*>', re.IGNORECASE)

# Characters that break a markdown table cell. The facts table puts model
# prose in single cells, several of them inside an inline code span, so an
# embedded newline splits the row, a pipe adds a column, and a backtick ends
# the span. Only the human-readable half is affected -- json.dumps escapes all
# three for the embedded document -- but a table which has come apart is how a
# reader decides the whole comment is untrustworthy.
TABLE_WHITESPACE_RE = re.compile(r'\s+')

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


# How far back from the end of the response the unfenced scan looks. The
# prompt asks for the verdict as the last thing emitted, and a response can
# quote a hundred kilobytes of log above it; trying a decode from every brace
# in all of that is quadratic work to find something the prompt says is at the
# bottom.
UNFENCED_SCAN_BYTES = 65536

FENCED_BLOCK_RE = re.compile(r'```(?:json)?\s*\n(.*?)\n\s*```', re.DOTALL)


def _looks_like_verdict(value):
    return isinstance(value, dict) and any(field in value for field in MODEL_FIELDS)


def _pick_verdict(candidates):
    """The best of several candidate objects, in newest-first order.

    An object carrying a `verdict` key beats one that merely has some other
    field of the schema in it. Without that preference a model which emits the
    verdict and then a trailing appendix -- {"evidence": [...]}, or a bare
    {"failure_signature": "..."} -- has the appendix chosen, the cleaning step
    then rejects it for having no verdict, and a triage which actually
    succeeded is published as "unknown".
    """
    for value in candidates:
        if 'verdict' in value:
            return value
    return candidates[0] if candidates else None


def _find_json_object(text):
    """Return the last usable JSON object in text, or None.

    Fenced blocks are tried first, and both searches run newest first: a model
    which illustrates the format before filling it in emits the real answer
    last. Failing that the tail of the response is scanned for a bare object,
    because models drop the fence often enough that requiring it would throw
    away good verdicts. raw_decode() stops at the end of the first complete
    value, so trailing prose after the object is tolerated.

    The unfenced scan is only reached when no fenced block held a candidate,
    because it is the expensive half: a decode attempt from every brace in the
    tail of a response which may quote a hundred kilobytes of log.
    """
    fenced = []
    for block in reversed(FENCED_BLOCK_RE.findall(text)):
        try:
            value = json.loads(block)
        except ValueError:
            continue
        if _looks_like_verdict(value):
            fenced.append(value)

    chosen = _pick_verdict(fenced)
    if chosen is not None:
        return chosen

    decoder = json.JSONDecoder()
    tail = text[-UNFENCED_SCAN_BYTES:]
    starts = [i for i, char in enumerate(tail) if char == '{']
    unfenced = []
    for start in reversed(starts):
        try:
            value, _ = decoder.raw_decode(tail[start:])
        except ValueError:
            continue
        if _looks_like_verdict(value):
            unfenced.append(value)
    return _pick_verdict(unfenced)


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


def _safe_prose(value):
    """Model prose with the markup that would break its own container removed.

    The document is published inside a fenced block inside a <details> section
    of a pull request comment, and the promise that a consumer can read the
    verdict back out of that comment rests on both surviving. A summary
    carrying a fence or a </details> ends them early. See UNSAFE_MARKUP_RE.
    """
    text = _coerce_str(value)
    if text is None:
        return None
    return _coerce_str(UNSAFE_MARKUP_RE.sub(' ', text))


def _table_cell(value):
    """Model prose flattened into something a markdown table cell survives.

    Applied at render time rather than in _safe_prose, because the escaping is
    a property of where the text is being put rather than of the text: the
    same value goes into the embedded JSON unmangled, and the summary
    paragraph above the table needs its pipes and backticks left alone. See
    TABLE_WHITESPACE_RE.
    """
    return TABLE_WHITESPACE_RE.sub(' ', str(value)).strip().replace(
        '|', r'\|').replace('`', "'")


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
        cleaned[field] = _safe_prose(raw.get(field))

    cleaned['tracking_issue'] = _coerce_int(raw.get('tracking_issue'))

    action = _coerce_str(raw.get('tracking_issue_action'))
    if action is not None:
        action = action.lower()
    if action not in ISSUE_ACTIONS:
        # 'none' rather than a guess of 'commented': the consumer is told that
        # an action of commented means the occurrence really was recorded, and
        # the verification step downstream can only take that claim away, not
        # discover one. Guessing a write happened is the wrong direction.
        action = 'none'
    cleaned['tracking_issue_action'] = action

    evidence = raw.get('evidence')
    if not isinstance(evidence, list):
        evidence = [evidence]
    cleaned['evidence'] = [item for item in (_safe_prose(i) for i in evidence) if item]

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


def parse_queue_ref(ref):
    """Merge group ref -> (base branch, pull request number).

    Returns (None, None) for anything that is not a merge queue ref, which is
    survivable: triage still runs and the verdict simply has no pull request
    to attach itself to.
    """
    match = QUEUE_REF_RE.match(ref or '')
    if not match:
        return None, None
    return match.group('base'), int(match.group('number'))


def do_envelope(repository, run_path, output_path, triage_run_url=''):
    """Build the facts half of the document from what GitHub said."""
    with open(run_path) as f:
        run = json.load(f)

    run_id = run.get('databaseId')
    if not isinstance(run_id, int) or isinstance(run_id, bool):
        sys.stderr.write('merge-triage: run json carries no numeric databaseId\n')
        return 1

    head_branch = run.get('headBranch') or ''
    base_branch, pull_request = parse_queue_ref(head_branch)
    if pull_request is None:
        sys.stderr.write(
            'merge-triage: no pull request number in ref %r\n' % head_branch)

    attempt = run.get('attempt')
    envelope = {
        'repository': repository,
        'run_id': run_id,
        'run_url': run.get('url') or '',
        'run_attempt': attempt if isinstance(attempt, int) else 1,
        'head_branch': head_branch,
        'head_sha': run.get('headSha') or '',
        'base_branch': base_branch,
        'pull_request': pull_request,
        'triage_run_url': triage_run_url or None
    }

    with open(output_path, 'w') as f:
        json.dump(_envelope_defaults(envelope), f, indent=2, sort_keys=True)
        f.write('\n')
    return 0


def do_fallback(envelope_path, output_path, error):
    """Write the no-verdict document for a triage that never reached a model."""
    with open(envelope_path) as f:
        envelope = _envelope_defaults(json.load(f))

    with open(output_path, 'w') as f:
        json.dump(_fallback(envelope, error), f, indent=2, sort_keys=True)
        f.write('\n')
    return 0


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
        facts.append('| First failing job | `%s` |' % _table_cell(document['failing_job']))
    if document.get('failing_step'):
        facts.append('| First failing step | `%s` |' % _table_cell(document['failing_step']))
    if document.get('failure_signature'):
        facts.append('| Signature | `%s` |' % _table_cell(document['failure_signature']))
    if document.get('confidence'):
        facts.append('| Confidence | %s |' % _table_cell(document['confidence']))
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
                 'The procedure it follows is described in `docs/developer_guide/ci.md`.')
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

    if command == 'envelope' and len(args) in (3, 4):
        return do_envelope(*args)
    if command == 'extract' and len(args) == 3:
        return do_extract(*args)
    if command == 'fallback' and len(args) == 3:
        return do_fallback(*args)
    if command == 'render' and len(args) == 2:
        return do_render(*args)
    if command == 'validate' and len(args) == 1:
        return do_validate(*args)

    sys.stderr.write(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
