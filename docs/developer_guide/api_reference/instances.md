# Instances (/instances/)

Instances sit at the core of Shaken Fist's functionality, and are the component
which ties most of the other concepts in the API together. Therefore, they are
also the most complicated part of Shaken Fist to explain. This description is
broken into basic functionality -- showing information about instances -- and then
moves onto more advanced topics like creation, deletion, and other lifecycle events.

???+ note

    For a detailed reference on the state machine for instances, see the
    [developer documentation on object states](/developer_guide/state_machine/#instances).

## Fetching information about an instance

There are two main ways to fetch information about instances -- you can list all
instances visible to your authenticated namespace, or you can collect information
about a specific instance by providing its UUID or name.

???+ info

    Note that the amount of information visible in an instance response will change
    over the lifecycle of the instance -- for example when you first request the
    instance be created versus when the instance has had its disk specification
    calculated.

???+ tip "REST API calls"

    * [GET /instances](https://openapi.shakenfist.com/#/instances/get_instances): List all instances visible to your authenticated namespace.
    * [GET /instances/{instance_ref}](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref_): Get information about a specific instance.

??? example "Python API client: get all visible instances"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    instances = sf_client.get_instances()
    print(json.dumps(instances, indent=4, sort_keys=True))
    ```

??? example "Python API client: get information about a specific instance"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    i = sf_client.get_instance('317e9b70-8e26-46af-a1c4-76931c0da5a9')
    print(json.dumps(i, indent=4, sort_keys=True))
    ```

## Instance creation

Instance creation is by far the most complicated call in Shaken Fist in terms
of the arguments that it takes. The code in the Python command line client is
helpful if you need a fully worked example of every possible permutation. The
OpenAPI documentation at https://openapi.shakenfist.com/#/instances/post_instances
provides comprehensive and up to date documentation on all the arguments to
the creation call.

The instance creation API call also takes three data structures: the `diskspec`;
the `networkspec`; and the `videospec`. These structures are not well documented
in the OpenAPI interface, so are documented here instead.

### diskspec

A `diskspec` consists of the following fields as a JSON dictionary:

* size (integer): the size of the disk in gigabytes.
* base (string): the base image for the disk. This can be a variety of URL-like strings,
  as documented on [the artifacts page in the user guide](/user_guide/artifacts/).
  For a blank disk, omit this value.
* bus (enum): the hardware bus the disk device should be attached to on the instance.
  In general you shouldn't care about this and can omit this value. However, in
  some cases, such as unmodified Microsoft Windows images it is required. The options
  available here are: sata; scsi; usb; virtio (the default); and nvme. While ide
  was previously supported, that support was removed in v0.7 due to extremely
  poor performance.
* type (enum): the type of device. The default is "disk", but in some cases you might
  want "cdrom".

A full example of a `diskspec` is therefore:

```python
{
    'size': 20,
    'base': 'debian:11',
    'bus': None,
    'type': None
}
```

### networkspec

Similarly, a `networkspec` consists of the following fields in a JSON dictionary:

* network_uuid (uuid): the UUID of the network the interface should exist on.
* macaddress (string): the MAC address of the interface. Omit this value to be allocated
  a MAC address automatically.
* address (string): the IPv4 address to assign to the interface. Omit this value to be
  allocated a random address.
* model (enum): the model of the network interface card. In general you should not have
  to set this, although it can matter in some cases, such as unmodified Microsoft
  Windows images. The options include: i82551; i82557b; i82559er; ne2k_pci; pcnet;
  rtl8139; e1000; and virtio (the default).
* float (boolean): whether to associate a floating IP with this interface to enable external
  accessibility to the instance. Note that you can float and unfloat an interface
  after instance creation if desired.

### videospec

A `videospec` differs from a `diskspec` and a `networkspec` in that it is not
passed as a list. You only have one `videospec` per instance. Once again, a
`videospec` is a JSON dictionary with the following fields:

* model (enum): the model of the video card to attach to the instance. Possible
  options include: vga; cirrus (the default); and qxl.
* memory (integer): the amount of video RAM the video card should have, in
  kibibytes (blocks of 1024 bytes).
* vdi (string): the VDI protocol to use. Options are "vnc", "spice" (the default), or
  "spiceconcurrent". spice and spiceconcurrent are the same except that spiceconcurrent
  allows limited multi-user sessions, with subsequent sessions not experiencing
  full VDI functionality.

???+ tip "REST API calls"

    * [POST /instances/](https://openapi.shakenfist.com/#/instances/post_instances): Create an instance.

??? example "Python API client: create and then delete a simple instance"

    ```python
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()
    i = sf_client.create_instance(
        'example', 1, 1024, None,
        [{
            'size': 20,
            'base': 'debian:11',
            'bus': None,
            'type': 'disk'
        }],
        None, None)

    time.sleep(30)

    i = sf_client.delete_instance(i['uuid'])
    ```

## Adding network interfaces after instance creation

As of Shaken Fist v0.8, it is also possible to add network interfaces to an
existing instance, assuming that your guest operating system supports device hot
plugging (all modern Linux versions do).

???+ tip "REST API calls"

    * [POST /instances/{instance_ref}/interfaces](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__interfaces)

??? example "Python API client: hot plug a network interface"

    Note that this example assumes the instance is running an image with the
    Shaken Fist in guest agent installed.

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()

    # Create a network to hot plug to
    hotnet = sf_client.allocate_network('10.0.0.0/24', True, True, 'hotplug')

    ...

    # Hot plug the interface in
    netdesc = {
        'network_uuid': hotnet['uuid'],
        'address': '10.0.0.5',
        'macaddress': '02:00:00:ea:3a:28'
    }
    sf_client.add_instance_interface(inst['uuid'], netdesc)

## Other instance lifecycle operations

A variety of other lifecycle operations are available on instances, including
deletion, and power management.

The power management actions available are:

* soft reboot: gracefully request a reboot from the instance operating system
  via ACPI. This is not guaranteed to actually work, but if it does is much less
  likely to cause disk corruption on the instance.
* hard reboot: the equivalent of holding the reset switch down on a physical machine
  until it reboots without operating system involvement.
* power on: turn the instance on, as if the power switch was pressed. Since v0.8
  power on operations have the side effect of creating the config drive if one is
  specified by the instance configuration. That is, you can recreate the config
  drive by powering the instance off and then on again.
* power off: turn the instance immediately off, as if the power switch was held
  down on a physical machine.
* pause: suspend execution of the instance, but leave it hot in RAM ready to
  restart.
* unpause: unsuspend execution of the instance.

???+ tip "REST API calls"

    * [DELETE /instances/{instance_ref}](https://openapi.shakenfist.com/#/instances/delete_instances__instance_ref_): Delete an instance.
    * [DELETE /instances/](https://openapi.shakenfist.com/#/instances/delete_instances): Delete all instances in a namespace.
    * [POST ​/instances​/{instance_ref}​/rebootsoft](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__rebootsoft): Soft (ACPI) reboot the instance.
    * [POST ​/instances​/{instance_ref}​/reboothard](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__reboothard): Hard (reset switch) reboot the instance.
    * [POST /instances/{instance_ref}/poweron](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__poweron): Power the instance on.
    * [POST /instances/{instance_ref}/poweroff](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__poweroff): Power the instance off, as if holding the power switch down.
    * [POST /instances/{instance_ref}/pause](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__pause): Pause an instance.
    * [POST /instances/{instance_ref}/unpause](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__unpause): Unpause an instance.

??? example "Python API client: create and then delete a simple instance"

    ```python
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()
    i = sf_client.create_instance(
        'example', 1, 1024, None,
        [{
            'size': 20,
            'base': 'debian:11',
            'bus': None,
            'type': 'disk'
        }],
        None, None)

    time.sleep(30)

    i = sf_client.delete_instance(i['uuid'])
    ```

??? example "Python API client: attempt a soft reboot, and hard reboot if required"

    Note that this example assumes the instance is running an image with the
    Shaken Fist in guest agent installed.

    ```python
    import time
    from shakenfist_client import apiclient
    import sys

    sf_client = apiclient.Client()
    i = sf_client.create_instance(
        'example', 1, 1024, None,
        [{
            'size': 20,
            'base': 'debian:11',
            'bus': None,
            'type': 'disk'
        }],
        None, None, side_channels=['sf-agent'])

    # Wait for the instance to be created, or error out. Use instance events to
    # provide status updates during boot.
    while i['state'] not in ['created', 'error']:
        events = sf_client.get_instance_events(i['uuid'])
        print('Waiting for the instance to start: %s' % events[0]['message'])
        time.sleep(5)
        i = sf_client.get_instance(i['uuid'])

    # Check the instance is created correctly
    if i['state'] != 'created':
        print('Instance is not in a created state!')
        sys.exit(1)
    print('Instance is created')

    # Wait for the agent to report the reboot time
    while not i['agent_system_boot_time']:
        print('Waiting for agent to start: %s' % i['agent_state'])
        time.sleep(20)
        i = sf_client.get_instance(i['uuid'])

    initial_boot = i['agent_system_boot_time']
    print('Instance booted at %d' % initial_boot)

    # Now try to soft reboot the instance, wait up to 60 seconds for a reboot to
    # be detected
    sf_client.reboot_instance(i['uuid'], hard=False)
    print('Soft rebooting instance')
    time.sleep(60)
    i = sf_client.get_instance(i['uuid'])

    # Wait for the agent to report the reboot time again
    while not i['agent_system_boot_time']:
        print('Waiting for agent to start: %s' % i['agent_state'])
        time.sleep(20)
        i = sf_client.get_instance(i['uuid'])

    if i['agent_system_boot_time'] != initial_boot:
        print('Boot time changed from %d to %s'
            % (initial_boot, i['agent_system_boot_time']))

    else:
        # We failed to soft reboot, let's hard reboot instead
        sf_client.reboot_instance(i['uuid'], hard=True)
        print('Instance did not reboot, hard rebooting')
    ```

    Sample output:

    ```bash
    $ python3 example.py
    Waiting for the instance to start: schedule complete
    Instance is created
    Waiting for agent to start: not ready (no contact)
    Waiting for agent to start: not ready (no contact)
    Waiting for agent to start: not ready (no contact)
    Instance booted at 1684404969
    Soft rebooting instance
    Boot time changed from 1684404969 to 1684405036.0
    ```

??? example "Python API client: power off and then on an instance"

    ```python
    import time
    from shakenfist_client import apiclient
    import sys

    sf_client = apiclient.Client()
    i = sf_client.create_instance(
            'example', 1, 1024, None,
            [{
                'size': 20,
                'base': 'debian:11',
                'bus': None,
                'type': 'disk'
            }],
            None, None)

    # Wait for the instance to be created, or error out. Use instance events to
    # provide status updates during boot.
    while i['state'] not in ['created', 'error']:
        events = sf_client.get_instance_events(i['uuid'])
        print('Waiting for the instance to start: %s' % events[0]['message'])
        time.sleep(5)
        i = sf_client.get_instance(i['uuid'])

    # Check the instance is created correctly
    if i['state'] != 'created':
        print('Instance is not in a created state!')
        sys.exit(1)
    print('Instance is created')

    # Check the instance is created correctly
    if i['power_state'] != 'on':
        print('Instance is not in powered on state!')
        sys.exit(1)

    # Power the instance off
    sf_client.power_off_instance(i['uuid'])
    while i['power_state'] != 'off':
        print('Waiting for the instance to power off')
        time.sleep(5)
        i = sf_client.get_instance(i['uuid'])

    time.sleep(30)

    # Power the instance on
    sf_client.power_on_instance(i['uuid'])
    while i['power_state'] != 'on':
        print('Waiting for the instance to power on')
        time.sleep(5)
        i = sf_client.get_instance(i['uuid'])

    print('Done')
    ```

    ```
    Waiting for the instance to start: set attribute
    Instance is created
    Waiting for the instance to power off
    Waiting for the instance to power on
    Done
    ```


??? example "Python API client: pause and unpause an instance"

    ```python
    import time
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.pause_instance('foo')

    time.sleep(30)

    sf_client.unpause_instance('foo')
    ```

## Other instance information

We can also request other information for an instance. For example, we can list the
instance's network interfaces, or the events for the instance. See the
[user guide](/user_guide/events/) for a general introduction to the Shaken Fist
event system.

???+ tip "REST API calls"

    * [GET /instances/{instance_ref}/interfaces](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref__interfaces): Request information on the instance's network interfaces, if any.
    * [GET /instances/{instance_ref}/events](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref__events): Fetch events for a specific instance.

??? example "Python API client: list network interfaces for an instance"

    Note that the interface details for an instance wont be populated until the
    instance has started being created on the hypervisor node. Specifically, this
    can be some time later if an image needs to be fetched from the Internet and
    transcoded. Therefore in this example we wait for the instance to be created
    before displaying interface details.

    ```python
    import json
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()
    i = sf_client.create_instance(
            'example', 1, 1024, None,
            [{
                'size': 20,
                'base': 'debian:11',
                'bus': None,
                'type': 'disk'
            }],
            None, None)

    # Wait for the instance to be created, or error out. Use instance events to
    # provide status updates during boot.
    while i['state'] not in ['created', 'error']:
        events = sf_client.get_instance_events(i['uuid'])
        print('Waiting for the instance to start: %s' % events[0]['message'])
        time.sleep(5)
        i = sf_client.get_instance(i['uuid'])

    # Check the instance is created correctly
    if i['state'] != 'created':
        print('Instance is not in a created state!')
        sys.exit(1)
    print('Instance is created')

    # Fetch and display interface details
    ifaces = sf_client.get_instance_interfaces(i['uuid'])[0]
    print(json.dumps(ifaces, indent=4, sort_keys=True))
    ```

    ```bash
    $ python3 example.py
    Waiting for the instance to start: Fetching required blob ffdfce7f-728e-4b76-83c2-304e252f98b1, 30% complete
    Instance is created
    [
        {
            "floating": null,
            "instance_uuid": "d512e9f5-98d6-4c36-8520-33b6fc6de15f",
            "ipv4": "10.0.0.6",
            "macaddr": "02:00:00:73:18:66",
            "metadata": {},
            "model": "virtio",
            "network_uuid": "6aaaf243-0406-41a1-aa13-5d79a0b8672d",
            "order": 0,
            "state": "created",
            "uuid": "b1981e81-b37a-4176-ba37-b61bc7208012",
            "version": 3
        }
    ]
    ```

??? example "Python API client: list events for an instance"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    interfaces = sf_client.get_instance_events('c0d52a77-0f8a-4f19-bec7-0c05efb03cb4')
    print(json.dumps(interfaces, indent=4, sort_keys=True))
    ```

    Note that events are returned in reverse chronological order and are limited
    to the 100 most recent events.

    ```json
    [
        ...
        {
            "duration": null,
            "extra": {
                "cpu usage": {
                    "cpu time ns": 357485828000,
                    "system time ns": 66297716000,
                    "user time ns": 291188112000
                },
                "disk usage": {
                    "vda": {
                        "actual bytes on disk": 956301312,
                        "errors": -1,
                        "read bytes": 406776320,
                        "read requests": 12225,
                        "write bytes": 2105954304,
                        "write requests": 3657
                    },
                    "vdb": {
                        "actual bytes on disk": 102400,
                        "errors": -1,
                        "read bytes": 279552,
                        "read requests": 74,
                        "write bytes": 0,
                        "write requests": 0
                    }
                },
                "network usage": {
                    "02:00:00:1d:24:ae": {
                        "read bytes": 147084732,
                        "read drops": 0,
                        "read errors": 0,
                        "read packets": 16484,
                        "write bytes": 2166754,
                        "write drops": 0,
                        "write errors": 0,
                        "write packets": 13144
                    }
                }
            },
            "fqdn": "sf-2",
            "message": "usage",
            "timestamp": 1685229509.9592097,
            "type": "usage"
        },
        ...
    ]
    ```

## Out-of-band interactions with instances

Shaken Fist supports three types of instance consoles, which provide out-of-band
management of instances -- that is, the instance does not need to have functioning
networking for these consoles to work. You can read a general introduction of
Shaken Fist's console functionality in [the user guide](/user_guide/consoles/).
This page focuses on the API calls which are used to implement the console
functionality in the Shaken Fist client.

* Read only console: to download the most recent portion of the read only text
  serial console, or clear the console, use the
  `/instances/{instance_ref}/consoledata` API calls below.
* Interactive serial console: lookup the console port from the instance details
  fetch (as described above), and then connect to that port on the hypervisor
  node with a TCP client such as telnet.
* Interactive VDI console: lookup the VDI console port from the instance details
  fetch (as described above), and then connect to that port on the hypervisor
  with the correct client (currently one of VNC or SPICE). Alternatively, use
  the `/instances/{instance_ref}/vdiconsolehelper` API call described below to
  download a `virt-viewer` configuration file and then connect with `virt-viewer`.
  See the example below for more details.

???+ tip "REST API calls"

    * [GET /instances/{instance_ref}/consoledata](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref__consoledata): Fetch read only serial console data for an instance
    * [DELETE /instances/{instance_ref}/consoledata](https://openapi.shakenfist.com/#/instances/delete_instances__instance_ref__consoledata): Clear the read only serial console for an instance.
    * [GET /instances/{instance_ref}/vdiconsolehelper](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref__vdiconsolehelper): Generate and return a `virt-viewer` configuration file for connecting to the interactive VDI console for the instance (if configured).

??? example "Python API client: connect seamlessly to a VDI console using virt-viewer"

    ```python
    import os
    from shakenfist_client import apiclient
    import subprocess
    import tempfile
    import time

    sf_client = apiclient.Client()
    i = sf_client.create_instance(
            'example', 1, 1024, None,
            [{
                'size': 20,
                'base': 'debian:11',
                'bus': None,
                'type': 'disk'
            }],
            None, None)

    # Wait for the instance to be created, or error out. Use instance events to
    # provide status updates during boot.
    while i['state'] not in ['created', 'error']:
        events = sf_client.get_instance_events(i['uuid'])
        print('Waiting for the instance to start: %s' % events[0]['message'])
        time.sleep(5)
        i = sf_client.get_instance(i['uuid'])

    # Check the instance is created correctly
    if i['state'] != 'created':
        print('Instance is not in a created state!')
        sys.exit(1)
    print('Instance is created')

    # We don't use NamedTemporaryFile as a context manager as the .vv file
    # will also attempt to clean up the file.
    (temp_handle, temp_name) = tempfile.mkstemp()
    os.close(temp_handle)
    try:
        with open(temp_name, 'w') as f:
            f.write(sf_client.get_vdi_console_helper(i['uuid']))

        p = subprocess.run('remote-viewer %s' % temp_name, shell=True)
        print('Remote viewer process exited with %d return code' % p.returncode)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    ```

## Executing commands within an instance

Since v0.7, assuming a given instance has the Shaken Fist agent installed and
running, and was created with a `sf-agent` side channel, you can use the Shaken
Fist agent to move data into and out of the instance and execute commands without
the instance needing to have working networking configured. You can read more
about the exact requirements for agent connectivity in the
[API reference guide for agent operations](/developer_guide/api_reference/agentoperations/).

Agent Operations are not created directly -- they are a side effect of a call to
one of the API methods below, which create an Agent Operation so the caller can
track the state of their request. At the time of writing, you can perform the
following operations via the agent:

* copy the contents of a blob into an instance and change its file permissions.
  The python API client has a helper to upload the file into a blob before copying
  to the instance.
* execute a command and return its results (exit code, stdout, stderr).
* get the contents of a file within an instance into a blob.

???+ tip "REST API calls"

    * [POST ​/instances​/{instance_ref}​/agent​/execute](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__agent_execute): execute a command within an instance and return results.
    * [POST /instances/{instance_ref}/agent/put](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__agent_put): copy a blob into an instance at the specified location with the specified permissions.

??? example "Python API client: execute a command on an instance"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    agentop = sf_client.instance_execute('...uuid...', 'cat /etc/os-release')
    print(json.dumps(agentop, indent=4, sort_keys=True))
    ```

    Which would return something along the lines of:

    ```json
    {
        "commands": [
            {
                "block-for-result": true,
                "command": "execute",
                "commandline": "cat /etc/os-release"
            }
        ],
        "instance_uuid": "a771fb13-aaad-4cb6-a86b-7ee51e7bacc6",
        "metadata": {},
        "namespace": "vdi",
        "results": {
            "0": {
                "command-line": "cat /etc/os-release",
                "result": true,
                "return-code": 0,
                "stderr": "",
                "stdout": "PRETTY_NAME=\"Debian GNU/Linux 11 (bullseye)\"..."
            }
        },
        "state": "complete",
        "uuid": "93fb538c-84f5-4ff8-83ba-2be5f5f92954",
        "version": 1
    }
    ```

??? example "Python API client: put a file onto an instance via a blob"

    ```python
    import os
    from shakenfist_client import apiclient
    from shakenfist_client import util

    sf_client = apiclient.Client()
    if not sf_client.check_capability('blob-search-by-hash'):
        blob = None
    else:
        # We can cheat here -- if we already have a blob in the cluster with the
        # checksum of the file we're uploading, we can skip the upload entirely and
        # just reuse that blob.
        blob = util.checksum_with_progress(sf_client, 'README.md')

    if not blob:
        artifact = util.upload_artifact_with_progress(
            sf_client, 'upload-to-instance', 'README.md', None)
    else:
        print('Recycling existing blob')
        artifact = sf_client.blob_artifact(
            'upload-to-instnace', blob['uuid'], source_url=None)
    print('Created artifact %s' % artifact['uuid'])

    st = os.stat('README.md')
    sf_client.instance_put_blob(
            '...instance_ref...', artifact['blob_uuid'], '/tmp/README.md', st.st_mode)
    ```

    Which would return something along the lines of:

    ```bash
    $ python3 /tmp/demo.py
    Calculate checksum: 100%|██████████████████████████| 805/805 [00:00<00:00, 13.0MB/s]
    Searching for a pre-existing blob with this hash...
    Recycling existing blob
    Created artifact 3c0a6a83-e9df-46f0-b9a3-819eb16bea23
    ```

??? example "Python API client: get a file from an instance via a blob"

    ```python
    from shakenfist_client import apiclient
    import sys

    sf_client = apiclient.Client()
    op = sf_client.instance_get('...instance_ref...', '/tmp/README.md')
    if '0' not in op.get('results', {}):
        print('Results not available.')
        sys.exit(1)

    blob_uuid = op['results']['0'].get('content_blob')
    if not blob_uuid:
        print('Results did not include content')
        sys.exit(1)

    with open('/tmp/README.md', 'wb') as f:
        for chunk in sf_client.get_blob_data(blob_uuid):
            f.write(chunk)
    ```

### Fetching information about an Instance's Agent Operations

Additionally, you can list the agent operations for a given instance.

???+ tip "REST API calls"

    * [GET /instances/{instance_ref}/agentoperations](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref__agentoperations): List all agent operations for an instance.

??? example "Python API client: get all agent operations for a specific instance"

    The instance here had the following command line commands run before this
    sample script was run:

    ```bash
    $ sf-client instance upload ...uuid... README.md /tmp/README.md
    $ sf-client --simple instance execute ...uuid... "cat /tmp/README.md"
    ```

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    agentops = sf_client.get_instance_agentoperations('...uuid...', all=True)
    print(json.dumps(agentops, indent=4, sort_keys=True))
    ```

    Note the `all` argument here. By default you are only returned agent operations
    which are queued to execute. To see all agent operations including those
    which have completed execution, pass `all=True`. This script outputs:

    ```json
    [
        {
            "commands": [
                {
                    "blob_uuid": "09306f15-b1b3-4850-afb4-f4179559fa7f",
                    "command": "put-blob",
                    "path": "/tmp/README.md"
                },
                {
                    "command": "chmod",
                    "mode": 33188,
                    "path": "/tmp/README.md"
                }
            ],
            "instance_uuid": "a771fb13-aaad-4cb6-a86b-7ee51e7bacc6",
            "metadata": {},
            "namespace": "vdi",
            "results": {
                "0": {
                    "path": "/tmp/README.md"
                },
                "1": {
                    "path": "/tmp/README.md"
                }
            },
            "state": "complete",
            "uuid": "343049d7-da2a-46f2-bb5c-edb783ec1fb9",
            "version": 1
        },
        {
            "commands": [
                {
                    "block-for-result": true,
                    "command": "execute",
                    "commandline": "cat /tmp/README.md"
                }
            ],
            "instance_uuid": "a771fb13-aaad-4cb6-a86b-7ee51e7bacc6",
            "metadata": {},
            "namespace": "vdi",
            "results": {
                "0": {
                    "command-line": "cat /tmp/README.md",
                    "result": true,
                    "return-code": 0,
                    "stderr": "",
                    "stdout": "...content of file..."
                }
            },
            "state": "complete",
            "uuid": "5a00d6f3-19b6-42bc-b1df-ddc4e5a299e9",
            "version": 1
        }
    ]
    ```

## Console screen captures

Since v0.8, Shaken Fist has provided an API for collecting screen captures of the
instance console. This works for either serial consoles or graphical consoles, its
literally the same was whatever would have been displayed on the monitor if the
instance was a physical machine.

???+ tip "REST API calls"

    * [GET ​/instances​/{instance_ref}​/screenshot](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref__screenshot): Collect a screenshot for an instance.

This API call returns a blob UUID, you then need to collect the contents of the
blob using the [GET /blobs/{blob_uuid}/data](https://openapi.shakenfist.com/#/blobs/get_blobs__blob_uuid__data)
API call. The python Shaken Fist API client perfoms both operations for you and
returns an iterator of binary chunks ready for you to process or write to a file.

??? example "Python API client: collect a screenshot for an instance an instance"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    with open(destination, 'wb') as f:
        for chunk in sf_client.get_screenshot(instance_ref):
            f.write(chunk)
    ```

## Metadata

All objects exposed by the REST API may have metadata associated with them. This
metadata is for storing values that are of interest to the owner of the resources,
not Shaken Fist. Shaken Fist does not attempt to interpret these values at all,
with the exception of the [instance affinity metadata values](/user_guide/affinity/).
The metadata store is in the form of a key value store, and a general introduction
is available [in the user guide](/user_guide/metadata/).

???+ info

    Note that for affinity metadata to be processed by the scheduler, it must be
    present in the instance create API call, which is why that call takes a
    metadata argument. Adding affinity metadata after instance creation will not
    affect the placement of that instance, but would affect the placement of
    future instances.

???+ tip "REST API calls"

    * [GET ​/instances​/{instance_ref}​/metadata](https://openapi.shakenfist.com/#/instances/get_instances__instance_ref__metadata): Get metadata for an instance.
    * [POST /instances/{instance_ref}/metadata](https://openapi.shakenfist.com/#/instances/post_instances__instance_ref__metadata): Create a new metadata key for an instance.
    * [DELETE /instances/{instance_ref}/metadata/{key}](https://openapi.shakenfist.com/#/instances/delete_instances__instance_ref__metadata__key_): Delete a specific metadata key for an instance.
    * [PUT /instances/{instance_ref}/metadata/{key}](https://openapi.shakenfist.com/#/instances/put_instances__instance_ref__metadata__key_): Update an existing metadata key for an instance.

??? example "Python API client: set metadata on an instance"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_instance_metadata_item(instance_uuid, 'foo', 'bar')
    ```

??? example "Python API client: get metadata for an instance"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    md = sf_client.get_instance_metadata(instance_uuid)
    print(json.dumps(md, indent=4, sort_keys=True))
    ```

??? example "Python API client: delete metadata for an instance"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_instance_metadata_item(instance_uuid, 'foo')
    ```
