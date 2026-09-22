# Copyright 2026 Michael Still and contributors

"""Pin the error contract's fixed facts against the documentation.

Issue #4254, from the phase 8 push audit of PLAN-api-input-validation:
the request-validation error contract is documented in four places
written across four phases, and phase 6's enforcement of required-ness
corrected only some of them. `docs/operator_guide/logging.md` and one
section of `docs/developer_guide/writing_an_endpoint.md` shipped
saying a parameter declared required but not supplied is recorded and
never enforced -- the opposite of what `validate_request` does -- and
the same stale claim sat in `config.py`'s `API_VALIDATION_MODE`
description, which is rendered into the configuration reference and
which no grep over `docs/` would ever reach.

This is deliberately not a narrative-parity test across the four
documents. A developer guide, an operator guide and a dated changelog
correctly state the same fact in different words for different
audiences (the reasoning `test_api_reference_specs.py` records), and
the release note was the one document that stayed *right* precisely
because it is time-scoped ("At this point in the rollout ..."), so a
strict-parity test would have flagged it as the odd one out. What is
pinned instead is the handful of facts which are single booleans or
fixed strings with no legitimate second phrasing, which is exactly
what drifted:

* The literal error shape `{"error": "<parameter>: <reason>",
  "status": ...}`. Every live occurrence must be byte-identical, up to
  the `.../400` status variant. `docs/plans/` and the component trees'
  `plans/` are historical by convention and excluded.
* That no live document claims missing-required findings are exempt
  from enforcement. `test_required_sweep.py` proves the runtime fact
  over the required declarations; the greps here tie the prose -- and
  the pydantic Field description -- to it.
"""

import os
import re

from shakenfist.config import SFConfig
from shakenfist.tests import base


def _docs_root():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, 'docs')


#: The one true rendering of a validation refusal, and the variant the
#: developer guide uses where the concrete status is beside the point.
CANONICAL_SHAPES = {
    '{"error": "<parameter>: <reason>", "status": 400}',
    '{"error": "<parameter>: <reason>", "status": ...}',
}

#: Anything that looks like an inline rendering of the error envelope
#: with a quoted message template. Loose enough to find a typo'd or
#: reordered copy of the shape above, tight enough not to match other
#: components' error JSON (ryll's envelope nests an object after
#: `"error":`, and the operator guide's capacity-refusal example is a
#: multi-line block whose brace is on its own line).
_SHAPE = re.compile(r'\{"error": "[^}]*\}')

#: The files the canonical shape is known to live in. If the finder
#: stops matching in one of these -- the shape reworded out of its
#: pattern, or deleted -- the assertion above goes vacuous for that
#: file, which is the worst outcome a consistency check can have.
SHAPE_BEARING_FILES = {
    os.path.join('developer_guide', 'writing_an_endpoint.md'),
    os.path.join('operator_guide', 'logging.md'),
    os.path.join('release_notes', 'v07-v08.md'),
}

#: Phrasings which can only be the pre-phase-6 claim that some findings
#: are exempt from enforcement. The exact regression this guards: both
#: shipped in tree after phase 6 made missing-required enforceable.
STALE_CLAIMS = [
    ('that some findings are exempt from enforcement',
     re.compile(r'other than\s+`?missing-required')),
    ('that required-ness is not enforced',
     re.compile(r'required[^.!?]{0,200}never\s+enforced')),
]

#: The live documents the stale-claim greps cover. The release note is
#: deliberately absent: its statement of the old behaviour is
#: time-scoped ("At this point in the rollout ...") and correct, which
#: is why it survived the drift the other documents suffered.
STALE_CLAIM_FILES = [
    os.path.join('developer_guide', 'writing_an_endpoint.md'),
    os.path.join('operator_guide', 'logging.md'),
]


class ErrorShapeTestCase(base.ShakenFistTestCase):
    def _live_markdown(self):
        for dirpath, dirnames, filenames in os.walk(_docs_root()):
            # Plan documents record what a phase said at the time and
            # are not corrected afterwards, so they legitimately carry
            # superseded renderings of the shape.
            dirnames[:] = sorted(d for d in dirnames if d != 'plans')
            for filename in sorted(filenames):
                if filename.endswith('.md'):
                    yield os.path.join(dirpath, filename)

    def test_every_live_occurrence_is_canonical(self):
        found_in = set()
        for path in self._live_markdown():
            with open(path) as f:
                text = f.read()
            rel = os.path.relpath(path, _docs_root())
            for match in _SHAPE.findall(text):
                with self.subTest(path=rel, match=match):
                    self.assertIn(
                        match, CANONICAL_SHAPES,
                        '%s renders the validation error shape as %r; the '
                        'contract is documented in several places and every '
                        'live rendering must be byte-identical' % (rel, match))
                found_in.add(rel)

        # Anti-vacuity: the files known to carry the shape still do. A
        # new file carrying a canonical occurrence is fine; one of
        # these three losing every occurrence means the shape was
        # reworded past the finder, not that the contract went away.
        self.assertTrue(
            SHAPE_BEARING_FILES.issubset(found_in),
            'the error shape is no longer found in %s'
            % sorted(SHAPE_BEARING_FILES - found_in))


class StaleClaimTestCase(base.ShakenFistTestCase):
    def test_the_documents_do_not_claim_the_exemption(self):
        for rel in STALE_CLAIM_FILES:
            with open(os.path.join(_docs_root(), rel)) as f:
                text = f.read()
            for what, pattern in STALE_CLAIMS:
                with self.subTest(path=rel, claim=what):
                    matched = pattern.search(text)
                    self.assertIsNone(
                        matched,
                        '%s claims %s (%r), but required-ness has been '
                        'enforced since phase 6 of PLAN-api-input-validation '
                        '-- test_required_sweep.py proves the runtime fact'
                        % (rel, what,
                           matched.group(0) if matched else None))

    def test_the_config_description_does_not_claim_the_exemption(self):
        # The operator-facing description of the control lives in a
        # pydantic Field, which is rendered into the configuration
        # reference but sits outside any documentation grep's reach.
        description = SFConfig.model_fields['API_VALIDATION_MODE'].description
        for what, pattern in STALE_CLAIMS:
            with self.subTest(claim=what):
                matched = pattern.search(description)
                self.assertIsNone(
                    matched,
                    'the API_VALIDATION_MODE description claims %s (%r)'
                    % (what, matched.group(0) if matched else None))

        # Anti-vacuity: the description still states what enforce does
        # with a missing-required finding, rather than having dropped
        # the subject -- silence is how the next drift starts.
        self.assertIn('missing-required', description)
