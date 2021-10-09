import logging
import mock
import testtools


class ShakenFistTestCase(testtools.TestCase):
    def setUp(self):
        super(ShakenFistTestCase, self).setUp()

        self.event = mock.patch(
            'shakenfist.logutil.SFCustomAdapter._emit_event')
        self.mock_event = self.event.start()
        self.addCleanup(self.event.stop)

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
