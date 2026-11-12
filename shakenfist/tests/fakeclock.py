# Copyright 2026 Michael Still and contributors

"""A clock a test moves by hand.

Four test modules had written the same few lines: an object holding a
time, callable so it can stand in for ``time.time`` or
``time.monotonic``, which the test advances to make a loop's cadence
exact rather than waiting for a real second to pass. A fifth would have
written them again.

What belongs here is only that idea. A clock which answers in
``datetime`` objects, one which replays a canned list of times, and one
which stands in for the whole ``time`` module including ``sleep`` are
different stand-ins, and they stay with the tests which need them under
names which say what they are.
"""


class FakeClock:
    """A time, callable, which only moves when a test moves it."""

    def __init__(self, now=1000000000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        """Move the clock forward, as a sleep or a slow call would."""
        self.now += seconds
        return self.now
