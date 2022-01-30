import logging
import mock
import testtools


class ShakenFistTestCase(testtools.TestCase):
    def setUp(self):
        super(ShakenFistTestCase, self).setUp()

        # Remove any syslog handlers
        for name, v in logging.Logger.manager.loggerDict.items():
            if not isinstance(v, logging.PlaceHolder):
                for h in v.handlers:
                    if h.__class__ == logging.handlers.SysLogHandler:
                        logging.getLogger(name).removeHandler(h)

        # Add log handler to stderr
        logging.getLogger().addHandler(logging.StreamHandler())
        logging.getLogger().setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_add_event = mock.patch('shakenfist.eventlog.add_event')
        self.mock_add_event.start()
        self.addCleanup(self.mock_add_event.stop)
