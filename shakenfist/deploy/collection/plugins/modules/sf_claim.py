# Copyright 2026 Michael Still and contributors
#
# A native Shaken Fist ansible module for managing a namespace's capacity
# claim. Shaped on sf_namespace.py, and like it it talks to the REST API
# through the shakenfist_client SDK rather than shelling out to sf-client.
#
# The server permits one *active* claim per namespace and refuses a second
# create outright, so "create it or re-date the one that is there" is the
# entire contract this module implements.
from __future__ import annotations

import time

from ansible.module_utils.basic import AnsibleModule

from shakenfist_client import apiclient


DOCUMENTATION = r'''
---
module: sf_claim
short_description: Manage a Shaken Fist namespace capacity claim.
requirements:
  - >-
    shakenfist-client on the ansible control node, built from a source tree
    which carries the namespace claim verbs. No release carries them yet, so
    today that means installing the client from git - pip install
    git+https://github.com/shakenfist/client-python@develop. The module says
    so, and names the verb it could not find, rather than raising an
    AttributeError at the first call.
description:
  - Idempotently ensure a namespace holds a capacity claim of a given size,
    or holds none at all.
  - A capacity claim reserves aggregate cluster capacity for a namespace, so
    that placements the namespace makes later are drawn from capacity the
    cluster has already promised it rather than won from whatever is free at
    the time. The intended user is a play which reconciles a standing fleet
    of instances and wants that fleet's footprint to survive a retire and
    rebuild of the instances themselves.
  - The server permits only one B(active) claim per namespace and refuses a
    second creation, so there is no separate "update" state. With
    O(state=present) the module creates the claim when the namespace holds
    no active one, and re-dates the existing claim (adjusting its limits if
    they differ) when it does.
  - >-
    Idempotence. The module reports C(changed) when it created a claim, when
    any of the three limits differed from the claim on the server, or when a
    re-date actually moved the claim's expiry. A re-date alone is therefore
    reported as a change, because it is one: the claim now holds cluster
    capacity for longer than it did, and an admission decision somewhere else
    on the cluster depends on that. The test is made against the server's own
    C(expires_at) before and after the write, so it never compares the
    control node's clock against the cluster's. A play which renews on a
    schedule and does not want the renewal in its change count should set
    C(changed_when) on the task, or set O(renew_within_seconds) so that the
    module re-dates only a claim which is actually near its expiry.
  - >-
    A lapsed claim. The server refuses to re-date a claim which is no longer
    active, and says to delete it and create a new one. This module does
    that, in that order - it creates the replacement first and only then
    removes the lapsed rows, so a capacity refusal leaves the namespace
    exactly as it found it. An expired claim holds no cluster capacity, so
    nothing is released by the removal and nothing is lost by the failure.
    Restoring coverage is the whole point of running the play, and failing
    instead would leave the fleet uncovered until somebody noticed.
  - >-
    Lapsed rows are reaped beside an active claim as well, so that a
    namespace which expired and was recreated out of band does not
    accumulate them. That reaping is deliberately B(not) reported as a
    change: an expired claim holds no cluster capacity and no admission
    decision anywhere depends on it, so removing the row alters nothing an
    operator can observe about the namespace's coverage. It is recorded in
    RV(log).
  - >-
    What a failure leaves behind. A refusal happens before anything is
    written, so it leaves the namespace exactly as it was found. The one
    exception is a failure while reaping lapsed rows, which happens after
    the claim itself was created or re-dated: the task fails, RV(meta)
    carries the claim that was written, and the lapsed rows are still
    there. They hold no capacity and the next run reaps them.
  - >-
    Capacity refusals. Creating or growing a claim is a guarded admission
    against the cluster, so the cluster may refuse it with HTTP 507 when
    there is not enough capacity to promise. That is an answer about the
    cluster and not a fault in this module: the task fails, RV(refusal)
    carries the status code and the server's per-dimension detail, and a
    play which can proceed without the claim should rescue it explicitly. A
    HTTP 503 means the cluster capacity accounting is not ready yet or the
    claim row was contended, and is worth retrying with C(until).
  - >-
    Removal is not symmetric with creation. O(state=absent) hands the
    reserved capacity straight back to the general pool, which takes effect
    immediately, while getting it back is a fresh admission decision which a
    busy cluster may refuse. Deleting a standing claim is therefore easy to
    do and may not be possible to undo.
options:
  namespace:
    description:
      - The namespace the claim covers.
      - This is not the namespace the module authenticates as. Claim
        management requires cluster administrator rights, so the caller is
        normally C(system) acting on somebody else's namespace.
    required: true
    type: str
  limit_cpus:
    description:
      - The number of vCPUs this namespace may hold at once. Required when
        O(state=present).
    required: false
    type: int
  limit_memory_mb:
    description:
      - The instance memory, in megabytes, this namespace may hold at once.
        Required when O(state=present).
    required: false
    type: int
  limit_disk_gb:
    description:
      - The instance disk, in gigabytes, this namespace may hold at once.
        Required when O(state=present).
    required: false
    type: int
  expires_in_seconds:
    description:
      - How long the claim should cover placements for, in seconds from now.
        Required when O(state=present), and must be positive.
      - >-
        A duration rather than a timestamp, and deliberately so: the expiry
        is computed from the cluster's clock, which is the only clock the
        expiry sweep ever compares against. Every run re-dates the claim to
        exactly this far in the future, which can shorten a claim as well as
        extend one.
    required: false
    type: int
  renew_within_seconds:
    description:
      - Re-date the claim only when it has less than this long left to run.
        Must be positive when it is set, and is ignored when
        O(state=absent).
      - >-
        When this is not set, which is the default, every O(state=present)
        run re-dates the claim. That is what a play which reconciles on a
        schedule wants: every run is another chance to re-date before the
        claim lapses, so a weekly play against a thirty day expiry has four
        of them.
      - >-
        Set it when the play would rather converge than renew. With a
        window set, a run which finds the limits already correct and the
        claim further from its expiry than the window writes nothing and
        reports no change, and check mode says the same. The remaining life
        is the server's own C(expires_at) against the control node's clock,
        which is the only comparison in this module spanning two clocks and
        is deliberately a coarse one: skew moves the moment the renewal
        happens and decides nothing else, and in particular never decides
        what is reported as changed. Choose a window comfortably larger
        than both the play's cadence and any plausible skew.
    required: false
    type: int
  state:
    description: Whether the namespace should hold a claim or not.
    required: false
    default: present
    choices: [present, absent]
    type: str
  api_url:
    description:
      - Base URL of the Shaken Fist API (for example
        C(http://sf-1:13000)). This, O(auth_namespace) and O(key) are all
        supplied together or not at all, and supplying only some of them is
        an error. When all three are omitted the module auto-discovers
        credentials from the environment and
        C(sfrc)/C(~/.shakenfist)/C(/etc/sf/shakenfist.json) exactly like the
        C(sf-client) CLI.
    required: false
    type: str
  auth_namespace:
    description:
      - The namespace to authenticate as, which is not O(namespace). Claim
        management is administrator only, so this is normally C(system). See
        O(api_url).
    required: false
    type: str
  key:
    description: The authentication key for O(auth_namespace). See O(api_url).
    required: false
    type: str
    no_log: true
author:
  - Michael Still and contributors
'''

EXAMPLES = r'''
- name: Hold a standing claim for a static runner fleet
  shakenfist.shakenfist.sf_claim:
    namespace: static-runners
    limit_cpus: 32
    limit_memory_mb: 65536
    limit_disk_gb: 1200
    expires_in_seconds: 2592000
    state: present
  register: result

- name: Renew the claim without counting the renewal as a change
  shakenfist.shakenfist.sf_claim:
    namespace: static-runners
    limit_cpus: 32
    limit_memory_mb: 65536
    limit_disk_gb: 1200
    expires_in_seconds: 2592000
  changed_when: false

- name: Renew the claim only once it is inside its last week
  shakenfist.shakenfist.sf_claim:
    namespace: static-runners
    limit_cpus: 32
    limit_memory_mb: 65536
    limit_disk_gb: 1200
    expires_in_seconds: 2592000
    renew_within_seconds: 604800

- name: Give the reserved capacity back to the cluster
  shakenfist.shakenfist.sf_claim:
    namespace: static-runners
    state: absent
'''

RETURN = r'''
changed:
  description: Whether the module created, resized or re-dated a claim.
  returned: always
  type: bool
failed:
  description: Whether the module failed.
  returned: always
  type: bool
meta:
  description:
    - The claim object as returned by the API, when available. It is also
      returned when the claim itself was written and the reaping of a
      lapsed row afterwards failed, so that the failure does not hide the
      half of the operation which succeeded.
  returned: success, and on a failure to reap a lapsed claim
  type: dict
refusal:
  description:
    - The status code and body of an API refusal, when the server refused
      the request. A C(status_code) of 507 means the cluster does not have
      the capacity to promise the claim; 503 means the request should be
      retried; 409 means the request conflicts with the claim which is
      already there.
  returned: on an API failure
  type: dict
log:
  description: A list of human readable progress messages, for debugging.
  returned: always
  type: list
  elements: str
'''


# The coverage state of a claim which is actually covering placements.
# Coverage is not the object's existence state and the two are deliberately
# separate on the wire, so both are checked below rather than either being
# read as the other.
COVERAGE_ACTIVE = 'active'

LIMIT_FIELDS = ('limit_cpus', 'limit_memory_mb', 'limit_disk_gb')

# The client SDK verbs this module calls. They are newer than any released
# shakenfist-client, so a control node which installed the client from PyPI
# has an apiclient.Client without them and every call here would be an
# AttributeError traceback. See _require_claim_verbs().
CLAIM_VERBS = ('get_namespace_claims', 'create_namespace_claim',
               'update_namespace_claim', 'delete_namespace_claim')


def _make_client(module):
    # Build a quiet, patient, blocking client. When all three connection
    # parameters are supplied we suppress configuration lookup and use them
    # verbatim; otherwise we let the client auto-discover from the environment
    # and sfrc config exactly like the sf-client CLI does.
    api_url = module.params.get('api_url')
    auth_namespace = module.params.get('auth_namespace')
    key = module.params.get('key')

    kwargs = {
        'verbose': False,
        'sync_request_timeout': 1800,
        'async_strategy': apiclient.ASYNC_BLOCK,
    }
    if api_url and auth_namespace and key:
        kwargs.update({
            'base_url': api_url,
            'namespace': auth_namespace,
            'key': key,
            'suppress_configuration_lookup': True,
        })
    try:
        return apiclient.Client(**kwargs)
    except apiclient.UnconfiguredException as e:
        module.fail_json(
            msg='Could not configure the Shaken Fist client: %s' % e, meta=None, log=[])


def _require_claim_verbs(client, module, log):
    """Fail clearly when the installed client SDK predates the claim verbs.

    The verbs landed on client-python's develop branch and no release
    carries them yet, so "your client is too old" is the most likely
    reason this module does not work on a control node which has done
    nothing wrong. An AttributeError traceback names the attribute and
    nothing an operator can act on, so name the fix instead. The check is
    against the client which was actually built, so it starts passing on
    its own once a release does carry the verbs.
    """
    missing = [verb for verb in CLAIM_VERBS
               if not callable(getattr(client, verb, None))]
    if not missing:
        return

    module.fail_json(
        msg='The installed shakenfist-client does not have the namespace '
            'claim verbs this module needs (missing: %s). They are not in a '
            'released client yet, so install the client from git on the '
            'ansible control node: pip install '
            'git+https://github.com/shakenfist/client-python@develop'
            % ', '.join(missing),
        meta=None, log=log)


def _live_claims(claims):
    """The claims which still exist, whatever their coverage state.

    The claims listing deliberately returns expired claims as well as active
    ones, because an expired claim still has a row and is the only thing
    which explains a namespace that stopped being charged for what it holds.
    A deleted claim is not interesting to either branch below, so it is
    dropped here rather than at each use.
    """
    return [c for c in claims or [] if c.get('state') != 'deleted']


def _partition_claims(claims):
    """Split a namespace's claims into the active one and the rest.

    Two creates racing for one namespace can both pass the server's
    existing-claim probe, so a namespace can legitimately hold more than one
    active claim. Admission draws down the lowest uuid, so that is the one
    re-dated here; the others are reported rather than touched, because
    deleting a claim which is holding capacity is not a thing to do as a
    side effect of a renewal.
    """
    active = sorted(
        [c for c in claims if c.get('coverage_state') == COVERAGE_ACTIVE],
        key=lambda c: str(c.get('uuid')))
    inactive = [c for c in claims if c.get('coverage_state') != COVERAGE_ACTIVE]

    if not active:
        return None, [], inactive
    return active[0], active[1:], inactive


def _limit_differences(claim, module):
    """The requested limits which do not match the claim on the server."""
    differences = {}
    for field in LIMIT_FIELDS:
        if claim.get(field) != module.params[field]:
            differences[field] = module.params[field]
    return differences


def _renewal_is_due(claim, module, log):
    """Whether this run should re-date the claim.

    Without renew_within_seconds every run re-dates, which is the default
    because it is what a scheduled reconcile wants: each run is another
    chance to re-date before the claim lapses.

    With a window set the claim is left alone while it has more than that
    long to run, so a repeat run and a check mode run both converge. The
    remaining life is the server's own expires_at against the control
    node's clock. That is the only comparison in this module which spans
    two clocks and it is deliberately a coarse one: skew moves the moment
    the renewal happens by the amount of the skew and decides nothing else.
    In particular it never decides what is reported as changed, which stays
    a comparison of two of the server's own timestamps.
    """
    window = module.params.get('renew_within_seconds')
    if not window:
        return True

    expires_at = claim.get('expires_at')
    if expires_at is None:
        # The server did not say when this claim expires, so there is
        # nothing for the window to be measured against. Re-date, which is
        # what this module does when there is no window at all.
        log.append('Claim %s has no expiry to measure the renewal window '
                   'against, re-dating' % claim.get('uuid'))
        return True

    remaining = expires_at - time.time()
    log.append(
        'Claim %s has about %d seconds left against a %d second renewal '
        'window' % (claim.get('uuid'), remaining, window))
    return remaining <= window


def _fail_api(module, log, message, e, meta=None):
    # An API refusal is an answer, not a crash, and the play needs enough of
    # it to tell a capacity refusal from a malformed request. sf_api.error()
    # carries the per-dimension detail of a capacity refusal in the response
    # body, so the body goes back to the play rather than only the summary.
    #
    # meta is the claim this module wrote before the failure, when there is
    # one. A reap which fails after a successful create or re-date must not
    # hide the claim it wrote from the play.
    module.fail_json(
        msg='%s: %s' % (message, e.message), meta=meta, log=log,
        refusal={'status_code': e.status_code, 'text': e.text})


def _create_claim(client, module, log):
    try:
        # By keyword: the SDK lives in another repository and these are
        # four mutually type-compatible integers, so a reordering there
        # would silently mis-assign limits rather than raising.
        return client.create_namespace_claim(
            module.params['namespace'],
            limit_cpus=module.params['limit_cpus'],
            limit_memory_mb=module.params['limit_memory_mb'],
            limit_disk_gb=module.params['limit_disk_gb'],
            expires_in_seconds=module.params['expires_in_seconds'])
    except apiclient.InsufficientResourcesException as e:
        _fail_api(
            module, log,
            'The cluster refused this claim, it does not have the capacity '
            'to promise it', e)
    except apiclient.APIException as e:
        _fail_api(module, log, 'Creating the namespace claim failed', e)


def _delete_claims(client, module, log, claims, description, meta=None):
    # meta is the claim this run wrote, when it wrote one, so that a reap
    # which fails still hands the play the claim which succeeded.
    for c in claims:
        log.append('Deleting %s claim %s' % (description, c.get('uuid')))
        try:
            client.delete_namespace_claim(
                module.params['namespace'], c['uuid'])
        except apiclient.ResourceNotFoundException:
            log.append('Claim %s was already gone' % c.get('uuid'))
        except apiclient.APIException as e:
            _fail_api(module, log, 'Deleting namespace claim %s failed'
                      % c.get('uuid'), e, meta=meta)


def _ensure_present(client, module, log):
    namespace = module.params['namespace']

    try:
        claims = _live_claims(client.get_namespace_claims(namespace))
    except apiclient.ResourceNotFoundException:
        module.fail_json(
            msg='Namespace %s does not exist, so it cannot hold a claim'
                % namespace, meta=None, log=log)
    except apiclient.APIException as e:
        _fail_api(module, log, 'Listing the claims of namespace %s failed'
                  % namespace, e)

    active, extra_active, inactive = _partition_claims(claims)
    for c in extra_active:
        log.append(
            'Namespace holds more than one active claim, leaving %s alone'
            % c.get('uuid'))

    if not active:
        if inactive:
            # The server refuses to re-date a claim which is not active and
            # says to replace it. Create first and reap afterwards: the
            # create is the half which can be refused, and an expired claim
            # holds no capacity, so this ordering means a refusal leaves the
            # namespace exactly as it was found.
            log.append('Namespace holds %d lapsed claim(s) and no active '
                       'claim, replacing' % len(inactive))
        else:
            log.append('Namespace holds no claim')

        if module.check_mode:
            module.exit_json(changed=True, meta=None, log=log)

        c = _create_claim(client, module, log)
        _delete_claims(client, module, log, inactive, 'lapsed', meta=c)
        module.exit_json(changed=True, meta=c, log=log)

    differences = _limit_differences(active, module)
    if differences:
        log.append('Claim %s limits differ: %s' % (active.get('uuid'), differences))

    if not differences and not _renewal_is_due(active, module, log):
        # There is nothing to write: the limits match and the claim is
        # further from its expiry than the renewal window the play asked
        # for. Lapsed rows are still worth reaping, and reaping is not a
        # change, so this run converges.
        log.append('Claim %s needs nothing' % active.get('uuid'))
        if not module.check_mode:
            _delete_claims(client, module, log, inactive, 'lapsed', meta=active)
        module.exit_json(changed=False, meta=active, log=log)

    if module.check_mode:
        # Check mode reports the re-date it cannot perform as a change,
        # because performing it would be one. There is no way to know
        # whether the server's clock has moved without asking it to move
        # the expiry, and guessing from the control node's clock is exactly
        # the comparison this module avoids making. A play which wants
        # check mode to converge sets renew_within_seconds, which is
        # answered above without writing anything.
        module.exit_json(changed=True, meta=active, log=log)

    # Only the limits which actually differ are named in the field mask, so
    # an unchanged dimension is not re-asserted and cannot race a resize
    # somebody else is making. The expiry is always named: re-dating is the
    # reason this task is in the play.
    try:
        updated = client.update_namespace_claim(
            namespace, active['uuid'],
            expires_in_seconds=module.params['expires_in_seconds'],
            **differences)
    except apiclient.InsufficientResourcesException as e:
        _fail_api(
            module, log,
            'The cluster refused to grow this claim, it does not have the '
            'capacity to promise it', e)
    except apiclient.ResourceStateConflictException as e:
        # The server answers 409 for a shrink below what the namespace is
        # already using, and for a claim which stopped being active between
        # the listing above and this write. Both are the caller's to fix
        # and neither is a fault in the cluster, so say which they are
        # rather than reporting a bare failure. The wording follows
        # CLAIM_REFUSAL_MESSAGE in the server's external_api/auth.py.
        _fail_api(
            module, log,
            'The cluster refused this change to claim %s: a claim cannot be '
            'shrunk below what it is already using, and a claim which is no '
            'longer active cannot be changed (delete it and create a new '
            'one)' % active['uuid'], e)
    except apiclient.APIException as e:
        _fail_api(module, log, 'Updating namespace claim %s failed'
                  % active['uuid'], e)

    # Both timestamps came from the cluster, so this asks whether the claim
    # moved and not whether two clocks agree.
    redated = updated.get('expires_at') != active.get('expires_at')
    if redated:
        log.append('Claim %s expiry moved to %s'
                   % (active.get('uuid'), updated.get('expires_at')))

    # Reap here as well as on the create path, so that lapsed rows beside an
    # active claim do not accumulate until somebody removes them by hand.
    # Not a change: an expired claim holds no cluster capacity, so removing
    # it alters nothing the namespace's coverage depends on.
    _delete_claims(client, module, log, inactive, 'lapsed', meta=updated)

    module.exit_json(changed=bool(differences) or redated, meta=updated, log=log)


def _ensure_absent(client, module, log):
    namespace = module.params['namespace']

    try:
        claims = _live_claims(client.get_namespace_claims(namespace))
    except apiclient.ResourceNotFoundException:
        log.append('Namespace did not exist')
        module.exit_json(changed=False, meta=None, log=log)
    except apiclient.APIException as e:
        _fail_api(module, log, 'Listing the claims of namespace %s failed'
                  % namespace, e)

    if not claims:
        log.append('Namespace holds no claim')
        module.exit_json(changed=False, meta=None, log=log)

    if module.check_mode:
        module.exit_json(changed=True, meta=None, log=log)

    _delete_claims(client, module, log, claims, 'existing')
    module.exit_json(changed=True, meta=None, log=log)


def run_module():
    argument_spec = {
        'namespace': {'required': True, 'type': 'str'},
        'limit_cpus': {'required': False, 'type': 'int'},
        'limit_memory_mb': {'required': False, 'type': 'int'},
        'limit_disk_gb': {'required': False, 'type': 'int'},
        'expires_in_seconds': {'required': False, 'type': 'int'},
        'renew_within_seconds': {'required': False, 'type': 'int'},
        'state': {
            'default': 'present',
            'choices': ['present', 'absent'],
            'type': 'str',
        },
        'api_url': {'required': False, 'type': 'str'},
        'auth_namespace': {'required': False, 'type': 'str'},
        'key': {'required': False, 'type': 'str', 'no_log': True},
    }

    module = AnsibleModule(
        argument_spec=argument_spec, supports_check_mode=True,
        required_if=[
            ('state', 'present',
             ['limit_cpus', 'limit_memory_mb', 'limit_disk_gb',
              'expires_in_seconds'])
        ],
        # All three connection parameters or none of them. A partial triple
        # is otherwise ignored in favour of whatever ambient credentials the
        # control node holds, which may be a different cluster entirely --
        # and this module's namespace parameter names the claim's target
        # rather than the identity, so a playbook written against the rest
        # of the collection lands exactly there.
        required_together=[['api_url', 'auth_namespace', 'key']])

    log = []

    state = module.params['state']
    if state == 'present' and module.params['expires_in_seconds'] < 1:
        # The server requires a positive duration. Say so here rather than
        # spending a round trip to be told, because a zero or negative
        # expiry in a play is a typo and not a decision.
        module.fail_json(
            msg='expires_in_seconds must be positive', meta=None, log=log)

    renew_within = module.params['renew_within_seconds']
    if state == 'present' and renew_within is not None and renew_within < 1:
        # A negative window would silently mean "never renew", which is the
        # opposite of what somebody setting it wants.
        module.fail_json(
            msg='renew_within_seconds must be positive when it is set',
            meta=None, log=log)

    client = _make_client(module)
    _require_claim_verbs(client, module, log)

    if state == 'present':
        _ensure_present(client, module, log)
    else:
        _ensure_absent(client, module, log)


def main():
    run_module()


if __name__ == '__main__':
    main()
