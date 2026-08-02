# The format of key secrets Shaken Fist generates for itself.
#
# Phase 3 of the auth federation plan is where the cluster starts
# minting key secrets rather than only accepting operator-chosen ones,
# so it is where the generated form gets a recognisable shape:
#
#     sfk_<32 chars base62 random><6 chars base62 CRC32 checksum>
#
# following the pattern GitHub (ghp_), GitLab (glpat-), Stripe
# (sk_live_) and Slack (xoxb-) use. The prefix makes a leaked
# credential greppable in logs and repositories; the checksum lets a
# scanner reject lookalikes without calling an API, which is what makes
# scanning at volume tolerable rather than alert spam.
#
# This costs nothing cryptographically. A bearer credential is a random
# identifier, not ciphertext, so a fixed prefix is a label beside the
# random part rather than a revealed piece of it -- the entropy of the
# random body is unchanged at roughly 190 bits.
#
# The prefix is reserved: operator-supplied secrets may not start with
# it. That reservation is what makes rejecting a bad checksum at /auth
# sound rather than a guess, because /auth cannot know which stored key
# a presented secret is meant to match until it bcrypt-compares against
# each one. See external_api/auth.py.

import secrets
import zlib


PREFIX = 'sfk_'
BODY_LENGTH = 32
CHECKSUM_LENGTH = 6

# Deliberately the same alphabet GitHub uses, so that off-the-shelf
# scanner rules need only the prefix and length changed.
ALPHABET = ('ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            'abcdefghijklmnopqrstuvwxyz'
            '0123456789')

TOTAL_LENGTH = len(PREFIX) + BODY_LENGTH + CHECKSUM_LENGTH


def _base62(value, width):
    """Encode an integer as fixed-width base62, most significant first."""
    out = []
    for _ in range(width):
        value, remainder = divmod(value, len(ALPHABET))
        out.append(ALPHABET[remainder])
    return ''.join(reversed(out))


def _checksum(payload):
    """The base62 CRC32 of everything preceding the checksum."""
    return _base62(zlib.crc32(payload.encode('utf-8')), CHECKSUM_LENGTH)


def generate():
    """A new cluster-minted key secret."""
    body = ''.join(secrets.choice(ALPHABET) for _ in range(BODY_LENGTH))
    payload = PREFIX + body
    return payload + _checksum(payload)


def has_prefix(secret):
    """Does this secret claim to be cluster-minted?

    Used to enforce the reservation at key creation, and to decide
    whether a presented secret is a candidate for early rejection.
    """
    return isinstance(secret, str) and secret.startswith(PREFIX)


def looks_valid(secret):
    """Is this a well-formed cluster-minted secret?

    False for anything that carries the prefix but does not check out
    -- wrong length, or a checksum that does not match the body. It
    says nothing about whether the secret is *current*, only that it
    was shaped by us and has not been corrupted in transit.
    """
    if not has_prefix(secret):
        return False
    if len(secret) != TOTAL_LENGTH:
        return False

    payload = secret[:-CHECKSUM_LENGTH]
    presented = secret[-CHECKSUM_LENGTH:]
    if any(c not in ALPHABET for c in secret[len(PREFIX):]):
        return False
    return secrets.compare_digest(presented, _checksum(payload))
