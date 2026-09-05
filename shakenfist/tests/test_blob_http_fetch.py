# Copyright 2026 Michael Still and contributors
#
# A blob under HTTP fetch has no references -- the artifact index
# reference is only created once the fetch succeeds -- so the cluster
# daemon's unreferenced blob reaper only leaves it alone while its
# last_used is fresh. http_fetch() must therefore persist last_used as
# a heartbeat while data is flowing, or any fetch taking longer than
# the reaper's 300 second grace period is reaped mid-flight and the
# fetch fails at the very end trying to move the blob from deleted to
# created (issue 4000).

import os
import tempfile
from unittest import mock

from shakenfist.tests import base
from shakenfist import blob


class FakeClock:
    """Return canned times, then repeat the last one forever."""

    def __init__(self, times):
        self.times = list(times)
        self.last = self.times[-1]

    def __call__(self):
        if self.times:
            self.last = self.times.pop(0)
        return self.last


class FakeResponse:
    def __init__(self, chunks, content_length=None):
        self.chunks = chunks
        self.headers = {}
        if content_length is not None:
            self.headers['Content-Length'] = str(content_length)

    def iter_content(self, chunk_size=None):
        yield from self.chunks


class HttpFetchHeartbeatTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        self.blob_uuid = '11111111-1111-4111-8111-111111111111'
        self.mock_blob = mock.MagicMock()
        self.mock_blob.uuid = self.blob_uuid

        self.filepath = mock.patch(
            'shakenfist.blob.Blob.filepath',
            return_value=os.path.join(self.tempdir.name, self.blob_uuid))
        self.filepath.start()
        self.addCleanup(self.filepath.stop)

        self.add_event = mock.patch('shakenfist.blob.add_event_multi')
        self.mock_add_event = self.add_event.start()
        self.addCleanup(self.add_event.stop)

    def test_progress_events_persist_a_heartbeat(self):
        # Four chunks, each crossing a ten percent boundary, so the
        # progress branch fires on every chunk irrespective of time.
        resp = FakeResponse(
            [b'x' * 25, b'x' * 25, b'x' * 25, b'x' * 25], content_length=100)

        blob.http_fetch(
            'http://example.com/image', resp, self.mock_blob, [])

        self.assertEqual(4, self.mock_blob.record_usage.call_count)

    def test_slow_fetch_without_content_length_heartbeats_on_time(self):
        # No Content-Length means no percentage tracking, so only the 30
        # second timer can fire. The clock: loop start at 1000, the first
        # chunk observes 1031 (more than 30 seconds later, so the
        # heartbeat fires and last_event resets to 1031), the second
        # chunk observes 1040 (only 9 seconds later, so it does not).
        resp = FakeResponse([b'x' * 10, b'x' * 10])

        with mock.patch('shakenfist.blob.time.time',
                        side_effect=FakeClock([1000.0, 1031.0, 1031.0, 1040.0])):
            blob.http_fetch(
                'http://example.com/image', resp, self.mock_blob, [])

        self.assertEqual(1, self.mock_blob.record_usage.call_count)

    def test_fast_fetch_does_not_heartbeat(self):
        # A single small chunk which crosses no percentage boundary and
        # finishes well inside 30 seconds writes nothing: the heartbeat
        # is tied to progress reporting, not to every chunk.
        resp = FakeResponse([b'x' * 5], content_length=1000000)

        blob.http_fetch(
            'http://example.com/image', resp, self.mock_blob, [])

        self.mock_blob.record_usage.assert_not_called()
        # The fetch still completed and registered the blob.
        self.mock_blob.register.assert_called_once_with(
            request_checksums=False)
