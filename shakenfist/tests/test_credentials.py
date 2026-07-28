"""The format of key secrets the cluster generates for itself."""

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
