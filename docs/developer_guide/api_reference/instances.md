# Instances (/instances/)

Instances sit at the core of Shaken Fist's functionality, and are the component
which ties most of the other concepts in the API together. Therefore, they are
also the most complicated part of Shaken Fist to explain.

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

    * [GET /instances](https://sfcbr.shakenfist.com/api/apidocs/#/instances/get_instances): List all instances visible to your authenticated namespace.
    * [GET /instances/{instance_ref}](https://sfcbr.shakenfist.com/api/apidocs/#/instances/get_instances__instance_ref_): Get information about a specific instance.

??? example "Python API client: get all visible instances"

    ``` python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    instances = sf_client.get_instances()
    print(json.dumps(instances, indent=4, sort_keys=True))
    ```

??? example "Python API client: get information about a specific instance"

    ``` python
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
OpenAPI documentation at https://sfcbr.shakenfist.com/api/apidocs/#/instances/post_instances
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
  available here are: ide; sata; scsi; usb; virtio (the default); and nvme.
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

???+ tip "REST API calls"

    * [POST /instances/](https://sfcbr.shakenfist.com/api/apidocs/#/instances/post_instances): Create an instance.

??? example "Python API client: create and then delete a simple instance"

    ``` python
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

## Other instance lifecycle operations

A variety of other lifecycle operations are available on instances, including
deletion, power management (soft reboot (ACPI), hard reboot (reset switch),
power on, power off, pause, and unpause).

MORE DETAILS HERE

???+ tip "REST API calls"

    * [DELETE /instances/{instance_ref}](https://sfcbr.shakenfist.com/api/apidocs/#/instances/delete_instances__instance_ref_): Delete an instance.
    * [DELETE /instances/](XXX): Delete all instances in a namespace.


## Other instance information



??? example "Python API client: list network interfaces for an instance"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    interfaces = sf_client.get_instance_interfaces('c0d52a77-0f8a-4f19-bec7-0c05efb03cb4')
    print(json.dumps(interfaces, indent=4, sort_keys=True))
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

    ```
    $python3 example.py
    [
        ...
        {
            "duration": null,
            "extra": "{\"candidates\": [\"sf-1\", \"sf-2\", \"sf-3\", \"sf-4\"], \"request-id\": \"01afb355-fe85-4408-84f9-56c4f10a9780\"}",
            "fqdn": "sf-1",
            "message": "schedule are hypervisors",
            "timestamp": 1684299968.0741177,
            "type": "audit"
        },
        {
            "duration": null,
            "extra": "{\"candidates\": [\"sf-1\", \"sf-2\", \"sf-3\", \"sf-4\"], \"request-id\": \"01afb355-fe85-4408-84f9-56c4f10a9780\"}",
            "fqdn": "sf-1",
            "message": "schedule initial candidates",
            "timestamp": 1684299968.0697834,
            "type": "audit"
        },
        {
            "duration": null,
            "extra": "{\"attribute\": \"interfaces\", \"request-id\": \"01afb355-fe85-4408-84f9-56c4f10a9780\", \"value\": []}",
            "fqdn": "sf-1",
            "message": "set attribute",
            "timestamp": 1684299968.030679,
            "type": "mutate"
        },
        {
            "duration": null,
            "extra": "{\"attribute\": \"power_state\", \"power_state\": \"initial\", \"request-id\": \"01afb355-fe85-4408-84f9-56c4f10a9780\"}",
            "fqdn": "sf-1",
            "message": "set attribute",
            "timestamp": 1684299968.020376,
            "type": "mutate"
        },
        {
            "duration": null,
            "extra": "{\"attribute\": \"state\", \"request-id\": \"01afb355-fe85-4408-84f9-56c4f10a9780\", \"update_time\": 1684299967.9816113, \"value\": \"initial\"}",
            "fqdn": "sf-1",
            "message": "set attribute",
            "timestamp": 1684299967.9816155,
            "type": "mutate"
        },
        ...
    ]
    ```