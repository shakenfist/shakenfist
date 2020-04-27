#!/usr/bin/python

# A simple Shaken Fist ansible module, with thanks to
# https://blog.toast38coza.me/custom-ansible-module-hello-world/

import json

from ansible.module_utils.basic import AnsibleModule


DOCUMENTATION = '''
---
module: github_repo
short_description: Manage your repos on Github
'''

EXAMPLES = '''
- name: Create Shaken Fist network
  sf_network:
    netblock: "192.168.242.0/24"
    name: "mynet"
  register: result

- name: Delete that network
  sf_network:
    uuid: "1ebc5020-6cfd-4641-8f3b-175596a19de0"
    state: absent
  register: result
'''


def error(message):
    return True, False, {'error': message}


def present(module):
    if not 'netblock' in module.params:
        return error('You must specify a netblock when creating a network')

    if not 'name' in module.params:
        return error('You must specify a name when creating a network')

    rc, stdout, _ = module.run_command(
        'sf-client --json network create %(netblock)s %(name)s'
        % module.params, check_rc=True,
        use_unsafe_shell=True)

    try:
        j = json.loads(stdout)
    except:
        rc = -1
        j = 'Failed to parse JSON: %s' % stdout

    if rc != 0:
        return True, False, j

    return False, True, j


def absent(module):
    if not 'uuid' in module.params:
        return error('You must specify a uuid when deleting a network')

    rc, stdout, _ = module.run_command(
        'sf-client --json network delete %(uuid)s' % module.params,
        check_rc=True, use_unsafe_shell=True)

    try:
        j = json.loads(stdout)
    except:
        rc = -1
        j = stdout

    if rc != 0:
        return True, False, j

    return False, True, j


def main():

    fields = {
        "uuid": {"required": False, "type": "str"},
        "netblock": {"required": False, "type": "str"},
        "name": {"required": False, "type": "str"},
        "state": {
            "default": "present",
            "choices": ['present', 'absent'],
            "type": 'str'
        },
    }

    choice_map = {
        "present": present,
        "absent": absent,
    }

    module = AnsibleModule(argument_spec=fields)
    is_error, has_changed, result = choice_map.get(
        module.params['state'])(module)

    if not is_error:
        module.exit_json(changed=has_changed, meta=result)
    else:
        module.fail_json(msg="Error manipulating network", meta=result)


if __name__ == '__main__':
    main()
