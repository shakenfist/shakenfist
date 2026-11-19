# Logging for tests is configured via the SHAKENFIST_LOG_TO_STDOUT environment
# variable in tox.ini. When set to '1', shakenfist_utilities.logs.setup() will
# write to stdout instead of syslog. That stdout is also the stestr worker's
# subunit result stream, so ShakenFistTestCase redirects those handlers into
# memory for the duration of each test and reports what they captured only for
# a test which did not pass -- see shakenfist/tests/logcapture.py and issue
# #4991.
