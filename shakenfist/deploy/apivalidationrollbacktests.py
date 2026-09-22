# Copyright 2019 Michael Still and contributors

"""Assertions for the documented API_VALIDATION_MODE rollback.

Issue 4253: `warn` and `off` are the operator's rollback for the whole
request validation layer, and until this script they had never been
exercised against a deployed cluster -- the unit suite proves the
parsing at both modes, but it cannot see the config rendering, the
process restart, or whether the handler guards still hold behind a real
gunicorn.

This runs from nodelifecycletests.sh, which is where cluster-disturbing
checks live: the stestr cluster suite runs many tests concurrently
against one shared cluster, so a cluster-wide mode flip or an sf-api
restart inside it would fail unrelated tests. The shell script performs
the operator's documented actions (`sf-ctl set-config
API_VALIDATION_MODE warn`, restart `sf-api`); this script asserts what
a caller sees afterwards:

* at `enforce` (the default, asserted before the flip and again after
  the roll-forward): an undeclared body key and a wrong-typed declared
  parameter are refused with the exact published shapes, carrying no
  interpreter text;
* at `warn`: the wrong-typed request now succeeds unchanged, while the
  three phase 7 handler guards -- a diskspec asking for neither size
  nor base, a null video model, and a null network_uuid on interface
  hotplug -- still refuse. That is decisions D34 and D42 (a guard
  keeps working under the rollback) proven on a real cluster.

Issue 4253's sketch asked for the *undeclared key* to be shown
succeeding at warn, and that is not what warn does: an undeclared key
merges into the handler's kwargs and raises TypeError at the call,
which since phase 5 is a recorded 500 answering `server error`
(decision D25 -- warn restores the pre-validation behaviour, and the
pre-validation behaviour of this input was never success). That 500 is
pinned in the unit suite (test_request_validation.py's
test_warn_mode_changes_no_response); it is not driven here because
deliberately provoking recorded server exceptions on the CI cluster
would trip the job's log checks. The input which really is refused at
enforce and answered normally at warn is a wrong-typed declared
parameter the handler tolerates, so that is the success probe.

Configuration comes from the environment the way every other CI client
does (sfrc: SHAKENFIST_API_URL, SHAKENFIST_NAMESPACE, SHAKENFIST_KEY).
Only shakenfist_client and requests are imported, because this runs
from the deployed /srv/shakenfist/venv, which does not carry the test
tree's dependencies.
"""

import argparse
import json
import os
import sys
import time

import requests
from shakenfist_client import apiclient


# A subset of the markers test_api_validation.py and
# test_request_validation.py hold refusal bodies to; repeated here for
# the reason the cluster suite gives -- this runs against a remote
# cluster and cannot import the server's test tree.
INTERPRETER_TEXT_MARKERS = (
    'got an unexpected keyword argument',
    'Traceback',
    'TypeError',
)

# GET /instances declares exactly one body parameter, 'all', so the
# second key here is undeclared -- the same probe the cluster suite's
# TestUndeclaredParameterRefused sends at enforce. Only ever sent at
# enforce: see the module docstring for why warn answers it with a
# recorded 500 rather than success.
UNDECLARED_BODY = {'all': False, 'no_such_parameter': 'zzz'}

# The same endpoint's one declared parameter with a value no boolean
# reading accepts. Enforce refuses it as a type mismatch; the handler
# reads it truthily and answers the listing, so at warn it succeeds --
# which makes it the probe that shows enforcement is really off.
BAD_BOOLEAN_BODY = {'all': 'banana'}


def log(msg):
    print(msg, flush=True)


def fail(msg):
    log('FAIL: %s' % msg)
    sys.exit(1)


def await_ready(deadline_seconds=120):
    """Wait for the sf-api the assertions will talk to to answer ready.

    The shell script restarts sf-api between invocations of this
    script, and gunicorn takes a few seconds to come back. /readyz is
    unauthenticated and answers 200 only once the API's database
    checker has passed, so it is the right gate for 'the restart the
    rollback depends on has completed'.
    """
    base_url = os.environ.get('SHAKENFIST_API_URL')
    if not base_url:
        fail('SHAKENFIST_API_URL is not set; source /etc/sf/sfrc first')

    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        try:
            r = requests.get('%s/readyz' % base_url, timeout=5)
            if r.status_code == 200:
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    fail('sf-api at %s did not answer /readyz within %d seconds'
         % (base_url, deadline_seconds))


def expect_400(client, method, url, body, message):
    """Assert a request is refused with exactly this 400."""
    try:
        client._request_url(method, url, data=body)
    except apiclient.RequestMalformedException as e:
        parsed = json.loads(e.text)
        if parsed != {'error': message, 'status': 400}:
            fail('%s %s answered a different refusal than expected.\n'
                 '  expected: %s\n  received: %s'
                 % (method, url, message, e.text))
        for marker in INTERPRETER_TEXT_MARKERS:
            if marker in e.text:
                fail('%s %s refusal leaked interpreter text: %s'
                     % (method, url, e.text))
        return
    fail('%s %s with %s was not refused' % (method, url, body))


def assert_enforce(client):
    """The enforce default refuses both probes.

    Run before the flip as the control which proves the later warn
    results are the mode's doing, and again after the roll-forward as
    proof the cluster came back to where it started.
    """
    try:
        client._request_url('GET', '/instances', data=UNDECLARED_BODY)
    except apiclient.RequestMalformedException as e:
        parsed = json.loads(e.text)
        if parsed != {'error': 'no_such_parameter: not declared by this endpoint', 'status': 400}:
            fail('enforce refused the undeclared key with an unexpected body: %s' % e.text)
        for marker in INTERPRETER_TEXT_MARKERS:
            if marker in e.text:
                fail('enforce refusal leaked interpreter text: %s' % e.text)
        log('OK: enforce refuses an undeclared body key')
    else:
        fail('an undeclared body key was not refused at enforce -- either '
             'API_VALIDATION_MODE is not enforce, or the restart did not happen')

    expect_400(
        client, 'GET', '/instances', BAD_BOOLEAN_BODY,
        'all: Not a valid boolean.')
    log('OK: enforce refuses a wrong-typed declared parameter')


def _create_minimal_instance(client):
    """Create the smallest possible instance, waiting out a 507.

    A minimal empty disk and no network: the instance never has to
    boot, it just has to exist and be non-terminal so the hotplug
    endpoint reaches its netdesc guard. The create is raw so the
    request is issued exactly once per attempt and the uuid is usable
    immediately, without the client's own wait-for-created loop. A 507
    is retried under a fresh name (the refused create error-deletes
    asynchronously, holding the old name) because right after cluster
    boot a node may briefly refuse work it has room for.
    """
    deadline = time.time() + 420
    attempt = 0
    while True:
        attempt += 1
        try:
            r = client._request_url('POST', '/instances', data={
                'name': 'rollback-hotplug-%d' % attempt,
                'cpus': 1,
                'memory': 128,
                'disk': [{'size': 1, 'type': 'disk'}],
            })
            inst = r.json()
            break
        except apiclient.InsufficientResourcesException as e:
            if time.time() > deadline:
                fail('could not create the hotplug test instance: %s' % e.text)
            log('  ... create refused with a 507, retrying: %s' % e.text)
            time.sleep(15)

    # Wait out the create before hot plugging into (and then deleting)
    # the instance, so neither request races the create tasks still on
    # the queue. The guard itself only needs a non-terminal instance,
    # but a delete racing a create is exactly the kind of log noise the
    # job's log checks exist to notice.
    while time.time() < deadline:
        inst = client.get_instance(inst['uuid'])
        if inst['state'] == 'created':
            return inst
        if inst['state'] not in ('initial', 'preflight', 'creating'):
            fail('the hotplug test instance entered state %s rather than '
                 'created' % inst['state'])
        time.sleep(5)
    fail('the hotplug test instance did not reach created before the deadline')


def assert_warn(client):
    """What warn promises: enforcement is off, the guards are not."""

    # 1. The request enforce refuses now succeeds, answered exactly as
    #    it always was -- a JSON list of instances. The undeclared-key
    #    probe is deliberately not sent here: at warn it is a recorded
    #    500 (decision D25), pinned in the unit suite, and provoking
    #    recorded server exceptions would trip the job's log checks.
    r = client._request_url('GET', '/instances', data=BAD_BOOLEAN_BODY)
    body = r.json()
    if not isinstance(body, list):
        fail('GET /instances with a wrong-typed declared parameter did not '
             'answer an instance list at warn: %s' % body)
    log('OK: warn accepts the wrong-typed parameter enforce refuses')

    # 2. The three phase 7 handler guards, which D42 keeps precisely so
    #    the rollback does not hand back a newly unguarded API. Each
    #    message is asserted in full, because at warn a 400 alone
    #    cannot tell a guard's answer from a stray validation refusal.
    expect_400(
        client, 'POST', '/instances',
        {'name': 'rollback-disk', 'cpus': 1, 'memory': 128, 'disk': [{}]},
        'disk specification must specify at least one of size or base')
    log('OK: the size-or-base diskspec guard holds at warn')

    expect_400(
        client, 'POST', '/instances',
        {'name': 'rollback-video', 'cpus': 1, 'memory': 128,
         'disk': [{'size': 1, 'type': 'disk'}], 'video': {'model': None}},
        'video specification requires "model"')
    log('OK: the null video model guard holds at warn')

    # The null network_uuid guard, at the endpoint issue 4223's symptom
    # was reachable on: interface hotplug, which has no scheduler in
    # front of it and used to answer 200 and create an interface on a
    # network the caller never named.
    inst = _create_minimal_instance(client)
    try:
        expect_400(
            client, 'POST', '/instances/%s/interfaces' % inst['uuid'],
            {'network': {'network_uuid': None}},
            'network specification is missing network_uuid')
        log('OK: the null network_uuid hotplug guard holds at warn')

        interfaces = client._request_url(
            'GET', '/instances/%s/interfaces' % inst['uuid']).json()
        if interfaces:
            fail('the refused hotplug created an interface anyway: %s'
                 % interfaces)
        log('OK: the refused hotplug created no interface')
    finally:
        client.delete_instance(inst['uuid'], async_request=True)

    # The delete above is asynchronous; wait for it so the lifecycle
    # tests that follow this exercise start from the empty cluster
    # their instance counting assumes.
    deadline = time.time() + 300
    while time.time() < deadline:
        listed = [i['uuid'] for i in client.get_instances()]
        if inst['uuid'] not in listed:
            return
        time.sleep(5)
    fail('the hotplug test instance %s did not delete within 300 seconds'
         % inst['uuid'])


def main():
    parser = argparse.ArgumentParser(
        description='Assert the API validation mode a cluster is in.')
    parser.add_argument(
        '--mode', choices=['enforce', 'warn'], required=True,
        help='The API_VALIDATION_MODE sf-api was restarted into.')
    args = parser.parse_args()

    await_ready()
    client = apiclient.Client(async_strategy=apiclient.ASYNC_PAUSE)

    if args.mode == 'enforce':
        assert_enforce(client)
    else:
        assert_warn(client)
    log('API validation %s assertions passed' % args.mode)


if __name__ == '__main__':
    main()
