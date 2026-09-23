# Copyright 2019 Michael Still and contributors
#
# A native Shaken Fist ansible module for managing networks. This replaces the
# legacy bash shim that shelled out to `sf-client ansible network`; the
# arg -> API mapping is ported from client-python's
# shakenfist_client/commandline/ansible.py.
from __future__ import annotations

import time

from ansible.module_utils.basic import AnsibleModule

from shakenfist_client import apiclient


DOCUMENTATION = r'''
---
module: sf_network
short_description: Create and delete Shaken Fist networks.
description:
  - Idempotently ensure a Shaken Fist network is present or absent.
  - When a network already exists but its specification has changed, the
    network is deleted and recreated (Shaken Fist networks are immutable),
    so dependent instances must be absent first.
  - Imports the C(shakenfist_client) SDK and talks to the Shaken Fist REST
    API directly; the control node only needs
    C(pip install shakenfist-client).
options:
  name:
    description: The name of the network. One of O(name) or O(uuid) is required.
    required: false
    type: str
  uuid:
    description: The UUID of the network. One of O(name) or O(uuid) is required.
    required: false
    type: str
  netblock:
    description: The IPv4 netblock for the network (for example C(10.0.0.0/24)).
    required: false
    type: str
  nat:
    description: Whether the network should provide NAT egress.
    required: false
    default: true
    type: bool
  dhcp:
    description: Whether the network should provide DHCP.
    required: false
    default: true
    type: bool
  dns:
    description: Whether the network should provide DNS.
    required: false
    default: false
    type: bool
  state:
    description: Whether the network should be present or absent.
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
      - The namespace the network belongs to, which is also the namespace
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
- name: Create a network
  shakenfist.shakenfist.sf_network:
    name: mynet
    netblock: 10.0.0.0/24
    state: present
  register: result

- name: Delete a network by uuid
  shakenfist.shakenfist.sf_network:
    uuid: "{{ result['meta']['uuid'] }}"
    state: absent
'''

RETURN = r'''
changed:
  description: Whether the module changed the network.
  returned: always
  type: bool
failed:
  description: Whether the module failed.
  returned: always
  type: bool
meta:
  description: The network object as returned by the API, when available.
  returned: success
  type: dict
log:
  description: A list of human readable progress messages, for debugging.
  returned: always
  type: list
  elements: str
'''


def _make_client(module):
    # Build a quiet, patient, blocking client. When all three connection
    # parameters are supplied we suppress configuration lookup and use them
    # verbatim; otherwise we auto-discover from the environment / sfrc config
    # exactly like the sf-client CLI.
    # api_url and key are connection parameters and nothing else, so either of
    # them arriving without the full set is a mistake rather than a request to
    # discover: the values passed would be discarded and the module pointed at
    # whatever cloud discovery found, with nothing said about it. namespace is
    # deliberately not held to that rule in this module. It names the namespace
    # to operate in -- it is passed to allocate_network() and get_network() --
    # as well as the one to authenticate as, so it is legitimate on its own and
    # keeps meaning "work here, and find the credentials the usual way", which
    # is how the deployment playbooks have always called this. sf_claim,
    # sf_namespace and sf_snapshot hold all three to the rule because their
    # identity parameter is an identity and nothing else.
    # The argument spec declares the same rule as required_by, and that is
    # where a play meets it: Ansible checks it while AnsibleModule is being
    # built, so it holds on every path through run_module() -- including check
    # mode -- and the failure is reported in Ansible's own wording. This guard
    # is the backstop for callers reaching _make_client() directly, which is
    # also why it passes an empty log: nothing has accumulated one by then.
    api_url = module.params.get('api_url')
    namespace = module.params.get('namespace')
    key = module.params.get('key')

    if (api_url or key) and not (api_url and namespace and key):
        supplied = [n for n, v in (('api_url', api_url),
                                   ('namespace', namespace),
                                   ('key', key)) if v]
        module.fail_json(
            msg=('api_url and key are only used when api_url, namespace and '
                 'key are all supplied. Got only %s, so the connection '
                 'details passed here would be silently discarded in favour '
                 'of discovered configuration.' % ', '.join(supplied)),
            meta=None, log=[])

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


def _allocate(client, module, log, namespace):
    netblock = module.params.get('netblock')
    nat = module.params.get('nat')
    dhcp = module.params.get('dhcp')
    dns = module.params.get('dns')
    name = module.params.get('name')

    try:
        n = client.allocate_network(netblock, nat, dhcp, name,
                                    namespace=namespace, provide_dns=dns)
        return n
    except apiclient.IncapableException:
        if dns:
            module.fail_json(
                msg='This cloud does not support DNS services', meta=None, log=log)
        n = client.allocate_network(netblock, nat, dhcp, name, namespace=namespace)
        return n


def _delete_and_wait(client, module, log, identifier, namespace):
    # Repeatedly attempt deletion for up to 180 seconds, returning the last
    # observed network object (or None if it is gone).
    start_time = time.time()
    while time.time() - start_time < 180:
        try:
            log.append('Attempt deletion...')
            client.delete_network(identifier, namespace=namespace)
            time.sleep(1)
            n = client.get_network(identifier, namespace=namespace)
            if not n or n['state'] == 'deleted':
                log.append('Deleted')
                return None
        except apiclient.ResourceNotFoundException:
            log.append('Deleted')
            return None
    return client.get_network(identifier, namespace=namespace)


def run_module():
    argument_spec = {
        'name': {'required': False, 'type': 'str'},
        'uuid': {'required': False, 'type': 'str'},
        'netblock': {'required': False, 'type': 'str'},
        'nat': {'required': False, 'type': 'bool', 'default': True},
        'dhcp': {'required': False, 'type': 'bool', 'default': True},
        'dns': {'required': False, 'type': 'bool', 'default': False},
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
        argument_spec=argument_spec, supports_check_mode=True,
        # api_url and key are connection parameters and nothing else, so
        # each drags in the other two. namespace also names the namespace to
        # operate in, so it stands alone. Declaring the rule here as well as
        # in _make_client() is what makes it hold on every path: Ansible
        # checks it while AnsibleModule is built, before run_module()
        # branches on check mode.
        required_by={'api_url': ('namespace', 'key'),
                     'key': ('namespace', 'api_url')})

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
        nat = module.params.get('nat')
        dhcp = module.params.get('dhcp')
        dns = module.params.get('dns')

        try:
            n = client.get_network(identifier, namespace=namespace)
        except apiclient.ResourceNotFoundException:
            n = {}

        if not n:
            log.append('Network did not exist')
            if module.check_mode:
                module.exit_json(changed=True, meta=None, log=log)
            n = _allocate(client, module, log, namespace)
            module.exit_json(changed=True, meta=n, log=log)

        # Check if the network has a changed specification.
        dirty = False
        for key in ['name', 'netblock']:
            if module.params.get(key) is None:
                module.fail_json(
                    msg='You must specify %s when creating a network' % key,
                    meta=None, log=log)
            if n[key] != module.params.get(key):
                log.append('Network dirty, %s changed' % key)
                dirty = True

        # Optional specification elements (always have a default from the
        # argument spec, so compare unconditionally).
        if n['provide_dhcp'] != dhcp:
            log.append('Network dirty, dhcp changed')
            dirty = True
        if n['provide_nat'] != nat:
            log.append('Network dirty, nat changed')
            dirty = True
        if n['provide_dns'] != dns:
            log.append('Network dirty, DNS changed')
            dirty = True

        if not dirty:
            log.append('Call was noop')
            module.exit_json(changed=False, meta=n, log=log)

        if module.check_mode:
            module.exit_json(changed=True, meta=n, log=log)

        remaining = _delete_and_wait(client, module, log, n['uuid'], namespace)
        if remaining and remaining.get('state') != 'deleted':
            log.append('Repeated attempts at deletion failed')
            module.fail_json(
                msg='Deletion of network for update failed, does it have instances?',
                meta=None, log=log)

        log.append('Creating network')
        n = _allocate(client, module, log, namespace)
        module.exit_json(changed=True, meta=n, log=log)

    # state == 'absent'
    try:
        client.get_network(identifier, namespace=namespace)
    except apiclient.ResourceNotFoundException:
        log.append('Network not found')
        module.exit_json(changed=False, meta=None, log=log)

    if module.check_mode:
        module.exit_json(changed=True, meta=None, log=log)

    remaining = _delete_and_wait(client, module, log, identifier, namespace)
    if remaining is None:
        module.exit_json(changed=True, meta=None, log=log)

    log.append('Repeated attempts at deletion failed')
    module.fail_json(
        msg='Deletion of network failed, does it have instances?',
        meta=remaining, log=log)


def main():
    run_module()


if __name__ == '__main__':
    main()
