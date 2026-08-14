# Copyright 2019 Michael Still and contributors
"""The test base class refuses a SecretStr in a containment assertion.

``assertNotIn(attrs.key, haystack)`` cannot fail under testtools.
SecretStr implements no __contains__, so the containment raises
TypeError -- and testtools' Contains matcher catches TypeError and
reports "does not contain", so the assertion passes however much of the
secret the haystack holds. Assertions of that shape are almost always
leak guards, so a vacuous one is a test reporting that no secret
escaped while checking nothing.

Wrapping the namespace key fields in SecretStr emptied six such guards
across three files without a single failure to announce it. These tests
pin the check that now makes the shape loud, including that it does not
interfere with the ordinary uses of assertIn and assertNotIn -- an
over-broad guard here would be worse than the bug, since it would fire
on tests that are working correctly.
"""

from pydantic import SecretStr

from shakenfist.tests import base


class SecretNeedleGuardTestCase(base.ShakenFistTestCase):
    def test_a_secretstr_needle_is_refused(self):
        secret = SecretStr('the-actual-secret')
        haystack = 'a log line containing the-actual-secret in full'

        # The bug being guarded: this haystack plainly contains the
        # secret, and yet the unguarded assertion passes.
        self.assertRaises(TypeError, self.assertNotIn, secret, haystack)
        self.assertRaises(TypeError, self.assertIn, secret, haystack)

    def test_the_refusal_says_what_to_do_instead(self):
        try:
            self.assertNotIn(SecretStr('x'), 'haystack')
        except TypeError as e:
            self.assertIn('get_secret_value', str(e))
        else:
            self.fail('a SecretStr needle was accepted')

    def test_the_unwrapped_needle_is_accepted_and_works(self):
        secret = SecretStr('the-actual-secret')

        self.assertIn(
            secret.get_secret_value(),
            'a log line containing the-actual-secret in full')
        self.assertNotIn(
            secret.get_secret_value(), 'a log line with nothing in it')

    def test_the_rendered_mask_is_not_a_substitute(self):
        # The other half of the trap, which the guard cannot catch
        # because str(secret) really is a string. Recorded here so the
        # reason is written down beside the check: asserting the mask is
        # absent is true of a haystack carrying the real secret.
        secret = SecretStr('the-actual-secret')
        leaked = 'a log line containing the-actual-secret in full'

        self.assertNotIn(str(secret), leaked)
        self.assertIn(secret.get_secret_value(), leaked)

    def test_ordinary_assertions_are_unaffected(self):
        self.assertIn('a', 'abc')
        self.assertNotIn('z', 'abc')
        self.assertIn('key', {'key': 'value'})
        self.assertNotIn('missing', {'key': 'value'})
        self.assertIn(2, [1, 2, 3])
        self.assertNotIn(4, [1, 2, 3])

    def test_a_secretstr_haystack_is_refused_too(self):
        # The vacuity is a property of the containment, not of which
        # side the SecretStr is on: 'x' in SecretStr('abc') raises
        # TypeError just as the other order does, and Contains.match()
        # turns that into "does not contain" either way.
        self.assertRaises(
            TypeError, self.assertNotIn, 'x', SecretStr('abc'))

    def test_containment_against_a_secretstr_really_does_raise(self):
        # The premise the guard rests on, pinned directly. If a future
        # pydantic gave SecretStr a __contains__, the guard would become
        # unnecessary rather than wrong -- but this test would say so.
        self.assertRaises(TypeError, lambda: 'a' in SecretStr('abc'))
        self.assertRaises(
            TypeError, lambda: SecretStr('a') in 'abc')
