# Ansible modules

Shaken Fist ships native Ansible modules for orchestration of cloud
resources as part of the `shakenfist.shakenfist` collection:
`sf_namespace`, `sf_network`, `sf_instance` and `sf_snapshot`. Earlier
releases shipped the modules as bash shims that redirected to the command
line client; those shims have been removed, and this documentation covers
the native collection modules.

## Installation

Install the collection and the Shaken Fist client SDK on your Ansible
control node:

```bash
ansible-galaxy collection install shakenfist.shakenfist
pip3 install shakenfist-client
```

The modules import the `shakenfist_client` python SDK and call the Shaken
Fist REST API directly — they do not shell out to `sf-client`, and the
control node never needs the Shaken Fist server package installed.

???+ note
    This example installs the Shaken Fist client in the system pip so that it
    is globally available to all Ansible users. The system pip is protected on
    modern Linux distributions, and you may need to include the
    `--break-system-packages` flag if your chosen Linux distribution does not
    package the Shaken Fist client, or install into a virtual environment and
    point `ansible_python_interpreter` at it.

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

## Referencing the modules

Reference the modules from your playbooks by their fully-qualified
collection name (FQCN), for example `shakenfist.shakenfist.sf_network`.

## Authentication

Every module accepts optional `api_url`, `namespace` and `key` connection
parameters. When all three are supplied they are used verbatim; when
omitted, the module auto-discovers credentials from the environment and
`sfrc` / `~/.shakenfist` / `/etc/sf/shakenfist.json` exactly like the
`sf-client` command line client does (see
[Authentication and Namespaces](../developer_guide/authentication.md)).

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
  shakenfist.shakenfist.sf_namespace:
    name: "{{ namespace_name }}"
    state: present
```

Delete a namespace:

```yaml
- name: Delete the namespace
  shakenfist.shakenfist.sf_namespace:
    name: "{{ namespace_name }}"
    state: absent
```

## Networks

### Parameters

| **Parameter** | **Comments** |
|---|---|
| dhcp<br/>*boolean* | Whether to provide DHCP services on the network. Defaults to `true`. Changing this value from what is present in the Shaken Fist cluster if the network already exists implies re-creation of the network. |
| dns<br/>*boolean* | Whether to provide DNS services on the network. Defaults to `false`. Changing this value from what is present in the Shaken Fist cluster if the network already exists implies re-creation of the network. |
| name<br/>*string* | The name of the network. Either `name` or `uuid` must be included in all requests. When both `name` and `uuid` are specified, `uuid` is used for existing resource lookups. If a network is identified by its `uuid`, then the network will be recreated if you specify a `name` which does not match the network in the Shaken Fist cluster. |
| nat<br/>*boolean* | Whether to provide NAT services on the network. Defaults to `true`. Changing this value from what is present in the Shaken Fist cluster if the network already exists implies re-creation of the network. |
| netblock<br/>*string* | The IP block for the network, for example `10.0.0.0/24`. Required when creating a network. Changing this value from what is present in the Shaken Fist cluster if the network already exists implies re-creation of the network. |
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
        'provide_dns': False,
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
  shakenfist.shakenfist.sf_network:
    netblock: "10.0.0.0/24"
    name: "ci"
  register: ci_network
```

Delete a network:

```yaml
- name: Delete the CI network
  shakenfist.shakenfist.sf_network:
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
| networks<br/>*list of strings* | A simpler format for specifying the networks an instance is attached to, following the same behaviour as the `-n` flag in the command line client: a list of network UUIDs. |
| networkspecs<br/>*list of strings* | A more verbose format for specifying the networks an instance is attached to that models the `-N` flag in the command line client, for example `network_uuid=...,address=10.0.0.5`. |
| ram<br/>*integer* | The amount of RAM the instance should have, in MB. |
| ssh_key<br/>*string* | An ssh public key to place into the instance's config drive. |
| user_data<br/>*string* | Base64-encoded user data to place into the instance's config drive. |
| placement<br/>*string* | Force placement of the instance onto a named node. |
| video<br/>*string* | The video model to use. |
| nvram_template<br/>*string* | The NVRAM template to use (for UEFI / secure boot). |
| configdrive<br/>*string* | The config drive style to use. |
| side_channels<br/>*list of strings* | A list of side channel names to expose to the instance. |
| uefi<br/>*boolean* | Whether to boot the instance with UEFI firmware. |
| secureboot<br/>*boolean* | Whether to enable UEFI secure boot for the instance (implies UEFI). |
| metadata<br/>*dictionary* | Metadata key-value pairs to set on the instance. |
| state<br/>*string* | The state of the resource. Valid states are `present` or `absent`, defaults to `present`. |
| uuid<br/>*string* | The UUID for the instance. Either `name` or `uuid` must be included in all requests with `state: absent`. If you specify a UUID and the instance does not exist in the Shaken Fist cluster, this argument will be ignored as UUIDs are randomly assigned on network creation. |
| await<br/>*boolean* | Whether to wait for the instance to be created. Only works for when state is `present`. Default is `false`. The wait is bounded by `await_timeout`, as is the creation which precedes it. |
| await_timeout<br/>*integer* | How many seconds an `await` may take, counted from the start of the operation rather than from the start of the wait. Where an instance is being replaced, the time spent deleting the old one and creating the new one is deducted from this number before the wait begins. It is a budget rather than a hard deadline: a deletion or creation already underway is not interrupted, and a `shakenfist-client` of v0.8.3 or earlier cannot be told not to wait while creating. Defaults to 600. |

### Examples

Create an instance attached to a network:

```yaml
- name: Create an instance
  shakenfist.shakenfist.sf_instance:
    name: "ci-worker"
    cpu: 2
    ram: 2048
    disks:
    - "20@debian:12"
    networks:
    - "{{ ci_network['meta']['uuid'] }}"
    await: true
  register: ci_worker
```

Delete an instance:

```yaml
- name: Delete the instance
  shakenfist.shakenfist.sf_instance:
    uuid: "{{ ci_worker['meta']['uuid'] }}"
    state: absent
```

## Snapshots

### Parameters

| **Parameter** | **Comments** |
|---|---|
| instance_uuid<br/>*string* | The UUID of the instance to snapshot. Required when `state` is `present`. |
| uuid<br/>*string* | The UUID of the snapshot artifact to delete. Required when `state` is `absent`. |
| all<br/>*boolean* | Whether to snapshot all of the instance's disks rather than only the first. Defaults to `false`. |
| label<br/>*string* | An artifact label name to point at the new snapshot. |
| delete_after_label<br/>*boolean* | Whether to delete the snapshot artifact after applying the label. Defaults to `false`. |
| async<br/>*boolean* | When `true`, do not block waiting for the snapshot to be created (ignored when `label` is set, which always blocks). Defaults to `false`. |
| state<br/>*string* | The state of the resource. Valid states are `present` or `absent`, defaults to `present`. |

### Examples

Snapshot an instance and update a label:

```yaml
- name: Snapshot and update the "ciimage" label
  shakenfist.shakenfist.sf_snapshot:
    instance_uuid: "{{ ci_worker['meta']['uuid'] }}"
    label: ciimage
    state: present
```