# Copyright 2019 Michael Still and contributors
#
# A native Shaken Fist ansible module for snapshotting instances. This replaces
# the legacy module which shelled out to the `sf-client` CLI; it now imports
# the shakenfist_client SDK and calls the API directly.
from __future__ import annotations

from ansible.module_utils.basic import AnsibleModule

from shakenfist_client import apiclient


DOCUMENTATION = r'''
---
module: sf_snapshot
short_description: Create and delete Shaken Fist instance snapshots.
description:
  - Snapshot an instance's disks, optionally updating an artifact label, or
    delete a snapshot artifact.
  - Imports the C(shakenfist_client) SDK and talks to the Shaken Fist REST
    API directly; the control node only needs
    C(pip install shakenfist-client).
options:
  instance_uuid:
    description: The UUID of the instance to snapshot (state=present).
    required: false
    type: str
  uuid:
    description: The UUID of the snapshot artifact to delete (state=absent).
    required: false
    type: str
  all:
    description: Snapshot all disks rather than only the first.
    required: false
    default: false
    type: bool
  label:
    description: An artifact label name to point at the new snapshot.
    required: false
    type: str
  delete_after_label:
    description: Delete the snapshot artifact after applying the label.
    required: false
    default: false
    type: bool
  async:
    description:
      - When true, do not block waiting for the snapshot to be created
        (ignored when O(label) is set, which always blocks).
    required: false
    default: false
    type: bool
  state:
    description: Whether the snapshot should be present or absent.
    required: false
    default: present
    choices: [present, absent]
    type: str
  api_url:
    description:
      - Base URL of the Shaken Fist API. This, O(namespace) and O(key) are
        all supplied together or not at all, and supplying only some of them
        is an error rather than a silent fall back. When all three are
        omitted credentials are auto-discovered from the environment and
        C(sfrc) config exactly like the C(sf-client) CLI, which is the usual
        way to call this.
    required: false
    type: str
  namespace:
    description:
      - The namespace to authenticate as. This is an identity only; the
        instance snapshotted is O(instance_uuid). See O(api_url) for the
        all-or-nothing rule it shares with O(api_url) and O(key).
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
- name: Snapshot the primary disk of an instance
  shakenfist.shakenfist.sf_snapshot:
    instance_uuid: 9cd9ca86-0dd4-4ddd-aa28-822855ea4318
    state: present
  register: result

- name: Snapshot all disks without blocking
  shakenfist.shakenfist.sf_snapshot:
    instance_uuid: 9cd9ca86-0dd4-4ddd-aa28-822855ea4318
    all: true
    async: true
    state: present

- name: Snapshot and update the "ciimage" label
  shakenfist.shakenfist.sf_snapshot:
    instance_uuid: 9cd9ca86-0dd4-4ddd-aa28-822855ea4318
    label: ciimage
    state: present
'''

RETURN = r'''
changed:
  description: Whether the module created or deleted a snapshot.
  returned: always
  type: bool
failed:
  description: Whether the module failed.
  returned: always
  type: bool
meta:
  description: The snapshot result as returned by the API, when available.
  returned: success
  type: dict
'''


def _check_connection(module):
    """Refuse a partially specified connection, and say what was supplied.

    None of api_url, namespace and key is a connection parameter that can stand
    alone, so any of them arriving without the other two is a mistake rather
    than a request to discover: the values passed would be discarded and the
    module pointed at whatever cloud discovery found, with nothing said about
    it. namespace is held to that rule here because in this module it is an
    identity and nothing else -- the instance acted on is named by
    instance_uuid -- so namespace on its own says only "authenticate as this",
    which is exactly the instruction that would be discarded. sf_instance and
    sf_network exempt namespace because there it also names the object to
    operate on, and their deployment playbooks pass it alone.

    run_module() calls this immediately after AnsibleModule is built, which is
    what makes the rule hold on every path -- including the check mode paths
    that return before a client is ever needed. _make_client() calls it again
    for callers reaching it directly, the tests today and anything importing
    the module tomorrow.

    The rule is deliberately not declared on the argument spec. Ansible's
    required_together and required_by count key presence and never look at the
    value, so an empty string -- which is what "{{ sf_url | default('') }}"
    yields in a templated inventory, and which the rest of the collection
    treats as not supplied -- would be refused outright, while a full set with
    one member empty would be accepted and only caught later. Checking here
    keeps one definition of the rule, with one meaning of "supplied".
    """
    api_url = module.params.get('api_url')
    namespace = module.params.get('namespace')
    key = module.params.get('key')

    supplied = [n for n, v in (('api_url', api_url),
                               ('namespace', namespace),
                               ('key', key)) if v]
    if supplied and len(supplied) != 3:
        module.fail_json(
            msg=('api_url, namespace and key must be supplied together '
                 'or not at all. Got only %s, which would be '
                 'discarded in favour of discovered '
                 'configuration.' % ', '.join(supplied)),
            meta=None)
    return supplied


def _make_client(module):
    # Build a quiet, patient, blocking client. When all three connection
    # parameters are supplied we suppress configuration lookup and use them
    # verbatim; otherwise we auto-discover from the environment / sfrc config
    # exactly like the sf-client CLI.
    supplied = _check_connection(module)
    api_url = module.params.get('api_url')
    namespace = module.params.get('namespace')
    key = module.params.get('key')

    kwargs = {
        'verbose': False,
        'sync_request_timeout': 1800,
        'async_strategy': apiclient.ASYNC_BLOCK,
    }
    if module.params.get('async'):
        kwargs['async_strategy'] = apiclient.ASYNC_CONTINUE
    if len(supplied) == 3:
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
            msg='Could not configure the Shaken Fist client: %s' % e, meta=None)


def run_module():
    argument_spec = {
        'instance_uuid': {'required': False, 'type': 'str'},
        'uuid': {'required': False, 'type': 'str'},
        'all': {'required': False, 'type': 'bool', 'default': False},
        'label': {'required': False, 'type': 'str'},
        'delete_after_label': {'required': False, 'type': 'bool', 'default': False},
        'async': {'required': False, 'type': 'bool', 'default': False},
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

    # Before anything branches. Every other exit from run_module() -- an
    # argument check, a check mode return -- happens after this, so a play
    # that names a cluster half way cannot be told it would have succeeded.
    _check_connection(module)

    state = module.params['state']

    if state == 'present':
        if not module.params.get('instance_uuid'):
            module.fail_json(
                msg='You must specify an instance_uuid when creating a snapshot',
                meta=None)

        if module.check_mode:
            module.exit_json(changed=True, meta=None)

        client = _make_client(module)
        out = client.snapshot_instance(
            module.params['instance_uuid'],
            all=module.params.get('all', False),
            label_name=module.params.get('label'),
            delete_snapshot_after_label=module.params.get('delete_after_label', False))
        module.exit_json(changed=True, meta=out)

    # state == 'absent'
    if not module.params.get('uuid'):
        module.fail_json(
            msg='You must specify a uuid when deleting a snapshot', meta=None)

    if module.check_mode:
        module.exit_json(changed=True, meta=None)

    client = _make_client(module)
    client.delete_artifact(module.params['uuid'])
    module.exit_json(changed=True, meta=None)


def main():
    run_module()


if __name__ == '__main__':
    main()
