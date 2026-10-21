# Copyright 2019 Michael Still and contributors
#
# A native Shaken Fist ansible module for managing instances. This replaces
# the legacy bash shim that shelled out to `sf-client ansible instance`; the
# arg -> API mapping (including the dirtiness comparison) is ported from
# client-python's shakenfist_client/commandline/ansible.py.
from __future__ import annotations

import datetime
import inspect
import json
import math
import random
import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.shakenfist.shakenfist.plugins.module_utils.sf_connection import check_connection
from ansible_collections.shakenfist.shakenfist.plugins.module_utils.sf_connection import connection_argument_spec
from ansible_collections.shakenfist.shakenfist.plugins.module_utils.sf_connection import make_client

from shakenfist_client import apiclient


DOCUMENTATION = r'''
---
module: sf_instance
short_description: Create, replace and delete Shaken Fist instances.
description:
  - Idempotently ensure a Shaken Fist instance is present or absent.
  - Shaken Fist instances are immutable. When the requested specification
    differs from the existing instance, the instance is deleted and recreated.
  - Imports the C(shakenfist_client) SDK and talks to the Shaken Fist REST
    API directly; the control node only needs
    C(pip install shakenfist-client).
options:
  name:
    description: The name of the instance. One of O(name) or O(uuid) is required.
    required: false
    type: str
  uuid:
    description: The UUID of the instance. One of O(name) or O(uuid) is required.
    required: false
    type: str
  cpu:
    description: The number of vCPUs. Required when creating an instance.
    required: false
    type: int
  ram:
    description: The amount of RAM in megabytes. Required when creating.
    required: false
    type: int
  disks:
    description:
      - A list of simple disk specifications, each either a size in GB
        (for example C("10")) or C(size@base) (for example C(10@debian:11)).
    required: false
    type: list
    elements: str
  diskspecs:
    description:
      - A list of full comma separated disk key=value specifications (for
        example C(size=20,type=cdrom)).
    required: false
    type: list
    elements: str
  networks:
    description: A list of network UUIDs to attach with default settings.
    required: false
    type: list
    elements: str
  networkspecs:
    description:
      - A list of comma separated network key=value specifications (for
        example C(network_uuid=...,address=10.0.0.5,float=True)).
    required: false
    type: list
    elements: str
  ssh_key:
    description: An SSH public key to inject via the config drive.
    required: false
    type: str
  user_data:
    description: Base64 encoded cloud-init user data.
    required: false
    type: str
  placement:
    description: Force placement onto a named node.
    required: false
    type: str
  video:
    description:
      - The instance's video card and console, as a videospec. A key left
        out takes the server's default, so giving only a model of C(vga)
        asks for a vga card with 16384 KiB of memory and a SPICE console.
      - Like the other specifications it may also be written as a
        key=value string, for example C(model=qxl,memory=65536).
      - Unset, the server applies its default videospec, and an existing
        instance's video is not compared.
    required: false
    type: dict
    suboptions:
      model:
        description:
          - The video card model, for example C(cirrus), C(vga) or C(qxl).
            The set which works is the hypervisor's.
        type: str
        default: cirrus
      memory:
        description: The video card's memory, in KiB.
        type: int
        default: 16384
      vdi:
        description:
          - The console protocol to offer. C(spiceconcurrent) allows
            limited multi-user sessions, and C(spicedebug) behaves like
            C(spice) with verbose SPICE server logging.
        type: str
        choices: [vnc, spice, spiceconcurrent, spicedebug]
        default: spice
  nvram_template:
    description: The NVRAM template to use (for UEFI / secure boot).
    required: false
    type: str
  configdrive:
    description: The config drive style to use.
    required: false
    type: str
  side_channels:
    description: >-
      A list of side channel names to expose to the instance. If not
      specified, the server applies its default set (currently sf-agent
      and sf-agent2, which the in-guest agent requires). Pass an explicit
      empty list to create the instance with no side channels at all,
      which disables the in-guest agent.
    required: false
    type: list
    elements: str
  uefi:
    description: Whether to boot the instance with UEFI.
    required: false
    type: bool
  secure_boot:
    description: Whether to enable secure boot (implies UEFI).
    required: false
    type: bool
  metadata:
    description: A dictionary of metadata to set on the instance.
    required: false
    type: dict
  await:
    description: Whether to block until the instance has finished creating.
    required: false
    default: false
    type: bool
  await_timeout:
    description:
      - How long to wait, in seconds, when O(await) is true.
      - This is the budget for the whole operation rather than for the
        wait at the end of it. Where an instance is being replaced, the
        time spent deleting the old one and creating the new one is
        deducted from this number before the wait begins, so the task no
        longer takes the sum of two independent budgets on the same
        condition.
      - It is a budget rather than a hard deadline. This module cannot
        interrupt a deletion or a creation already underway, so a task
        can still overrun -- it just cannot spend the same budget twice.
        That matters most with a shakenfist-client older than the one
        which grew a timeout argument on instance creation (anything up
        to and including v0.8.3), because such a client cannot be told
        not to wait while creating.
    required: false
    default: 600
    type: int
  capacity_wait:
    description:
      - How long, in seconds, to keep replaying a create which the cluster
        refused for lack of capacity. The default of 0 turns the wait off,
        and the module then fails on the first refusal exactly as it
        always has.
      - Two refusals are replayed, and nothing else. The first is a 507
        from the create whose JSON body has C(transient) set to true,
        which the server sends only for a scheduler stage that waiting
        can clear. The second, only when O(await) is true, is an instance
        which preflight aborted with the error message C(Unable to find
        suitable node) or C(Too many start attempts). A 507 without the
        marker (for example an exhausted address pool), a C(transient)
        false 507, a body which is not JSON, a 409, an affinity failure
        and every other error fail at once.
      - A refused create still leaves an instance record behind, which
        the server parks in state C(error) and which keeps its name until
        someone deletes it. Before each replay the module waits for that
        record to reach C(error), deletes it, and waits for it to reach
        C(deleted), so that the replay can reuse the name without making a
        lookup by name ambiguous. The time this takes is part of the
        wait.
      - Replays are at least 60 seconds apart, plus up to 15 seconds of
        random jitter, so a value under 60 never replays at all.
      - This is separate from O(await_timeout) rather than taken out of
        it. The capacity clock starts with the first refused attempt and
        stops when an attempt is accepted, and the accepted instance's
        wait still gets the whole of O(await_timeout). A task's worst case
        is therefore O(capacity_wait) plus O(await_timeout), plus the
        deletion of any instance being replaced.
      - When the budget runs out the task fails with the server's last
        refusal, followed by how long it waited, how many attempts it made
        and why it stopped. RV(capacity_waited) says what happened either
        way, and is returned too when a task fails for another reason after
        a refusal.
    required: false
    default: 0
    type: int
  state:
    description: Whether the instance should be present or absent.
    required: false
    default: present
    choices: [present, absent]
    type: str
  api_url:
    description:
      - Base URL of the Shaken Fist API. This and O(key) are only used
        when they and O(namespace) are all supplied; passing either of them
        without the other two is an error rather than a silent fall back.
        When they are omitted credentials are auto-discovered from the
        environment and C(sfrc) config exactly like the C(sf-client) CLI,
        which is the usual way to call this.
    required: false
    type: str
  namespace:
    description:
      - The namespace the instance belongs to, which is also the namespace
        authenticated as. Because it names the object and not just an
        identity it may be supplied on its own, unlike O(api_url) and
        O(key).
    required: false
    type: str
  key:
    description: The authentication key for O(namespace).
    required: false
    type: str
    no_log: true
author:
  - Michael Still and contributors
'''

EXAMPLES = r'''
- name: Create an instance
  shakenfist.shakenfist.sf_instance:
    name: myvm
    cpu: 2
    ram: 2048
    disks:
      - 10@debian:11
    networks:
      - "{{ mynet_uuid }}"
    state: present
  register: result

- name: Delete an instance by uuid
  shakenfist.shakenfist.sf_instance:
    uuid: "{{ result['meta']['uuid'] }}"
    state: absent
'''

RETURN = r'''
changed:
  description: Whether the module created or replaced the instance.
  returned: always
  type: bool
failed:
  description: Whether the module failed.
  returned: always
  type: bool
meta:
  description: The instance object as returned by the API, when available.
  returned: success
  type: dict
log:
  description: A list of human readable progress messages, for debugging.
  returned: always
  type: list
  elements: str
capacity_waited:
  description:
    - What O(capacity_wait) did. Returned on success whether or not it is
      enabled, and with any failure which comes after a refused create.
      A failure with no refusal before it does not return it.
  returned: on success when state is present, and on failure once a create was refused
  type: dict
  contains:
    seconds:
      description:
        - Seconds spent waiting for capacity, rounded up. This runs from
          the start of the first refused attempt to the start of the
          accepted one, or to the moment the wait gave up. When the task
          failed for another reason, it runs to the start of the last
          attempt, or to the failure itself if it came while waiting for
          the refused record to be deleted. It is 0 when nothing was
          refused.
      type: int
    attempts:
      description: The number of creates this task sent. 0 when no create was needed.
      type: int
    outcome:
      description:
        - C(not_refused) when no create was refused, C(placed) when a
          replay was accepted, C(exhausted) when the budget ran out, and
          C(failed) when a create was refused and the task then failed for
          some other reason. C(failed) covers both a replay which was
          accepted and then failed, and a replay refused for a reason
          waiting cannot clear, so it is not evidence that capacity was
          still short; the failure message gives the cause.
      type: str
    refusals:
      description:
        - One entry per refusal, each with C(at) (UTC, ISO 8601), C(kind)
          (C(api) for a 507 from the create, C(preflight) for a preflight
          abort), C(stage) (the 507 body's stage, or null) and C(message).
      type: list
      elements: dict
    namespace_instances:
      description:
        - How many other active instances the namespace held at the first
          refusal, or null when nothing was refused or the listing failed.
      type: int
'''


# Keys the server adds to a stored disk specification which no caller
# supplied, and which therefore must not contribute to the dirtiness
# comparison. See the comment at the comparison itself in _check_instance().
SERVER_POPULATED_DISK_KEYS = ['blob_uuid', 'disk_base']

# The pacing of capacity_wait's replays, in seconds. The spacing is not the
# 15 second Retry-After the server sends with a transient 507: that figure
# is a fixed constant rather than a forecast, and every replay creates an
# instance record and allocates addresses before it is refused, so a wait
# of half an hour at 15 seconds would cost a refusing cluster about 120
# dead records rather than about 28. The jitter stops the creates of a
# fleet of builds which were refused together from arriving together again.
CAPACITY_REPLAY_SPACING = 60
CAPACITY_REPLAY_JITTER = 15

# How often to look for the refused instance's record while waiting for it
# to be deleted. The same cadence the client's own await uses.
CAPACITY_RECORD_POLL = 5

# The two preflight abort messages which mean "no node had room". The
# server records a preflight abort on the instance only as a message string
# -- preflight's LowResourceException carries a stage, but AbortInstanceStart
# discards it -- so this is string matching, and a server change which
# rewords either message quietly stops these aborts being replayed. That is
# the safe direction to fail in: they then fail the task as they always
# did. The affinity and "Requested node ..." aborts are different strings,
# deliberately absent: waiting does not satisfy an affinity rule or free a
# node the caller pinned to.
#
# The match is not exact about the cause, though. Too many start attempts
# is preflight's limit on placement attempts, which it checks before
# anything else, so it ends any instance which has been placed too often --
# including one whose attempts failed because a node could not schedule for
# itself, and one pinned to a node, which would otherwise have ended with a
# "Requested node" message. Those are replayed too. That is accepted rather
# than worked around: this module cannot pin an instance to a node, a node
# which cannot schedule is as likely to recover within the wait as a full
# one is to drain, and the replays are bounded by the budget either way.
PREFLIGHT_CAPACITY_MESSAGES = (
    'Unable to find suitable node',
    'Too many start attempts',
)

# The states a preflight-aborted instance can be read back in. preflight
# calls enqueue_delete_due_error(), which moves the instance to
# preflight-error and queues a delete, and the delete of an instance in an
# -error state ends in state error rather than deleted. That delete is
# usually done within seconds, so by the time the client's await has seen
# the error and this module reads the instance again, error is the likelier
# of the two. The message, not the state, is what identifies the abort.
PREFLIGHT_ABORT_STATES = ('preflight-error', 'error')

# The capacity wait's sleep, jitter source and wall clock. They are module
# level names so that the tests can replace them and run a half hour wait
# in no real time. The wait's durations are read from time.monotonic(), the
# clock the operation budget already reads, so that the two can never
# disagree about how long something took.
_sleep = time.sleep
_jitter = random.uniform


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


class InstanceCreationException(Exception):
    ...


def _create_accepts_timeout(client):
    """Does this client's create_instance() take a timeout?

    The collection requires shakenfist-client unpinned, so the control
    node's client is whatever pip resolved and may predate the argument.
    Feature-detect rather than assume: guessing wrong is a TypeError that
    stops every instance creation, and there is no version to test
    against that is not itself a guess about backports.
    """
    try:
        signature = inspect.signature(client.create_instance)
    except (TypeError, ValueError):
        # A callable inspect cannot describe, such as some C
        # implementations. Assume the old shape; the elapsed-time
        # deduction still applies. Note that a mock does not land here --
        # inspect.signature() happily reports (*args, **kwargs) for one,
        # which has no timeout parameter and so returns False below.
        return False
    return 'timeout' in signature.parameters


def _fail_api_error(module, log, action, error, **extra):
    """Fail through fail_json() so the accumulated log survives.

    The log carries the dirtiness reasoning -- the only record of why the
    module decided to replace an instance. An APIException allowed to
    escape run_module() crashes the module and discards that log, which
    is exactly when it is most needed: a create failure is usually the
    consequence of a dirtiness decision the operator did not expect
    (issue 4060).

    extra is passed through to fail_json() as further return values. Only
    the capacity wait uses it, to return what it waited once a create has
    been refused.
    """
    msg = '%s: %s' % (action, error)
    log.append(msg)
    module.fail_json(msg=msg, meta=None, log=log, **extra)


def _refusal_body(e):
    """The JSON object a 507 carries, or None.

    Read from e.text, which every client release back to v0.8.3 sets.
    e.headers would carry the Retry-After as well, but it exists only on
    clients newer than any release, and the collection requires
    shakenfist-client unpinned -- so the body is the only part of the
    response this module can rely on. A proxy's HTML 507, a truncated body,
    or JSON which is not an object is not a body the server wrote, and
    reads as None.
    """
    try:
        body = json.loads(getattr(e, 'text', None))
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    return body


def _transient_refusal(e):
    """Did the server say this refusal is worth waiting out?

    Only a 507 whose body has transient set to exactly true. The server
    sets it only for a scheduler stage a wait can clear, so this leaves out
    the permanent stages (a request for more vCPUs than any node has), the
    unmarked 507s an exhausted address pool returns, and a 507 from an
    older server which predates the marker. The exception class alone is
    not enough: every one of those is an InsufficientResourcesException
    too (shakenfist/shakenfist#4474 is the same mistake in the functional
    suite).
    """
    if not isinstance(e, apiclient.InsufficientResourcesException):
        return False
    body = _refusal_body(e)
    return body is not None and body.get('transient') is True


def _preflight_capacity_abort(instance):
    """Did preflight abort this instance because no node had room?

    See PREFLIGHT_CAPACITY_MESSAGES and PREFLIGHT_ABORT_STATES for why this
    is a match on the exact message.
    """
    if not instance:
        return False
    return (instance.get('state') in PREFLIGHT_ABORT_STATES and
            instance.get('error_message') in PREFLIGHT_CAPACITY_MESSAGES)


def _is_error_state(state):
    # The same test the client's await applies: the -error transition
    # states and the terminal error state all mean the instance will never
    # be ready.
    return bool(state) and (state == 'error' or state.endswith('-error'))


def _await_record_gone(client, log, ref, namespace, deadline):
    """Wait for a refused create's instance record to stop holding its name.

    ref is the instance's name for a 507 (the response carries no uuid,
    although the server did create a record before refusing it) and its
    uuid for a preflight abort. Returns True once a lookup reports the
    record not found or deleted, and False if deadline (a time.monotonic()
    value) passes first. Nothing else counts as gone.

    A record left by a refusal does not go away on its own. The server
    moves it to an -error state and queues a delete, but the delete of an
    instance in an -error state ends in state error, not deleted, and the
    instance stays there until a caller deletes it again: an error is kept
    for the caller to acknowledge. error is one of the states a lookup by
    name searches, so until then a second instance of the same name makes
    every lookup by that name fail as ambiguous -- and the playbooks which
    wait for capacity find their instances by name. So this acknowledges
    the refused record with a delete of its own, by uuid, and then waits
    for that delete to finish.

    The acknowledgement waits for state error, rather than being sent as
    soon as the record is found in a transitional -error state. A delete
    request aborts the instance's outstanding operations, so sent while the
    server's own delete was still queued it would cancel that delete, take
    its place, and itself end in state error -- needing a third. Sent once
    the server's delete has finished, one is enough.

    It only deletes a record in an error state. Anything healthy which a
    lookup by name finds is some other instance, and is left alone; it is
    not the refused record either, so it never counts as gone, and the
    wait runs out rather than creating a second instance of that name.
    Within this module that cannot happen -- the module only creates when
    no instance of the name exists, or after deleting the one which did --
    short of another caller creating the same name concurrently, or a task
    which identifies its instance by uuid while another of the same name
    exists. A record which an interrupted earlier run left in state error
    never reaches here: the task's first lookup by name finds it, and the
    task either replaces it, deleting it before the create, or adopts it
    and fails in the await. Two such records make that first lookup
    ambiguous, and the task fails there instead.

    An APIException other than not found is raised to the caller: a failed
    lookup is not a refusal, and must not be replayed.
    """
    # Once the record has been found, follow it by uuid: a lookup by name
    # cannot tell it from anything else which takes the name afterwards.
    uuid = None
    acknowledged = False
    while True:
        try:
            if uuid:
                i = client.get_instance(uuid)
            else:
                i = client.get_instance(ref, namespace=namespace)
        except apiclient.ResourceNotFoundException:
            return True
        if not i or i.get('state') == 'deleted':
            return True

        state = i.get('state')
        if not uuid and _is_error_state(state):
            uuid = i['uuid']
        if uuid and state == 'error' and not acknowledged:
            acknowledged = True
            log.append('Deleting the refused instance %s so that its name '
                       'can be reused' % uuid)
            try:
                # async_request for the reason _delete_and_wait() gives:
                # without it the client polls for up to an hour itself.
                client.delete_instance(uuid, async_request=True)
            except apiclient.ResourceNotFoundException:
                return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        _sleep(min(CAPACITY_RECORD_POLL, remaining))


class _CapacityWait:
    """The bounded wait for capacity which capacity_wait asks for.

    One of these exists for every state=present run. With a budget of 0 it
    reads no clock, makes no API call and changes no message: it counts the
    creates it is told about, and reports that nothing was refused.

    The capacity clock is separate from await_timeout, and the two never
    share seconds. It starts with the first refused attempt and stops at
    the start of the attempt which is accepted. Between those it counts the
    pauses between replays, the waits for each refused record to be
    deleted, the creates which were refused, and every await which ended in
    a preflight capacity abort. run_module() deducts what it counted from
    the time it charges to await_timeout, so the accepted instance's await
    is budgeted exactly as if it had been the first attempt. A task's worst
    case is therefore capacity_wait plus await_timeout: a replay is never
    sent after the capacity budget has run out, and the last one sent can
    still spend a whole await.
    """

    def __init__(self, module, client, log, budget, name, namespace):
        self.module = module
        self.client = client
        self.log = log
        self.budget = max(0, budget or 0)
        self.name = name
        self.namespace = namespace

        self.attempts = 0
        self.refusals = []
        self.namespace_instances = None
        self.exhausted = False

        # time.monotonic() values, read only when the budget is not 0.
        self.attempt_started = None
        self.first_refused = None

        # Capacity seconds counted so far; see the class docstring.
        self.seconds = 0

    def start_attempt(self):
        """Note that a create is about to be sent."""
        self.attempts += 1
        if not self.budget:
            return
        self.attempt_started = time.monotonic()
        if self.first_refused is not None:
            self.seconds = self.attempt_started - self.first_refused

    def replayable_api_refusal(self, e):
        return bool(self.budget) and _transient_refusal(e)

    def preflight_abort(self, instance_uuid):
        """Read an instance whose await failed, if this wait might replay it.

        Returns the instance when preflight aborted it for capacity, and
        None otherwise. The client's await raises the same exception for
        every error state, and preflight carries no machine readable reason
        onto the instance, so the instance has to be read again to tell a
        capacity abort from anything else. A failed read is not grounds for
        a replay: the caller reports the await's own failure instead.
        """
        if not self.budget:
            return None
        try:
            instance = self.client.get_instance(instance_uuid)
        except apiclient.APIException as e:
            self.log.append('Reading the instance back after its await '
                            'failed did not work: %s' % e)
            return None
        if _preflight_capacity_abort(instance):
            return instance
        return None

    def refused(self, kind, stage, message, ref, ref_namespace, action, error):
        """Record a refusal, then either wait to replay it or fail the task.

        Returns when the replay may be sent. Otherwise fails the task
        through _fail_api_error(), with action and error as the failure
        would have read without a wait, so the server's own words lead
        the message (kerbside-patches' automatic retry of infrastructure
        failures matches on them).
        """
        if self.first_refused is None:
            self.first_refused = self.attempt_started
            self._count_namespace_instances()
        self.refusals.append({
            'at': _utcnow().isoformat(),
            'kind': kind,
            'stage': stage,
            'message': message,
        })
        self.log.append('Attempt %d was refused for capacity (%s, stage %s): '
                        '%s' % (self.attempts, kind, stage, message))

        deadline = self.first_refused + self.budget
        next_attempt = (self.attempt_started + CAPACITY_REPLAY_SPACING +
                        _jitter(0, CAPACITY_REPLAY_JITTER))
        if next_attempt > deadline:
            self._exhaust(action, error,
                          'the next replay would have been sent after the '
                          'capacity_wait budget of %d seconds ran out'
                          % self.budget)

        try:
            gone = _await_record_gone(
                self.client, self.log, ref, ref_namespace, deadline)
        except apiclient.APIException as e:
            # Failed inside the wait, so the wait ran until now.
            self.seconds = time.monotonic() - self.first_refused
            _fail_api_error(
                self.module, self.log,
                'Waiting for the refused instance to be deleted failed', e,
                **self.failure())
        if not gone:
            self._exhaust(action, error,
                          'the refused instance %s still held its name when '
                          'the capacity_wait budget of %d seconds ran out'
                          % (ref, self.budget))

        pause = next_attempt - time.monotonic()
        if pause > 0:
            _sleep(pause)
        self.log.append('Replaying the create, %d seconds after the first '
                        'refusal' % (time.monotonic() - self.first_refused))

    def _count_namespace_instances(self):
        # How many instances the build already held when it was refused,
        # which is what deciding whether a build should ask for all of its
        # instances at once needs to know. Counted once: it is a property
        # of the build, not of each replay. Diagnostic only, so a failure
        # is logged rather than failing the task. GET /instances lists
        # what the caller's namespace can see, which for the system
        # namespace is every namespace, so filter when the task names one.
        # The refused record is the only active instance of this name (see
        # _await_record_gone()), so leaving the name out leaves it out.
        try:
            instances = self.client.get_instances()
        except apiclient.APIException as e:
            self.log.append('Counting the instances in the namespace '
                            'failed: %s' % e)
            return
        count = 0
        for i in instances or []:
            if i.get('name') == self.name:
                continue
            if self.namespace and i.get('namespace') != self.namespace:
                continue
            count += 1
        self.namespace_instances = count
        self.log.append('The namespace held %d other instances at the first '
                        'capacity refusal' % count)

    def _exhaust(self, action, error, why):
        self.exhausted = True
        self.seconds = time.monotonic() - self.first_refused
        self.log.append('Giving up waiting for capacity: %s' % why)
        _fail_api_error(
            self.module, self.log, action,
            '%s (waited %d seconds for capacity over %d %s: %s)'
            % (error, math.ceil(self.seconds), self.attempts,
               'attempt' if self.attempts == 1 else 'attempts', why),
            capacity_waited=self.report())

    def failure(self):
        """The return values a task failing for another reason adds.

        An empty dict unless a create was refused, so that a failure with
        no refusal before it -- and every failure with capacity_wait at 0 --
        returns exactly what it did before the option existed. Once one
        was, what the wait did is returned with the failure, which is the
        run that most needs it. seconds runs to the start of the last
        attempt, or to now when the failure came inside the wait itself.
        Changes nothing, so it is safe to call more than once.
        """
        if not self.refusals:
            return {}
        return {'capacity_waited': self.report(failed=True)}

    def report(self, failed=False):
        if self.exhausted:
            outcome = 'exhausted'
        elif failed:
            outcome = 'failed'
        elif self.refusals:
            outcome = 'placed'
        else:
            outcome = 'not_refused'
        return {
            'seconds': math.ceil(self.seconds),
            'attempts': self.attempts,
            'outcome': outcome,
            'refusals': list(self.refusals),
            'namespace_instances': self.namespace_instances,
        }


def _stored_video(video):
    """An existing instance's videospec, in the shape the module sends.

    An instance created with sf-client's --videospec memory=65536 stores
    its memory as the string the command line parser produced, which is
    the same videospec as the integer the module sends.
    """
    if not isinstance(video, dict):
        return video
    video = dict(video)
    memory = video.get('memory')
    if isinstance(memory, str) and memory.isdigit():
        video['memory'] = int(memory)
    return video


def _check_instance(client, existing, params, log):
    # Faithful port of client-python's commandline/ansible.py _check_instance.
    # Builds the create_instance() args and kwargs and determines whether the
    # existing instance (if any) differs from the requested specification.
    dirty = False
    instance_args = []
    instance_kwargs = {}

    # Required parameters. Note the names differ between the ansible params and
    # the existing instance object for historical reasons.
    for param_name, existing_name in [('name', 'name'), ('cpu', 'cpus'),
                                      ('ram', 'memory')]:
        if params.get(param_name) is None:
            raise InstanceCreationException('You must specify %s' % param_name)

        if param_name == 'name':
            if existing.get('name') != params['name']:
                log.append('Instance dirty: name has changed from %s to %s'
                           % (existing.get('name'), params['name']))
                dirty = True
            instance_args.append(params['name'])
            continue

        value = int(params[param_name])
        instance_args.append(value)
        if existing.get(existing_name) != value:
            log.append('Instance dirty: %s has changed from %s to %s'
                       % (param_name, existing.get(existing_name), value))
            dirty = True

    # Networks. Networks in the REST API are represented by interfaces, so we
    # represent everything as a networkspec for the purposes of creation.
    requested_networks = []
    for n in params.get('networks') or []:
        requested_networks.append({
            'network_uuid': n,
            'model': 'virtio',
            'float': False
        })

    for n in params.get('networkspecs') or []:
        defn = {}
        for elem in n.split(','):
            s = elem.split('=')
            if len(s) != 2:
                raise InstanceCreationException(
                    'network specification should be key=value not %s' % elem)
            if s[0] == 'float':
                # The value arrives as a string; bool('False') is truthy, so
                # parse the common boolean spellings explicitly.
                s[1] = str(s[1]).strip().lower() in ('true', '1', 'yes')
            defn[s[0]] = s[1]
        requested_networks.append(defn)

    # Painful dirtiness comparison of the interfaces.
    existing_interfaces = []
    for interface in existing.get('interfaces', []):
        iface = client.get_interface(interface['uuid'])
        existing_interfaces.append({
            'network_uuid': iface['network_uuid'],
            'macaddress': iface['macaddr'],
            'address': iface['ipv4'],
            'model': iface['model'],
            # The API reports 'floating' as the allocated floating address (a
            # string) or None, but a networkspec requests float as a boolean.
            # Normalise to a bool so "has a floating IP" compares equal to a
            # requested float=True, rather than being perpetually dirty.
            'float': bool(iface['floating'])
        })
    if len(existing_interfaces) != len(requested_networks):
        log.append('Instance dirty: the number of interfaces changed')
        dirty = True
    else:
        for idx in range(len(existing_interfaces)):
            existing_iface = existing_interfaces[idx]
            requested_iface = requested_networks[idx]

            if existing_iface['network_uuid'] != requested_iface['network_uuid']:
                log.append('Instance dirty: interface %d changed network' % idx)
                dirty = True
                break

            for attr in ['macaddress', 'address', 'model', 'float']:
                if (attr in requested_iface and
                        existing_iface[attr] != requested_iface[attr]):
                    log.append('Instance dirty: interface %d changed %s' % (idx, attr))
                    dirty = True
                    break

    instance_args.append(requested_networks)

    # Disks. Convert everything to a disk spec because that's what's returned
    # by the REST API.
    requested_disks = []
    for d in params.get('disks') or []:
        base = None
        if '@' not in d:
            size = int(d)
        else:
            size, base = d.split('@')
            try:
                size = int(size)
            except ValueError:
                raise InstanceCreationException('disk size must be an integer')

        requested_disks.append({
            'base': base,
            'bus': None,
            'size': size,
            'type': 'disk'
        })

    for d in params.get('diskspecs') or []:
        defn = {
            'base': None,
            'bus': None,
            'size': None,
            'type': 'disk'
        }
        for elem in d.split(','):
            s = elem.split('=')
            if len(s) != 2:
                raise InstanceCreationException(
                    'disk specification should be key=value not %s' % elem)

            if s[0] == 'size':
                try:
                    s[1] = int(s[1])
                except ValueError:
                    raise InstanceCreationException('disk size must be an integer')

            defn[s[0]] = s[1]
        requested_disks.append(defn)

    # Clean up existing disk specifications. The server adds keys of its own
    # to the disk spec it stores: disk_base is its internal representation of
    # a cleaned up disk's base, and blob_uuid is the blob that base resolved
    # to. blob_uuid in particular only appears once the base has been
    # resolved, which makes its presence a race against the image fetch --
    # an unchanged request compares equal before resolution and dirty after,
    # so the module would tear down and recreate an otherwise identical
    # instance. Neither key is something the caller asked for, so neither
    # should make a request look dirty unless the caller did ask for it: we
    # only strip keys the corresponding requested disk does not mention.
    cleaned_existing_disks = []
    for idx, e in enumerate(existing.get('disk_spec', [])):
        cleaned = dict(e)
        requested = requested_disks[idx] if idx < len(requested_disks) else {}
        for key in SERVER_POPULATED_DISK_KEYS:
            if key not in requested:
                cleaned.pop(key, None)
        cleaned_existing_disks.append(cleaned)

    json_requested = json.dumps(requested_disks, sort_keys=True)
    json_existing = json.dumps(cleaned_existing_disks, sort_keys=True)
    if json_requested != json_existing:
        log.append('Instance dirty: disk specification has changed')
        log.append('    Requested: %s' % json_requested)
        log.append('    Existing: %s' % json_existing)
        dirty = True

    instance_args.append(requested_disks)

    # Single string values (passed positionally to create_instance). The
    # server does not distinguish "no value" from an explicit empty string
    # for these fields: both are stored the same way and reported back as
    # null. A parameter of "" compared against that null was therefore
    # perpetually dirty, and the instance was silently replaced on every
    # run (issue 4276). Normalise both sides so "no user data" has one
    # spelling whether the caller wrote "" or omitted the parameter.
    for key in ['ssh_key', 'user_data']:
        value = params.get(key) or None
        if value is not None:
            if (existing.get(key) or None) != value:
                log.append('Instance dirty: %s has changed' % key)
                dirty = True
        instance_args.append(value)

    # The videospec. The API declares video a dict, but this parameter
    # was once a string: the API refused it with a 400 at its default
    # validation mode, and a string could never equal the dict the API
    # reports, so a play which set it was perpetually dirty as well
    # (issue 4227). Ansible fills in each suboption's default, and those
    # are the server's own, so a requested videospec is complete and
    # compares key for key with a stored one.
    if params.get('video') is not None:
        requested_video = dict(params['video'])
        existing_video = _stored_video(existing.get('video'))
        if existing_video != requested_video:
            log.append('Instance dirty: video has changed')
            log.append('    Requested: %s'
                       % json.dumps(requested_video, sort_keys=True))
            log.append('    Existing: %s'
                       % json.dumps(existing_video, sort_keys=True))
            dirty = True
        instance_kwargs['video'] = requested_video

    # Optional single string keyword values.
    for key in ['placement', 'nvram_template', 'configdrive', 'namespace']:
        if params.get(key) is not None:
            if existing.get(key) != params[key]:
                log.append('Instance dirty: %s has changed' % key)
                dirty = True
            kwarg = 'force_placement' if key == 'placement' else key
            instance_kwargs[kwarg] = params[key]

    # Optional list-of-strings values. An unset parameter means "no
    # preference": we omit the kwarg entirely so the server applies its
    # default (for side_channels currently ['sf-agent', 'sf-agent2']), and
    # we skip the dirty comparison because we cannot know what the server's
    # default is from here -- comparing unset against the applied default
    # would be perpetually dirty, forcing a needless delete-and-recreate on
    # every "ensure present". That breaks idempotency and, for an instance
    # with a static address, fails the recreate with a 409 because the
    # address is still reserved by the instance being replaced.
    #
    # The previous behaviour normalised unset to [] and passed it through,
    # but the API treats an explicit empty list as "no side channels at
    # all", which silently disabled the in-guest agent on every instance
    # this module created without an explicit side_channels parameter. An
    # explicit value -- including an explicit empty list to disable the
    # agent -- is still compared and passed through.
    for key in ['side_channels']:
        if params.get(key) is None:
            continue
        values = params[key]
        if (existing.get(key) or []) != values:
            log.append('Instance dirty: %s has changed' % key)
            dirty = True
        instance_kwargs[key] = values

    # Optional boolean values. These parameters are named for the API
    # fields they compare against and the create_instance() kwargs they
    # become, so no name mapping is required. That was not always true:
    # this parameter was once spelled secureboot while the API reported
    # secure_boot, and the comparison read the parameter name, so an
    # explicit value -- even one matching the server exactly -- found
    # nothing to compare against, was perpetually dirty, and replaced the
    # instance on every run (issue 4061). Name new parameters for the API
    # field rather than adding a mapping here.
    for key in ['uefi', 'secure_boot']:
        if params.get(key) is not None:
            value = bool(params[key])
            if existing.get(key) != value:
                log.append('Instance dirty: %s has changed' % key)
                dirty = True
            instance_kwargs[key] = value

    # Metadata is a dict.
    metadata = {}
    for k, v in (params.get('metadata') or {}).items():
        metadata[k] = v
    if metadata:
        instance_kwargs['metadata'] = metadata

    if dirty:
        return True, instance_args, instance_kwargs

    return False, None, None


def _delete_and_wait(client, log, identifier, namespace):
    # Ask for the deletion, not for the wait. The client is built with
    # ASYNC_BLOCK, so a plain delete_instance() runs its own poll loop for
    # up to an hour inside a single call, and the 180 second bound below
    # was only ever a "do not start another attempt" check evaluated
    # between calls -- an instance which never reached 'deleted' held this
    # function for 3600 seconds while it advertised 180 (issue 3851).
    # async_request has been an argument since client v0.4.8, so unlike
    # the create timeout in run_module() no feature detection is needed; it
    # returns {} rather than the instance, which nothing here reads.
    #
    # The DELETE is issued once rather than per iteration: with a
    # non-blocking call, the old retrying shape would have enqueued one
    # deletion per second against a stuck instance.
    #
    # monotonic() rather than time(): this is a duration, and an NTP step
    # mid-loop would otherwise shorten or extend it arbitrarily.
    start_time = time.monotonic()
    log.append('Attempt deletion...')
    try:
        client.delete_instance(
            identifier, namespace=namespace, async_request=True)
        while time.monotonic() - start_time < 180:
            time.sleep(1)
            i = client.get_instance(identifier, namespace=namespace)
            if not i or i['state'] == 'deleted':
                return None
        return client.get_instance(identifier, namespace=namespace)
    except apiclient.ResourceNotFoundException:
        return None


def run_module():
    argument_spec = {
        'name': {'required': False, 'type': 'str'},
        'uuid': {'required': False, 'type': 'str'},
        'cpu': {'required': False, 'type': 'int'},
        'ram': {'required': False, 'type': 'int'},
        'disks': {'required': False, 'type': 'list', 'elements': 'str'},
        'diskspecs': {'required': False, 'type': 'list', 'elements': 'str'},
        'networks': {'required': False, 'type': 'list', 'elements': 'str'},
        'networkspecs': {'required': False, 'type': 'list', 'elements': 'str'},
        'ssh_key': {'required': False, 'type': 'str'},
        'user_data': {'required': False, 'type': 'str'},
        'placement': {'required': False, 'type': 'str'},
        'video': {
            'required': False,
            'type': 'dict',
            'options': {
                'model': {'type': 'str', 'default': 'cirrus'},
                'memory': {'type': 'int', 'default': 16384},
                'vdi': {
                    'type': 'str',
                    'default': 'spice',
                    'choices': [
                        'vnc', 'spice', 'spiceconcurrent', 'spicedebug'],
                },
            },
        },
        'nvram_template': {'required': False, 'type': 'str'},
        'configdrive': {'required': False, 'type': 'str'},
        'side_channels': {'required': False, 'type': 'list', 'elements': 'str'},
        'uefi': {'required': False, 'type': 'bool'},
        'secure_boot': {'required': False, 'type': 'bool'},
        'metadata': {'required': False, 'type': 'dict'},
        'await': {'required': False, 'type': 'bool', 'default': False},
        'await_timeout': {'required': False, 'type': 'int', 'default': 600},
        'capacity_wait': {'required': False, 'type': 'int', 'default': 0},
        'state': {
            'default': 'present',
            'choices': ['present', 'absent'],
            'type': 'str',
        },
    }
    # namespace names the namespace to operate in -- it is passed to
    # get_instance() and delete_instance() -- as well as the one to
    # authenticate as, so it is exempt from the all-or-nothing connection
    # rule and may be supplied alone. See check_connection().
    argument_spec.update(connection_argument_spec())

    module = AnsibleModule(
        argument_spec=argument_spec, supports_check_mode=True)

    # Before anything branches. Every other exit from run_module() -- an
    # argument check, a check mode return -- happens after this, so a play
    # that names a cluster half way cannot be told it would have succeeded.
    check_connection(module, identity_optional=True)

    log = []
    state = module.params['state']
    namespace = module.params.get('namespace')

    identifier = module.params.get('uuid') or module.params.get('name')
    log.append('Will use identifier %s' % identifier)
    if not identifier:
        module.fail_json(
            msg='You must specify one of name or uuid', meta=None, log=log)

    client = make_client(module, identity_optional=True)

    if state == 'present':
        try:
            i = client.get_instance(identifier, namespace=namespace)
        except apiclient.ResourceNotFoundException:
            i = {}
        except apiclient.APIException as e:
            _fail_api_error(module, log, 'Fetching the existing instance failed', e)

        try:
            needs_replacement, instance_args, instance_kwargs = \
                _check_instance(client, i, module.params, log)
        except InstanceCreationException as e:
            module.fail_json(msg=str(e), meta=None, log=log)
        except apiclient.APIException as e:
            _fail_api_error(
                module, log, 'Comparing against the existing instance failed', e)

        capacity = _CapacityWait(
            module, client, log, module.params.get('capacity_wait'),
            module.params.get('name'), namespace)

        if module.check_mode:
            module.exit_json(
                changed=needs_replacement, meta=(i or None), log=log,
                capacity_waited=capacity.report())

        # The budget starts here rather than at the create, because a
        # replacement deletes the old instance first and that time is
        # spent inside the task like any other. An instance which needs
        # no replacement spends nothing between here and the await, and
        # so gets the whole budget for it. monotonic() because this is a
        # duration: a wall clock step mid-create would otherwise inflate
        # the budget past await_timeout or collapse it to nothing.
        operation_started = time.monotonic()
        if needs_replacement:
            if i:
                try:
                    remaining = _delete_and_wait(
                        client, log, identifier, namespace)
                except apiclient.APIException as e:
                    _fail_api_error(
                        module, log, 'Instance deletion for update failed', e)
                if remaining and remaining.get('state') != 'deleted':
                    log.append('Repeated attempts at deletion failed')
                    module.fail_json(
                        msg='Deletion of instance for update failed.',
                        meta=None, log=log)

            # When we are going to await below, await_timeout is the
            # budget for the whole operation, so the create must not wait
            # as well. It used to: the client is built with ASYNC_BLOCK,
            # so create_instance polled for an hour, returned an instance
            # still in 'creating', and only then did the 600 second await
            # start on the same condition. The task took the sum -- about
            # 4200 seconds -- and reported the 600
            # (shakenfist/kerbside#355).
            #
            # timeout=0 is not a falsy "unset" to the client: it tests
            # "if timeout is None" and then bounds the POST's dependency
            # retries and the wait afterwards at now + 0, so the call
            # returns as soon as the POST is accepted
            # (shakenfist/client-python#369).
            #
            # The collection requires shakenfist-client unpinned, so the
            # control node can be running a client older than the one that
            # grew this argument -- it is not in any release up to and
            # including v0.8.3. Passing it blindly there is a TypeError
            # and every instance creation in the fleet stops working, so
            # ask before passing it. The elapsed-time deduction below
            # covers the older client: it cannot get the task down to
            # await_timeout, but it does stop the two budgets from being
            # added together.
            #
            # sf_snapshot solves its version of this at client
            # construction, by selecting ASYNC_CONTINUE, and that would
            # avoid this branch entirely. It is not done here because the
            # strategy is a property of the whole client, and the narrower
            # per-call arguments (this one, and async_request on the
            # delete in _delete_and_wait()) keep each call site's waiting
            # behaviour explicit at the call itself.
            if module.params.get('await') and _create_accepts_timeout(client):
                instance_kwargs['timeout'] = 0
                log.append('Client accepts a create timeout, so the create '
                           'will not wait')
            elif module.params.get('await'):
                log.append('Client predates the create timeout argument, so '
                           'the create will wait and what it spends is '
                           'deducted from the await budget instead')

        # Each pass is one create and its await. A pass ends in a replay
        # only when capacity_wait is set and the cluster refused the
        # create for capacity; _CapacityWait.refused() either returns when
        # the replay may go, or fails the task. Every replay goes through
        # the same instance_kwargs, so a create which was told not to wait
        # (timeout=0) is told so on every attempt.
        while True:
            if needs_replacement:
                capacity.start_attempt()
                try:
                    i = client.create_instance(
                        *instance_args, **instance_kwargs)
                except apiclient.APIException as e:
                    # Only the create's own refusal is replayed. The
                    # lookups and the await are outside this clause, so a
                    # failure of one of those is never mistaken for one.
                    if not capacity.replayable_api_refusal(e):
                        _fail_api_error(
                            module, log, 'Instance creation failed', e,
                            **capacity.failure())
                    body = _refusal_body(e)
                    capacity.refused(
                        'api', body.get('stage'), body.get('error'),
                        module.params.get('name'), namespace,
                        'Instance creation failed', e)
                    continue

            if not module.params.get('await'):
                log.append('Not awaiting instance')
                break

            # Whatever the delete and the create already spent comes out
            # of the budget, so await_timeout bounds the sequence rather
            # than each leg of it. Time spent waiting for capacity does
            # not: it has a budget of its own (see _CapacityWait), and is
            # always 0 when capacity_wait is.
            await_timeout = module.params.get('await_timeout')
            elapsed = time.monotonic() - operation_started - capacity.seconds
            await_budget = max(0, await_timeout - elapsed)
            log.append('Awaiting instance %s for %d seconds (%d already '
                       'spent)' % (i['uuid'], await_budget, elapsed))
            try:
                # A budget of zero is still worth handing over: the
                # client checks the instance once before consulting its
                # clock, so an instance which is already created is
                # reported as created rather than as a timeout.
                client.await_instance_create(i['uuid'], timeout=await_budget)
            except Exception as e:
                # Only an instance this task created can be replayed: one
                # which needed no replacement was not refused by anything
                # this task did.
                aborted = None
                if needs_replacement:
                    aborted = capacity.preflight_abort(i['uuid'])
                if aborted:
                    capacity.refused(
                        'preflight', None, aborted['error_message'],
                        i['uuid'], None, 'Waiting for instance failed',
                        aborted['error_message'])
                    continue

                if await_budget <= 0:
                    # The client's own message would name a zero second
                    # timeout, which is the symptom rather than the
                    # cause. Say where the budget actually went.
                    msg = ('The entire await_timeout budget of %d seconds '
                           'was consumed deleting and creating the '
                           'instance, leaving nothing to wait with: %s'
                           % (await_timeout, e))
                else:
                    msg = 'Waiting for instance failed: %s' % e
                log.append(msg)
                module.fail_json(msg=msg, meta=None, log=log,
                                 **capacity.failure())
            break

        try:
            meta = client.get_instance(i['uuid'])
        except apiclient.APIException as e:
            _fail_api_error(module, log, 'Fetching the created instance failed', e,
                            **capacity.failure())
        module.exit_json(changed=needs_replacement, meta=meta, log=log,
                         capacity_waited=capacity.report())

    # state == 'absent'
    try:
        client.get_instance(identifier, namespace=namespace)
    except apiclient.ResourceNotFoundException:
        log.append('Instance not found')
        module.exit_json(changed=False, meta=None, log=log)
    except apiclient.APIException as e:
        _fail_api_error(module, log, 'Fetching the existing instance failed', e)

    if module.check_mode:
        module.exit_json(changed=True, meta=None, log=log)

    try:
        remaining = _delete_and_wait(client, log, identifier, namespace)
    except apiclient.APIException as e:
        _fail_api_error(module, log, 'Instance deletion failed', e)
    if remaining is None:
        log.append('Deleted')
        module.exit_json(changed=True, meta=None, log=log)

    log.append('Repeated attempts at deletion failed')
    module.fail_json(msg='Deletion of instance failed', meta=remaining, log=log)


def main():
    run_module()


if __name__ == '__main__':
    main()
