# Copyright 2026 Michael Still and contributors

"""The API reference's three spec sections must agree with ARGTYPES.

Definition-of-done item 11 of
docs/plans/PLAN-api-input-validation-phase-07-structured.md: "No page
states a spec's shape differently ... A script in the test suite
compares the reference's key lists against ARGTYPES rather than a
reviewer comparing them by eye."

The reason it is a test rather than a review item is that the two
documents drift in a way a reader cannot see. Before this phase a
diskspec key which was not documented was silently discarded, so a
reference which listed the wrong keys cost a caller a defaulted disk
and no message. Since this phase an undocumented key is refused with a
400, which means the reference is now the *only* published statement of
which keys exist -- the OpenAPI document has the schema, but the prose
here is what a human reads before they type. A key added to
ARGTYPES and not to the reference is an undocumented feature; a key
documented here and not in ARGTYPES is a 400 the documentation told
somebody to earn.

The census which wrote these schemas found exactly this class of
drift already in tree and not caused by this phase: usage.md listed
nine NIC models where the API reference listed eight, and usage.md and
config.DISK_BUS's own description both still listed the `ide` bus,
which external_api/instance.py has refused since v0.7. Nothing was
comparing them.

What is compared, and what is not:

* The key set, exactly, in both directions.
* Each key's published type, through the annotation the reference
  carries in brackets after the key name. `(enum)` means the schema
  types the value a string *and* carries an enum; every other
  annotation is the JSON Schema type itself.
* Required-ness, through the `, required` the annotation carries.
* Every value of every published enum, which must appear verbatim in
  the bullet documenting its key. The other direction is deliberately
  not asserted: the bus bullet names `ide` in order to say it is
  refused, and a bullet which explains why a value is *not* accepted is
  the reference doing its job rather than drifting.

Prose is not compared. A description lives in ARGTYPES as well, and
comparing the two would put the same paragraph in two files and fail
this test on a wording fix -- the same reasoning
test_openapi_spec.py's _without_descriptions() records.
"""

import os
import re

from shakenfist.external_api import base as api_base
from shakenfist.tests import base


#: Which reference section describes which ARGTYPES token. The section
#: headings are `### diskspec` and friends, in
#: docs/developer_guide/api_reference/instances.md.
SPEC_SECTIONS = {
    'diskspec': 'diskspec',
    'networkspec': 'networkspec',
    'videospec': 'videospec',
}

#: `* size (integer): the size of the disk ...`, or
#: `* network_uuid (string, required): the network ...`. Only top level
#: bullets, because a continuation line is indented and a nested bullet
#: is not a key.
_BULLET = re.compile(r'^\* ([a-z_]+) \(([^)]*)\): ')

_HEADING = re.compile(r'^#+ ')


def _reference_path():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(
        root, 'docs', 'developer_guide', 'api_reference', 'instances.md')


def parse_reference(text):
    """Every `### <spec>` section's documented keys.

    Returns {spec: {key: (annotation, bullet text)}}. The bullet text is
    carried so an enum's values can be looked for in the prose which
    documents them, and it includes the bullet's continuation lines --
    the bus enum's values are spread over four of them.
    """
    specs = {}
    current = None
    key = None
    for line in text.splitlines():
        heading = line.strip()
        if _HEADING.match(heading):
            name = heading.lstrip('# ').strip()
            current = name if name in SPEC_SECTIONS else None
            key = None
            continue

        if current is None:
            continue

        matched = _BULLET.match(line)
        if matched:
            key = matched.group(1)
            specs.setdefault(current, {})[key] = [matched.group(2), line]
            continue

        if key is not None and line.startswith('  '):
            # A continuation of the bullet above.
            specs[current][key][1] += ' ' + line.strip()
            continue

        # A blank line, or prose which is not part of a bullet, ends the
        # bullet but not the section.
        key = None

    return {spec: {name: tuple(value) for (name, value) in keys.items()}
            for (spec, keys) in specs.items()}


class APIReferenceSpecsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        with open(_reference_path()) as f:
            self.documented = parse_reference(f.read())

    def test_every_spec_section_was_found(self):
        """The parse itself is the thing most likely to rot.

        A heading rename or a change to the bullet style would make
        every assertion below vacuous rather than failing, which is the
        worst outcome a consistency check can have.
        """
        self.assertEqual(sorted(SPEC_SECTIONS), sorted(self.documented))
        for spec, keys in self.documented.items():
            self.assertNotEqual({}, keys, spec)

    def test_the_documented_keys_are_the_published_keys(self):
        for spec, token in sorted(SPEC_SECTIONS.items()):
            with self.subTest(spec=spec):
                published = api_base.ARGTYPES[token]['properties']
                self.assertEqual(
                    sorted(published), sorted(self.documented[spec]),
                    '%s: the API reference and ARGTYPES list different keys'
                    % spec)

    def test_each_documented_type_is_the_published_type(self):
        for spec, token in sorted(SPEC_SECTIONS.items()):
            published = api_base.ARGTYPES[token]['properties']
            for key, (annotation, _) in sorted(self.documented[spec].items()):
                with self.subTest(spec=spec, key=key):
                    parts = [p.strip() for p in annotation.split(',')]
                    declared = parts[0]
                    if declared == 'enum':
                        self.assertEqual(
                            'string', published[key]['type'],
                            '%s.%s is documented as an enum' % (spec, key))
                        self.assertIn(
                            'enum', published[key],
                            '%s.%s is documented as an enum but publishes no '
                            'enum' % (spec, key))
                    else:
                        self.assertEqual(
                            declared, published[key]['type'],
                            '%s.%s' % (spec, key))
                        self.assertNotIn(
                            'enum', published[key],
                            '%s.%s publishes an enum the reference does not '
                            'document as one' % (spec, key))

    def test_each_documented_requirement_is_the_published_requirement(self):
        for spec, token in sorted(SPEC_SECTIONS.items()):
            required = api_base.ARGTYPES[token].get('required', [])
            for key, (annotation, _) in sorted(self.documented[spec].items()):
                with self.subTest(spec=spec, key=key):
                    documented = 'required' in [
                        p.strip() for p in annotation.split(',')]
                    self.assertEqual(
                        key in required, documented,
                        '%s.%s: the reference and ARGTYPES disagree about '
                        'whether it is required' % (spec, key))

    def test_every_published_enum_value_is_documented(self):
        """One direction only -- see this module's docstring for why the
        other is deliberately absent."""
        checked = 0
        for spec, token in sorted(SPEC_SECTIONS.items()):
            published = api_base.ARGTYPES[token]['properties']
            for key, value in sorted(published.items()):
                if 'enum' not in value:
                    continue
                bullet = self.documented[spec][key][1]
                for member in value['enum']:
                    with self.subTest(spec=spec, key=key, member=member):
                        self.assertIn(
                            member, bullet,
                            '%s.%s publishes %r and the reference does not '
                            'name it' % (spec, key, member))
                        checked += 1

        # The rule is vacuous if nothing publishes an enum at all.
        self.assertNotEqual(0, checked)

    def test_the_unknown_key_refusal_is_stated(self):
        """The contract change this phase made is the reason the key
        list above has to be right, so the reference has to say it."""
        with open(_reference_path()) as f:
            text = f.read()
        self.assertIn('is refused with a 400 naming it', text)
