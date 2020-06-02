#!/usr/bin/python

# A simple Shaken Fist ansible module, with thanks to
# https://blog.toast38coza.me/custom-ansible-module-hello-world/

import json

from ansible.module_utils.basic import AnsibleModule


DOCUMENTATION = """
---
module: sf_instance
short_description: Create and delete Shaken Fist instances.
"""

EXAMPLES = """
- name: Create Shaken Fist instance
  sf_instance:
    name: 'myinstance'
    cpu: 1
    ram: 1
    disks:
      - 8@cirros
    networks:
      - 1ebc5020-6cfd-4641-8f3b-175596a19de0
    ssh_key: ssh-rsa AAAAB3NzaC1yc2EAA...f5uaZaTqQa18t8s= mikal@marvin
    user_data: |
      IyEvYmluL3NoCgplY2hvICJIZWxsbyBXb3JsZC4gIFRoZSB0aW1lIGlzIG5vdyAkKGRhdGUgLVIp
      ISIgPiAvaG9tZS9jaXJyb3Mvb3V0cHV0LnR4dApjaG93biBjaXJyb3MuY2lycm9zIC9ob21lL2Np
      cnJvcy9vdXRwdXQudHh0
    state: present
  register: result

- name: Create Shaken Fist instance with fancy disks
  sf_instance:
    name: 'myinstance'
    cpu: 1
    ram: 1
    diskspecs:
      - size=8,base=cirros,bus=ide,type=disk
      - size=16,type=disk
      - base=http://archive.ubuntu.com/ubuntu/dists/focal/main/installer-amd64/current/legacy-images/netboot/mini.iso,type=cdrom
    networks:
      - 1ebc5020-6cfd-4641-8f3b-175596a19de0
    ssh_key: ssh-rsa AAAAB3NzaC1yc2EAA...f5uaZaTqQa18t8s= mikal@marvin
    user_data: |
      IyEvYmluL3NoCgplY2hvICJIZWxsbyBXb3JsZC4gIFRoZSB0aW1lIGlzIG5vdyAkKGRhdGUgLVIp
      ISIgPiAvaG9tZS9jaXJyb3Mvb3V0cHV0LnR4dApjaG93biBjaXJyb3MuY2lycm9zIC9ob21lL2Np
      cnJvcy9vdXRwdXQudHh0
    state: present
  register: result

- name: Delete an intance
  sf_instance:
    uuid: 'afb68328-6ff0-498f-bdaa-27d3fcc97f31'
    state: absent
  register: result
"""


def error(message):
    return True, False, {'error': message}


def present(module):
    for required in ['name', 'cpu', 'ram']:
        if not module.params.get(required):
            return error('You must specify a %s when creating an instance' % required)

    params = {}
    for key in ['name', 'cpu', 'ram']:
        params[key] = module.params.get(key)

    if not module.params.get('disks'):
        params['disks'] = ''
    else:
        params['disks'] = '-d %s' % (' -d '.join(module.params['disks']))

    if not module.params.get('diskspecs'):
        params['diskspecs'] = ''
    else:
        params['diskspecs'] = '-D %s' % (
            ' -D '.join(module.params['diskspecs']))

    if not module.params.get('networks'):
        params['networks'] = ''
    else:
        params['networks'] = '-n %s' % (' -n '.join(module.params['networks']))

    if not module.params.get('networkspecs'):
        params['networkspecs'] = ''
    else:
        params['networkspecs'] = '-N %s' % (
            ' -N '.join(module.params['networkspecs']))

    if not module.params.get('placement'):
        params['placement'] = ''
    else:
        params['placement'] = '-p  %s' % module.params['placement']

    extra = ''
    if module.params.get('ssh_key'):
        extra += ' -I "%s"' % module.params['ssh_key']
    if module.params.get('user_data'):
        extra += ' -U "%s"' % module.params['user_data']
    params['extra'] = extra

    cmd = ('sf-client --json instance create %(name)s %(cpu)s %(ram)s '
           '%(disks)s %(diskspecs)s %(networks)s %(networkspecs)s %(placement)s '
           '%(extra)s' % params)
    rc, stdout, stderr = module.run_command(
        cmd, check_rc=False, use_unsafe_shell=True)

    try:
        j = json.loads(stdout)
    except:
        rc = -1
        j = ('Failed to parse JSON:\n'
             '[[command: %s]]\n'
             '[[stdout: %s]]\n'
             '[[stderr: %s]]'
             % (cmd, stdout, stderr))

    if rc != 0:
        return True, False, j

    return False, True, j


def absent(module):
    if not module.params.get('uuid'):
        return error('You must specify a uuid when deleting an instance')

    cmd = ('sf-client --json instance delete %(uuid)s' % module.params)
    rc, stdout, stderr = module.run_command(
        cmd, check_rc=False, use_unsafe_shell=True)

    try:
        j = json.loads(stdout)
    except:
        rc = -1
        j = ('Failed to parse JSON:\n'
             '[[command: %s]]\n'
             '[[stdout: %s]]\n'
             '[[stderr: %s]]'
             % (cmd, stdout, stderr))

    if rc != 0:
        return True, False, j

    return False, True, j


def main():

    fields = {
        'uuid': {'required': False, 'type': 'str'},
        'name': {'required': False, 'type': 'str'},
        'cpu': {'required': False, 'type': 'str'},
        'ram': {'required': False, 'type': 'str'},
        'disks': {'required': False, 'type': 'list', 'elements': 'str'},
        'diskspecs': {'required': False, 'type': 'list', 'elements': 'str'},
        'networks': {'required': False, 'type': 'list', 'elements': 'str'},
        'networkspecs': {'required': False, 'type': 'list', 'elements': 'str'},
        'ssh_key': {'required': False, 'type': 'str'},
        'user_data': {'required': False, 'type': 'str'},
        'placement': {'required': False, 'type': 'str'},
        'state': {
            'default': 'present',
            'choices': ['present', 'absent'],
            'type': 'str'
        },
    }

    choice_map = {
        'present': present,
        'absent': absent,
    }

    module = AnsibleModule(argument_spec=fields)
    is_error, has_changed, result = choice_map.get(
        module.params['state'])(module)

    if not is_error:
        module.exit_json(changed=has_changed, meta=result)
    else:
        module.fail_json(msg='Error manipulating instance',
                         params=module.params,
                         meta=result)


if __name__ == '__main__':
    main()
