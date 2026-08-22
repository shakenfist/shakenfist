# Copyright 2019 Michael Still and contributors
#
# A native Shaken Fist ansible module for managing instances. This replaces
# the legacy bash shim that shelled out to `sf-client ansible instance`; the
# arg -> API mapping (including the dirtiness comparison) is ported from
# client-python's shakenfist_client/commandline/ansible.py.
from __future__ import annotations

import inspect
import json
import time

from ansible.module_utils.basic import AnsibleModule

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
    description: The video model to use.
    required: false
    type: str
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
  secureboot:
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
  state:
    description: Whether the instance should be present or absent.
    required: false
    default: present
    choices: [present, absent]
    type: str
  api_url:
    description:
      - Base URL of the Shaken Fist API. When omitted (with O(namespace)
        and O(key)) credentials are auto-discovered from the environment and
        C(sfrc) config exactly like the C(sf-client) CLI.
    required: false
    type: str
  namespace:
    description: The namespace the instance belongs to / authenticate as.
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
'''


# Keys the server adds to a stored disk specification which no caller
# supplied, and which therefore must not contribute to the dirtiness
# comparison. See the comment at the comparison itself in _check_instance().
SERVER_POPULATED_DISK_KEYS = ['blob_uuid', 'disk_base']


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


def _make_client(module):
    # Build a quiet, patient, blocking client. When all three connection
    # parameters are supplied we suppress configuration lookup and use them
    # verbatim; otherwise we auto-discover from the environment / sfrc config
    # exactly like the sf-client CLI.
    api_url = module.params.get('api_url')
    namespace = module.params.get('namespace')
    key = module.params.get('key')

    kwargs = {
        'verbose': False,
        'sync_request_timeout': 1800,
        'async_strategy': apiclient.ASYNC_BLOCK,
    }
    if api_url and namespace and key:
        kwargs.update({
            'base_url': api_url,
            'namespace': namespace,
            'key': key,
            'suppress_configuration_lookup': True,
        })
    try:
        return apiclient.Client(**kwargs)
    except apiclient.UnconfiguredException as e:
        module.fail_json(
            msg='Could not configure the Shaken Fist client: %s' % e, meta=None, log=[])


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

    # Single string values (passed positionally to create_instance).
    for key in ['ssh_key', 'user_data']:
        if params.get(key) is not None:
            if existing.get(key) != params[key]:
                log.append('Instance dirty: %s has changed' % key)
                dirty = True
            instance_args.append(params[key])
        else:
            instance_args.append(None)

    # Optional single string keyword values.
    for key in ['placement', 'video', 'nvram_template', 'configdrive', 'namespace']:
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

    # Optional boolean values.
    for key in ['uefi', 'secureboot']:
        if params.get(key) is not None:
            value = bool(params[key])
            if existing.get(key) != value:
                log.append('Instance dirty: %s has changed' % key)
                dirty = True
            kwarg = 'secure_boot' if key == 'secureboot' else key
            instance_kwargs[kwarg] = value

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
    # monotonic() rather than time(): this is a duration, and an NTP step
    # mid-loop would otherwise shorten or extend it arbitrarily.
    start_time = time.monotonic()
    while time.monotonic() - start_time < 180:
        try:
            log.append('Attempt deletion...')
            client.delete_instance(identifier, namespace=namespace)
            time.sleep(1)
            i = client.get_instance(identifier, namespace=namespace)
            if not i or i['state'] == 'deleted':
                return None
        except apiclient.ResourceNotFoundException:
            return None
    return client.get_instance(identifier, namespace=namespace)


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
        'video': {'required': False, 'type': 'str'},
        'nvram_template': {'required': False, 'type': 'str'},
        'configdrive': {'required': False, 'type': 'str'},
        'side_channels': {'required': False, 'type': 'list', 'elements': 'str'},
        'uefi': {'required': False, 'type': 'bool'},
        'secureboot': {'required': False, 'type': 'bool'},
        'metadata': {'required': False, 'type': 'dict'},
        'await': {'required': False, 'type': 'bool', 'default': False},
        'await_timeout': {'required': False, 'type': 'int', 'default': 600},
        'state': {
            'default': 'present',
            'choices': ['present', 'absent'],
            'type': 'str',
        },
        'api_url': {'required': False, 'type': 'str'},
        'namespace': {'required': False, 'type': 'str'},
        'key': {'required': False, 'type': 'str', 'no_log': True},
    }

    module = AnsibleModule(
        argument_spec=argument_spec, supports_check_mode=True)

    log = []
    state = module.params['state']
    namespace = module.params.get('namespace')

    identifier = module.params.get('uuid') or module.params.get('name')
    log.append('Will use identifier %s' % identifier)
    if not identifier:
        module.fail_json(
            msg='You must specify one of name or uuid', meta=None, log=log)

    client = _make_client(module)

    if state == 'present':
        try:
            i = client.get_instance(identifier, namespace=namespace)
        except apiclient.ResourceNotFoundException:
            i = {}

        try:
            needs_replacement, instance_args, instance_kwargs = \
                _check_instance(client, i, module.params, log)
        except InstanceCreationException as e:
            module.fail_json(msg=str(e), meta=None, log=log)

        if module.check_mode:
            module.exit_json(
                changed=needs_replacement, meta=(i or None), log=log)

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
                remaining = _delete_and_wait(client, log, identifier, namespace)
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
            # strategy is a property of the client and this same client
            # also runs _delete_and_wait() above, where delete_instance
            # blocks today. That loop polls for itself and would probably
            # tolerate the change, but "probably" is not a thing to find
            # out on the deletion path, so the narrower per-call argument
            # is used instead.
            if module.params.get('await') and _create_accepts_timeout(client):
                instance_kwargs['timeout'] = 0
                log.append('Client accepts a create timeout, so the create '
                           'will not wait')
            elif module.params.get('await'):
                log.append('Client predates the create timeout argument, so '
                           'the create will wait and what it spends is '
                           'deducted from the await budget instead')

            i = client.create_instance(*instance_args, **instance_kwargs)

        if not module.params.get('await'):
            log.append('Not awaiting instance')
        else:
            # Whatever the delete and the create already spent comes out
            # of the budget, so await_timeout bounds the sequence rather
            # than each leg of it.
            await_timeout = module.params.get('await_timeout')
            elapsed = time.monotonic() - operation_started
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
                module.fail_json(msg=msg, meta=None, log=log)

        module.exit_json(
            changed=needs_replacement, meta=client.get_instance(i['uuid']), log=log)

    # state == 'absent'
    try:
        client.get_instance(identifier, namespace=namespace)
    except apiclient.ResourceNotFoundException:
        log.append('Instance not found')
        module.exit_json(changed=False, meta=None, log=log)

    if module.check_mode:
        module.exit_json(changed=True, meta=None, log=log)

    remaining = _delete_and_wait(client, log, identifier, namespace)
    if remaining is None:
        log.append('Deleted')
        module.exit_json(changed=True, meta=None, log=log)

    log.append('Repeated attempts at deletion failed')
    module.fail_json(msg='Deletion of instance failed', meta=remaining, log=log)


def main():
    run_module()


if __name__ == '__main__':
    main()
