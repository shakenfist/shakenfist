# Copyright 2019 Michael Still and contributors
"""Tests for state message clipping (issue 4112).

``object_states.message`` is ``sa.String(255)``, so a longer message
used to be rejected by MariaDB with DataError 1406 -- and because that
write happened inside instance error handling, the failure cascaded
into skipped instance cleanup. The State model now clips the message at
construction time, which covers both the client-side write path
(baseobject) and the database daemon's SetObjectState servicer.
"""

from shakenfist.schema.object_state import clip_state_message
from shakenfist.schema.object_state import State
from shakenfist.schema.object_state import STATE_MESSAGE_MAX_LENGTH
from shakenfist.tests import base


class ClipStateMessageTestCase(base.ShakenFistTestCase):
    def test_none_unchanged(self):
        self.assertIsNone(clip_state_message(None))

    def test_short_message_unchanged(self):
        self.assertEqual('all fine', clip_state_message('all fine'))

    def test_boundary_message_unchanged(self):
        msg = 'x' * STATE_MESSAGE_MAX_LENGTH
        self.assertEqual(msg, clip_state_message(msg))

    def test_long_message_clipped_with_ellipsis(self):
        # 549 characters is the length of the qemu-img failure message
        # from the issue 4112 incident.
        clipped = clip_state_message('x' * 549)
        self.assertEqual(STATE_MESSAGE_MAX_LENGTH, len(clipped))
        self.assertTrue(clipped.endswith('...'))
        self.assertEqual('x' * (STATE_MESSAGE_MAX_LENGTH - 3), clipped[:-3])


class StateMessageClipTestCase(base.ShakenFistTestCase):
    def test_long_message_clipped_at_construction(self):
        state = State(value='creating-error', update_time=1.0, message='y' * 549)
        self.assertEqual(STATE_MESSAGE_MAX_LENGTH, len(state.message))
        self.assertTrue(state.message.endswith('...'))

    def test_short_message_unchanged(self):
        state = State(value='creating-error', update_time=1.0,
                      message='qemu-img failed')
        self.assertEqual('qemu-img failed', state.message)

    def test_no_message(self):
        state = State(value='created', update_time=1.0)
        self.assertIsNone(state.message)
