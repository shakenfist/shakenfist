from unittest import mock

import testtools


class ShakenFistTestCase(testtools.TestCase):
    def setUp(self):
        super().setUp()

        # Logging is configured in shakenfist/tests/__init__.py to write to
        # stdout, which stestr captures and only displays for failing tests.

        self.mock_add_event_multi = mock.patch(
            'shakenfist.eventlog.add_event_multi')
        self.mock_add_event_multi.start()
        self.addCleanup(self.mock_add_event_multi.stop)
