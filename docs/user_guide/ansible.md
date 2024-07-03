# Ansible module

The Shaken Fist Ansible modules were re-written in v0.8. This documentation
covers that newer version.

## Installation

The Shaken Fist command line client also ships with an Ansible module for
orchestration of cloud resources. `getsf` installs this in the right place on the
primary node, but its likely that you'll need to hand install the module code
on other client machines.

```bash
$ sudo pip3 install shakenfist-client
$ sudo cp venv/share/shakenfist/ansible/* /usr/share/ansible/plugins/modules/
$ sudo chmod 0644 /usr/share/ansible/plugins/modules/sf_*
```

???+ note
    This example installs the Shaken Fist client in the system pip so that it
    is globally available to all Ansible users. The system pip is protected on
    modern Linux distributions, and you may need to include the
    `--break-system-packages` flag if your chosen Linux distribution does not
    package the Shaken Fist client.

    You'll know you need to do this if you see an error like this:

    ```bash
    $ sudo pip3 install shakenfist-client
    error: externally-managed-environment

    × This environment is externally managed
    ╰─> To install Python packages system-wide, try apt install
        python3-xyz, where xyz is the package you are trying to
        install.

        If you wish to install a non-Debian-packaged Python package,
        create a virtual environment using python3 -m venv path/to/venv.
        Then use path/to/venv/bin/python and path/to/venv/bin/pip. Make
        sure you have python3-full installed.

        If you wish to install a non-Debian packaged Python application,
        it may be easiest to use pipx install xyz, which will manage a
        virtual environment for you. Make sure you have pipx installed.

        See /usr/share/doc/python3.11/README.venv for more information.

    note: If you believe this is a mistake, please contact your Python
    installation or OS distribution provider. You can override this, at
    the risk of breaking your Python installation or OS, by passing
    --break-system-packages.
    hint: See PEP 668 for the detailed specification.
    ```

## Implementation

Ansible modules can be written in any language, although they are normally in
python. In order to centralize the code for our Ansible module, the module files
you install are simple `bash` redirects to the Shaken Fist command line client.
The client then does the right things to make the module work correctly.

???+ note
    Specifically, the command line `sf-client ansible ...` is what the bash
    redirect scripts use. The ansible command line module appears in help output
    for the command line client, but is not intended for direct user use.

## Namespaces

### Parameters

| **Parameter** | **Comments** |
|---|---|
| name<br/>*string* | The name of the namespace. This must always be specified. |
| state<br/>*string* | The state of the resource. Valid states are `present` or `absent`, defaults to `present`. |

### Return value

Unless an error is experienced the full REST API information for the namespace is
returned in a dictionary element called `meta`. An example returned dictionary is:

```python
{
    "changed": true,
    "failed": false,
    "log": [...],
    "meta": {
        "keys": [],
        "metadata": {},
        "name": "ci-003-peephie6Oo",
        "state": "created",
        "trust": {
            "full": [
                "system"
            ]
        },
        "version": 5
    },
    "msg": null
}
```

### Examples

Create a namespace:

```yaml
- name: Create a namespace
    sf_namespace:
    name: "{{ namespace_name }}"
    state: present
```

Delete a network:

```yaml
- name: Delete the namespace
    sf_namespace:
    uuid: "{{ namespace_name }}"
    state: absent
```

## Networks

### Parameters

| **Parameter** | **Comments** |
|---|---|
| dhcp<br/>*boolean* | Whether to provide DHCP services on the network. Defaults to `true`. Changing this value from what is present in the Shaken Fist cluster if the network already exists implies re-creation of the network. |
| name<br/>*string* | The name of the network. Either `name` or `uuid` must be included in all requests. When both `name` and `uuid` are specified, `uuid` is used for existing resource lookups. If a network is identified by its `uuid`, then the network will be recreated if you specify a `name` which does not match the network in the Shaken Fist cluster. |
| nat<br/>*boolean* | Whether to provide NAT services on the network. Defaults to `true`. Changing this value from what is present in the Shaken Fist cluster if the network already exists implies re-creation of the network. |
| state<br/>*string* | The state of the resource. Valid states are `present` or `absent`, defaults to `present`. |
| uuid<br/>*string* | The UUID for the network. Either `name` or `uuid` must be included in all requests with `state: absent`. If you specify a UUID and the network does not exist in the Shaken Fist cluster, this argument will be ignored as UUIDs are randomly assigned on network creation. |

### Return value

Unless an error is experienced the full REST API information for the network is
returned in a dictionary element called `meta`. An example returned dictionary is:

```python
{
    'changed': False,
    'failed': False,
    "log": [...],
    'msg': None,
    'meta': {
        'floating_gateway': '192.168.10.230',
        'metadata': {},
        'name': 'ci',
        'namespace': 'system',
        'netblock': '10.0.0.0/24',
        'provide_dhcp': True,
        'provide_nat': True,
        'state': 'created',
        'uuid': 'a8a52ac5-49b6-4444-80d0-3ab6573343ad',
        'version': 4,
        'vxid': 1436254
    }
}
```

### Examples

Create a network:

```yaml
- name: Create a network for CI infrastructure
    sf_network:
    netblock: "10.0.0.0/24"
    name: "ci"
  register: ci_network
```

Delete a network:

```yaml
- name: Delete the CI network
    sf_network:
    uuid: "{{ ci_network['meta']['uuid'] }}"
    state: absent
```

## Instances

### Parameters

| **Parameter** | **Comments** |
|---|---|
| cpu<br/>*integer* | The number of vCPUs the instance should have. |
| disks<br/>*list of strings* | A simpler format for specifying what disks an instance has that follows the same behaviour as the `-d` flag in the command line client. Specifications are of the form: `size@base` where base is optional and size is in GB. That is, `100@debian:11` is valid, but so is `100` for an empty 100gb disk. |
| diskspecs<br/>*list of strings* | A more verbose format for specifying what disks an instance has that models the `-D` flag in the command line client. Specifications are of the form: `size=20,base=debian:11,bus=sata;type=cdrom` where all elements are optional except for `size`. A more complete definition of this format is in the [developer reference documentation](/developer_guide/api_reference/instances/#diskspec). |
| name<br/>*string* | The name of the instance. Either `name` or `uuid` must be included in all requests. When both `name` and `uuid` are specified, `uuid` is used for existing resource lookups. If a instance is identified by its `uuid`, then the instance will be recreated if you specify a `name` which does not match the instance in the Shaken Fist cluster. |
| ram<br/>*integer* | The amount of RAM the instance should have, in MB. |
| state<br/>*string* | The state of the resource. Valid states are `present` or `absent`, defaults to `present`. |
| uuid<br/>*string* | The UUID for the instance. Either `name` or `uuid` must be included in all requests with `state: absent`. If you specify a UUID and the instance does not exist in the Shaken Fist cluster, this argument will be ignored as UUIDs are randomly assigned on network creation. |
| await<br/>*boolean* | Whether to wait for the instance to be created. Only works for when state is `present`. Default is `false`. |
| await_timeout<br/>*integer* | How many seconds to wait in an `await`. Defaults to 600. |