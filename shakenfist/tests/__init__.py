# Logging for tests is configured via the SHAKENFIST_LOG_TO_STDOUT environment
# variable in tox.ini. When set to '1', shakenfist_utilities.logs.setup() will
# write to stdout instead of syslog, allowing stestr to capture logs and only
# display them for failing tests.
