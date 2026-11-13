# Copyright 2026 Michael Still and contributors
#
# Tests for shakenfist/util/keymap.py, which turns key names and text
# into the Linux keycodes libvirt's send-key takes.

from shakenfist import exceptions
from shakenfist.tests import base
from shakenfist.util import keymap


class KeymapCharacterTestCase(base.ShakenFistTestCase):
    def test_lowercase_letters_are_unshifted(self):
        self.assertEqual([30], keymap.chord_for_character('a'))
        self.assertEqual([38], keymap.chord_for_character('l'))
        self.assertEqual([50], keymap.chord_for_character('m'))

    def test_uppercase_letters_carry_shift(self):
        self.assertEqual(
            [keymap.KEY_LEFTSHIFT, 30], keymap.chord_for_character('A'))

    def test_digits_and_their_shifted_symbols_share_a_keycode(self):
        # KEY_1 is 2 and shift-1 is '!', so the two must name the same
        # key. A transcription error in either table shows up here.
        self.assertEqual([2], keymap.chord_for_character('1'))
        self.assertEqual(
            [keymap.KEY_LEFTSHIFT, 2], keymap.chord_for_character('!'))

    def test_the_loose_keys_are_reachable(self):
        self.assertEqual([41], keymap.chord_for_character('`'))
        self.assertEqual([43], keymap.chord_for_character('\\'))
        self.assertEqual([57], keymap.chord_for_character(' '))
        self.assertEqual([28], keymap.chord_for_character('\n'))
        self.assertEqual([15], keymap.chord_for_character('\t'))

    def test_every_shifted_symbol_resolves(self):
        for char in '~!@#$%^&*()_+{}|:"<>?':
            chord = keymap.chord_for_character(char)
            self.assertEqual(
                keymap.KEY_LEFTSHIFT, chord[0],
                'shift is missing from the chord for %r' % char)
            self.assertEqual(2, len(chord))

    def test_each_keycode_is_claimed_once_per_shift_state(self):
        # Two characters claiming one keycode in the same shift state
        # would make one of them silently type the other.
        seen = set()
        for (char, entry) in keymap._CHARACTERS.items():
            self.assertNotIn(entry, seen, '%r duplicates %r' % (char, entry))
            seen.add(entry)

    def test_an_untypeable_character_is_refused(self):
        self.assertRaises(exceptions.InvalidKeyName,
                          keymap.chord_for_character, 'é')

    def test_text_is_one_chord_per_character(self):
        self.assertEqual(
            [[35], [30], [keymap.KEY_LEFTSHIFT, 2], [28]],
            keymap.chords_for_text('ha!\n'))

    def test_empty_text_sends_nothing(self):
        self.assertEqual([], keymap.chords_for_text(''))


class KeymapNameTestCase(base.ShakenFistTestCase):
    def test_named_keys_resolve(self):
        self.assertEqual([28], keymap.chord_for_key('enter'))
        self.assertEqual([62], keymap.chord_for_key('f4'))
        self.assertEqual([108], keymap.chord_for_key('down'))

    def test_names_are_case_insensitive_and_accept_the_virsh_prefix(self):
        self.assertEqual([28], keymap.chord_for_key('ENTER'))
        self.assertEqual([28], keymap.chord_for_key('KEY_ENTER'))
        self.assertEqual([28], keymap.chord_for_key('key_enter'))

    def test_a_chord_is_joined_with_plus(self):
        self.assertEqual([29, 56, 111],
                         keymap.chord_for_key('ctrl+alt+delete'))

    def test_a_chord_may_mix_names_and_characters(self):
        self.assertEqual([29, 46], keymap.chord_for_key('ctrl+c'))

    def test_a_single_character_key_is_taken_literally(self):
        # Otherwise '+' would split into two empty names, and '-' would
        # be indistinguishable from a chord separator typo.
        self.assertEqual([keymap.KEY_LEFTSHIFT, 13],
                         keymap.chord_for_key('+'))
        self.assertEqual([12], keymap.chord_for_key('-'))

    def test_an_unknown_name_is_refused_naming_the_key(self):
        e = self.assertRaises(exceptions.InvalidKeyName,
                              keymap.chord_for_key, 'banana')
        self.assertIn('banana', str(e))

    def test_a_non_string_key_is_refused(self):
        self.assertRaises(exceptions.InvalidKeyName,
                          keymap.chord_for_key, 42)
        self.assertRaises(exceptions.InvalidKeyName,
                          keymap.chord_for_key, '')

    def test_a_chord_longer_than_libvirt_accepts_is_refused(self):
        self.assertRaises(
            exceptions.InvalidKeyName, keymap.chord_for_key,
            '+'.join(['ctrl'] * (keymap.MAX_CHORD_KEYS + 1)))

    def test_keys_are_one_chord_each(self):
        self.assertEqual([[28], [29, 46]],
                         keymap.chords_for_keys(['enter', 'ctrl+c']))
