# Copyright 2019 Michael Still and contributors
"""Key names and text to Linux input event codes.

libvirt's ``virDomainSendKey()`` takes keycodes rather than key names, and
the python bindings do not expose the name tables ``virsh send-key`` uses,
so Shaken Fist carries its own. The codeset is ``VIR_KEYCODE_SET_LINUX``,
whose values are the ``KEY_*`` constants of ``linux/input-event-codes.h``
-- the set qemu's input layer speaks natively, so nothing in the
hypervisor has to translate the codes before the guest sees them.

A "chord" here is the list of keycodes libvirt presses together and then
releases in one call: ``ctrl+alt+delete`` is one chord of three codes, and
typed text is one chord per character, two codes long where the character
needs shift.
"""

from typing import Any

from shakenfist import exceptions


# The keycodes of the character keys, in the contiguous runs
# input-event-codes.h lays them out in. Written as runs rather than as a
# literal per character because a transcription error in a run of
# twelve is visible, where one in a dict of ninety is not.
_RUNS = (
    ('1234567890-=', 2),      # KEY_1 .. KEY_EQUAL
    ('qwertyuiop[]', 16),     # KEY_Q .. KEY_RIGHTBRACE
    ("asdfghjkl;'", 30),      # KEY_A .. KEY_APOSTROPHE
    ('zxcvbnm,./', 44),       # KEY_Z .. KEY_SLASH
)

# The character keys which fall outside those runs. Newline is the enter
# key and tab is the tab key, so that a caller can send a multi line
# answer to a wizard as one string.
_LOOSE = {'`': 41, '\\': 43, ' ': 57, '\n': 28, '\t': 15}

# What shift turns each of those keys into, on a US layout. Shaken Fist
# publishes no other layout because the guest decides what a keycode
# means and we cannot ask it.
_SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', '`1234567890-=[]\\;\',./'))

KEY_LEFTSHIFT = 42

# Keys with no character of their own, which a caller names instead. The
# aliases are the unqualified spellings an operator expects to work
# (``ctrl`` rather than only ``leftctrl``).
_NAMED = {
    'esc': 1, 'escape': 1, 'backspace': 14, 'tab': 15, 'enter': 28,
    'return': 28, 'ctrl': 29, 'leftctrl': 29, 'grave': 41,
    'shift': KEY_LEFTSHIFT, 'leftshift': KEY_LEFTSHIFT, 'backslash': 43,
    'rightshift': 54, 'alt': 56, 'leftalt': 56, 'space': 57,
    'capslock': 58, 'rightctrl': 97, 'rightalt': 100, 'home': 102,
    'up': 103, 'pageup': 104, 'left': 105, 'right': 106, 'end': 107,
    'down': 108, 'pagedown': 109, 'insert': 110, 'delete': 111,
    'f1': 59, 'f2': 60, 'f3': 61, 'f4': 62, 'f5': 63, 'f6': 64,
    'f7': 65, 'f8': 66, 'f9': 67, 'f10': 68, 'f11': 87, 'f12': 88,
}


def _build_characters() -> dict[str, tuple[int, bool]]:
    chars: dict[str, tuple[int, bool]] = {}
    for (run, first) in _RUNS:
        for (offset, char) in enumerate(run):
            chars[char] = (first + offset, False)
    for (char, code) in _LOOSE.items():
        chars[char] = (code, False)

    # Derived from the unshifted keys above rather than listed, so a
    # shifted character cannot name a different key to the one it
    # shares a keycap with.
    for (shifted, plain) in _SHIFTED.items():
        chars[shifted] = (chars[plain][0], True)
    for char in 'abcdefghijklmnopqrstuvwxyz':
        chars[char.upper()] = (chars[char][0], True)
    return chars


_CHARACTERS = _build_characters()

# virDomainSendKey() refuses more than VIR_DOMAIN_SEND_KEY_MAX_KEYS
# codes in one call, so a chord longer than that is refused here with a
# message naming the key rather than by libvirt with one naming nothing.
MAX_CHORD_KEYS = 16


def chord_for_character(char: str) -> list[int]:
    """The keycodes which type one character."""
    entry = _CHARACTERS.get(char)
    if entry is None:
        raise exceptions.InvalidKeyName(
            'no key types %r; send it as a key name instead' % char)
    (code, shifted) = entry
    return [KEY_LEFTSHIFT, code] if shifted else [code]


def chord_for_key(key: Any) -> list[int]:
    """The keycodes for one named key, or for a chord such as ctrl+alt+delete.

    A single character key is taken literally, so that '+' and '-' name
    themselves rather than being read as chord separators.
    """
    if not isinstance(key, str) or not key:
        raise exceptions.InvalidKeyName('%r is not a key name' % key)

    codes: list[int] = []
    for part in ([key] if len(key) == 1 else key.split('+')):
        if len(part) == 1:
            codes.extend(chord_for_character(part))
            continue

        # virsh spells these KEY_ENTER, so accept that as well as the
        # bare name a caller is more likely to type.
        name = part.lower()
        if name.startswith('key_'):
            name = name[len('key_'):]
        if name not in _NAMED:
            raise exceptions.InvalidKeyName('%r is not a known key' % part)
        codes.append(_NAMED[name])

    if len(codes) > MAX_CHORD_KEYS:
        raise exceptions.InvalidKeyName(
            '%r presses more than %d keys at once' % (key, MAX_CHORD_KEYS))
    return codes


def chords_for_text(text: str) -> list[list[int]]:
    """One chord per character of text."""
    return [chord_for_character(char) for char in text]


def chords_for_keys(keys: list[Any]) -> list[list[int]]:
    """One chord per named key or chord."""
    return [chord_for_key(key) for key in keys]
