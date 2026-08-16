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
        # third; the CI detector's copy is asserted by the detector
        # itself, which fails if its query stops matching a token it
        # deliberately emitted.
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
