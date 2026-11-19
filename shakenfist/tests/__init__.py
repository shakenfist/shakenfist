# Logging for tests is configured via the SHAKENFIST_LOG_TO_STDOUT environment
# variable in tox.ini. When set to '1', shakenfist_utilities.logs.setup() will
# write to stdout instead of syslog. ShakenFistTestCase.setUp() then holds
# those records in a per-test buffer and attaches them to the result only when
# the test fails -- see shakenfist/tests/log_capture.py and issue #4991.
