"""The format of key secrets the cluster generates for itself."""

import os
import re
import tomllib

from shakenfist.tests import base
from shakenfist.util import credentials


class FormatTestCase(base.ShakenFistTestCase):
    def test_generated_secrets_are_well_formed(self):
        secret = credentials.generate()
        self.assertTrue(secret.startswith(credentials.PREFIX))
        self.assertEqual(credentials.TOTAL_LENGTH, len(secret))
        self.assertTrue(credentials.looks_valid(secret))

    def test_generated_secrets_fit_the_key_length_limit(self):
        # The API refuses keys longer than 72 characters, because that
        # is bcrypt's limit.
        self.assertLessEqual(credentials.TOTAL_LENGTH, 72)

    def test_secrets_are_unique(self):
        self.assertEqual(
            100, len({credentials.generate() for _ in range(100)}))

    def test_checksum_catches_a_single_character_change(self):
        # The point of the checksum is that a scanner can reject a
        # lookalike without calling us, so a near miss must fail.
        secret = credentials.generate()
        for position in (5, 20, len(secret) - 1):
            wrong = 'A' if secret[position] != 'A' else 'B'
            corrupted = secret[:position] + wrong + secret[position + 1:]
            self.assertFalse(
                credentials.looks_valid(corrupted),
                f'corruption at position {position} was not detected')

    def test_truncation_is_rejected(self):
        secret = credentials.generate()
        self.assertFalse(credentials.looks_valid(secret[:-1]))
        self.assertFalse(credentials.looks_valid(secret + 'A'))

    def test_operator_secrets_are_not_mistaken_for_ours(self):
        for secret in ('hunter2', '', 'sfk', 'SFK_something'):
            self.assertFalse(credentials.looks_valid(secret))
            self.assertFalse(
                credentials.has_prefix(secret),
                f'{secret!r} should not claim the reserved prefix')

    def test_prefix_claimed_but_invalid(self):
        # This is the shape early rejection at /auth keys off: it
        # claims to be ours and demonstrably is not.
        self.assertTrue(credentials.has_prefix('sfk_nonsense'))
        self.assertFalse(credentials.looks_valid('sfk_nonsense'))

    def test_non_string_input_is_handled(self):
        for value in (None, 42, b'sfk_bytes', ['sfk_']):
            self.assertFalse(credentials.has_prefix(value))
            self.assertFalse(credentials.looks_valid(value))


class ScannerAgreementTestCase(base.ShakenFistTestCase):
    """The scanners and the format must describe the same thing.

    A leak detector is worth exactly as much as its agreement with
    what the cluster actually mints. Restating the pattern inside the
    test would only prove the test agrees with itself, so these read
    the committed artifacts and check them against real generated
    secrets. If the format is ever changed, these fail rather than the
    scanners quietly ceasing to match anything.
    """

    def _repository_root(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    def _gitleaks_rule(self, rule_id='shakenfist-key-secret'):
        path = os.path.join(self._repository_root(), '.gitleaks.toml')
        with open(path, 'rb') as f:
            config = tomllib.load(f)

        for rule in config.get('rules', []):
            if rule.get('id') == rule_id:
                return rule
        self.fail(
            f'.gitleaks.toml has no rule with id {rule_id!r}. The CI '
            'job and this test both name it, so renaming the rule '
            'silently stops the repository being scanned for our own '
            'credential format.')

    def test_the_gitleaks_rule_matches_what_we_mint(self):
        pattern = re.compile(self._gitleaks_rule()['regex'])
        for _ in range(50):
            secret = credentials.generate()
            self.assertTrue(
                pattern.fullmatch(secret),
                'The gitleaks rule does not match a secret the cluster '
                'just generated, so a committed credential would not '
                'be detected.')

    def test_the_gitleaks_rule_is_not_looser_than_the_format(self):
        pattern = re.compile(self._gitleaks_rule()['regex'])
        secret = credentials.generate()
        for lookalike in (secret[:-1], secret + 'A', 'sfk_short'):
            self.assertFalse(
                pattern.fullmatch(lookalike),
                f'The gitleaks rule matches {len(lookalike)} characters '
                'as well as the real length, which widens it beyond the '
                'format and invites false positives.')

    def test_the_alert_rule_uses_the_same_expression(self):
        # The operator's alerting rule, the functional CI detector and
        # the gitleaks rule are three copies of one pattern in three
        # languages. This is the binding between the first and the
        # third.
        path = os.path.join(
            self._repository_root(), 'examples', 'loki-secret-alert.yaml')
        with open(path) as f:
            alert = f.read()

        self.assertIn(
            self._gitleaks_rule()['regex'], alert,
            'examples/loki-secret-alert.yaml does not search for the '
            'same pattern as .gitleaks.toml, so an operator following '
            'our documentation would be looking for something other '
            'than what we mint.')

    def test_the_ci_detector_uses_the_same_expression(self):
        """And the binding for the second of the three copies.

        The detector emits a control token and fails if its own query
        does not find it, which is nearly this assertion -- but not
        quite. The control is built from a hand-written 'sfk_' plus 32
        plus 6 layout rather than from credentials.generate(), so if the
        format changed (a 40 character body, say) the detector's pattern
        and its control would still agree with each other while
        matching nothing the cluster mints. It would pass, green and
        vacuous, which is precisely the failure this phase exists to
        prevent.

        Read as text rather than imported: the functional suite is a
        client of the cluster and is not importable from here.
        """
        path = os.path.join(
            self._repository_root(), 'shakenfist', 'deploy', 'shakenfist_ci',
            'smoke_ci_tests', 'test_loki.py')
        with open(path) as f:
            detector = f.read()

        expression = self._gitleaks_rule()['regex']
        self.assertIn(
            "SECRET_SHAPE = '%s'" % expression, detector,
            'The functional CI leak detector does not search for the '
            'same pattern as .gitleaks.toml, so the repository and the '
            'log stream are being scanned for different things and one '
            'of them is not what we mint.')

    def test_accepted_findings_are_documented_and_well_formed(self):
        """Every .gitleaksignore entry is a claim, and claims need reasons.

        An entry silences a finding gitleaks was right to report, in a
        commit nobody can now change. A malformed one silences nothing
        and gitleaks says so only at trace level, so a typo would leave
        the job red with no explanation; an undocumented one leaves the
        next reader unable to tell an accepted risk from a mistake.
        """
        path = os.path.join(self._repository_root(), '.gitleaksignore')
        if not os.path.exists(path):
            # No accepted findings at all, which is the state this test
            # tells its reader to aim for below. Following that advice
            # should leave the suite green rather than turning this into
            # a FileNotFoundError.
            return

        with open(path) as f:
            lines = f.read().splitlines()

        entries = 0
        commented = False
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                # A blank line ends a block, so the next entry needs its
                # own explanation rather than inheriting the last one.
                commented = False
                continue
            if stripped.startswith('#'):
                commented = True
                continue

            entries += 1
            fields = stripped.split(':')
            self.assertEqual(
                4, len(fields),
                f'.gitleaksignore line {number} is not a fingerprint of '
                'the form commit:path:rule-id:line. gitleaks silently '
                'ignores an entry which matches nothing, so this would '
                'leave the scan failing with no indication why.')
            self.assertRegex(
                fields[0], r'^[0-9a-f]{40}$',
                f'.gitleaksignore line {number} does not begin with a '
                'full commit hash.')
            self.assertTrue(
                fields[3].isdigit(),
                f'.gitleaksignore line {number} does not end with a line '
                'number.')
            self.assertTrue(
                commented,
                f'.gitleaksignore line {number} accepts a finding with no '
                'comment above it explaining why it is safe and what was '
                'done about the credential. Adding an entry is a claim; '
                'record the basis for it.')

        self.assertTrue(
            entries,
            '.gitleaksignore has no entries at all. If the accepted '
            'findings have genuinely been dealt with, delete the file '
            'rather than leaving an empty one behind.')

    def test_the_documented_example_is_not_a_credential(self):
        # The tail used by the documentation and by the CI detector's
        # control tokens. 'zzzzzz' in base62 is 56800235583, larger
        # than the largest CRC32 (4294967295), so no input produces it
        # and validity fails by arithmetic rather than by assertion.
        # Several things depend on that being true; this is where it
        # is checked.
        body = credentials.generate()[len(credentials.PREFIX):-6]
        example = f'{credentials.PREFIX}{body}zzzzzz'

        self.assertTrue(credentials.has_prefix(example))
        self.assertFalse(credentials.looks_valid(example))
        self.assertTrue(
            re.fullmatch(self._gitleaks_rule()['regex'], example),
            'A scanner must still match the shape of an invalid '
            'example, or the examples would not exercise the rules '
            'they are written to demonstrate.')
