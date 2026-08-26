# Copyright 2019 Michael Still and contributors
"""Retry a request while its answer is transient.

Kept free of imports from the rest of this suite (and of
shakenfist_client, which is not a test dependency of the repository) so
the unit tests in shakenfist/tests can load it by path and exercise the
loop with a fake clock. The functional suite is a client of a deployed
cluster and is not otherwise importable from there.
"""

import time


def retry_while_transient(request, transient_statuses, deadline,
                          clock=time.time, sleep=time.sleep, interval=10):
    """Call request() until its status stops being transient, or time is up.

    request returns a (status, body) pair. A status in transient_statuses
    is retried every interval seconds until clock() passes deadline, at
    which point the transient answer is returned as it stands -- giving
    up is the caller's assertion to fail with the body in hand, not an
    exception from here. Any other status is returned immediately, so a
    refusal the caller means to assert on is never waited out into
    something else.
    """
    while True:
        status, body = request()
        if status not in transient_statuses or clock() > deadline:
            return status, body
        sleep(interval)
