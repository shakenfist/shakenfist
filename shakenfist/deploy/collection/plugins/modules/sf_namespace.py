# Copyright 2019 Michael Still and contributors
#
# A native Shaken Fist ansible module for managing namespaces. This replaces
# the legacy bash shim that shelled out to `sf-client ansible namespace`; the
# arg -> API mapping is ported from client-python's
# shakenfist_client/commandline/ansible.py.
from __future__ import annotations

import time

from ansible.module_utils.basic import AnsibleModule

from shakenfist_client import apiclient


DOCUMENTATION = r'''
---
module: sf_namespace
short_description: Create and delete Shaken Fist namespaces.
description:
  - Idempotently ensure a Shaken Fist namespace is present or absent.
  - Imports the C(shakenfist_client) SDK and talks to the Shaken Fist REST
    API directly; the control node does not need the Shaken Fist server
    package, only C(pip install shakenfist-client).
options:
  name:
    description: The name of the namespace.
    required: true
    type: str
  state:
    description: Whether the namespace should be present or absent.
    required: false
    default: present
    choices: [present, absent]
    type: str
  api_url:
    description:
      - Base URL of the Shaken Fist API (for example
        C(http://sf-1:13000)). When omitted (together with O(namespace)
        and O(key)) the module auto-discovers credentials from the
        environment and C(sfrc)/C(~/.shakenfist)/C(/etc/sf/shakenfist.json)
        exactly like the C(sf-client) CLI.
    required: false
    type: str
  namespace:
    description: The namespace to authenticate as. See O(api_url).
    required: false
    type: str
  key:
    description: The authentication key for O(namespace). See O(api_url).
    required: false
    type: str
    no_log: true
author:
  - Michael Still and contributors
'''

EXAMPLES = r'''
- name: Create a namespace
  shakenfist.shakenfist.sf_namespace:
    name: myproject
    state: present
  register: result

- name: Delete a namespace
  shakenfist.shakenfist.sf_namespace:
    name: myproject
    state: absent
'''

RETURN = r'''
changed:
  description: Whether the module changed the namespace.
  returned: always
  type: bool
failed:
  description: Whether the module failed.
  returned: always
  type: bool
meta:
  description: The namespace object as returned by the API, when available.
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
    # verbatim; otherwise we let the client auto-discover from the environment
    # and sfrc config exactly like the sf-client CLI does.
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


def run_module():
    argument_spec = {
        'name': {'required': True, 'type': 'str'},
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
    name = module.params['name']
    client = _make_client(module)

    if state == 'present':
        try:
            n = client.get_namespace(name)
        except apiclient.ResourceNotFoundException:
            n = {}

        if not n:
            log.append('Namespace did not exist')
            if module.check_mode:
                module.exit_json(changed=True, meta=None, log=log)
            n = client.create_namespace(name)
            module.exit_json(changed=True, meta=n, log=log)

        # It already exists as we expect.
        module.exit_json(changed=False, meta=n, log=log)

    # state == 'absent'
    try:
        n = client.get_namespace(name)
        if n['state'] == 'deleted':
            log.append('Namespace is already deleted')
            module.exit_json(changed=False, meta=None, log=log)
    except apiclient.ResourceNotFoundException:
        log.append('Namespace did not exist')
        module.exit_json(changed=False, meta=None, log=log)

    if module.check_mode:
        module.exit_json(changed=True, meta=n, log=log)

    try:
        start_time = time.time()
        while time.time() - start_time < 180:
            try:
                log.append('Attempt deletion (state is %s)...' % n.get('state'))
                client.delete_namespace(name)
                time.sleep(1)
                n = client.get_namespace(name)
                if not n:
                    break
                if n['state'] == 'deleted':
                    break
            except apiclient.ResourceNotFoundException:
                n = {}
                break

        if n and n['state'] != 'deleted':
            module.fail_json(
                msg='Deletion of namespace failed', meta=n, log=log)

        module.exit_json(changed=True, meta=n or None, log=log)
    except apiclient.ResourceNotFoundException:
        module.exit_json(changed=True, meta=None, log=log)


def main():
    run_module()


if __name__ == '__main__':
    main()
