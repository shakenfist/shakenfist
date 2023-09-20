# Network Interfaces (/interfaces/)

Network Interfaces (known as just "interfaces" in the REST API), are the object
which ties an instance to the networks it is present on. There is a 1:1 mapping
between network interface objects and NICs inside an instance, such that an
instance with multiple interfaces on the same network would have two network
interface objects associated with it.

To lookup the network interfaces for an instance, use the
[GET /instances/{instance_ref}/interfaces](/developer_guide/api_reference/instances/#other-instance-information) API call as documented in the instance documentation.

???+ note

    For a detailed reference on the state machine for network interfaces, see the
    [developer documentation on object states](/developer_guide/state_machine/#network-interfaces).

## Fetching information about a network interface

As with other objects in the Shaken Fist REST API, you can fetch the details for
a network interface from the REST API.

???+ tip "REST API calls"

    * [GET /interfaces/{interface_uuid}](https://openapi.shakenfist.com/#/interfaces/get_interfaces__interface_uuid_): Get information about a specific network interface.

??? example "Python API client: fetch interface details"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    iface = sf_client.get_interface('b1981e81-b37a-4176-ba37-b61bc7208012')
    print(json.dumps(iface, indent=4, sort_keys=True))
    ```

??? example "Python API client: fetch details for a network interface associated with an instance"

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

## Floating network interfaces

Network interfaces by default have an address on the private IP range of the
network they belong to. This is sufficient to access resources outside the
Shaken Fist cluster, as long as the network has `provide_nat` enabled when
created. However, a network interface is not accessible from outside the
Shaken Fist cluster in this state.

To make a network interface accessible to clients outside the Shaken Fist
cluster, you "float" the interface. This assigns an address from the cluster's
`floating` network, which is then DNAT'ed to the private IP address of the
interface. As an instance it is not possible to see your floating address
from inside the instance, as the network address translation has already
occurred when the packets reach the instance.

???+ tip "REST API calls"

    * [POST /interfaces/{interface_uuid}/float](https://openapi.shakenfist.com/#/interfaces/post_interfaces__interface_uuid__float): Add a floating address to a network interface to make it externally accessible.
    * [POST /interfaces/{interface_uuid}/defloat](https://openapi.shakenfist.com/#/interfaces/post_interfaces__interface_uuid__defloat): Remove a floating address from a network interface, thus making the interface not externally accessible.

??? example "Python API client: float a network interface"

    A request to float an interface is an asynchronous operation, so we must make
    the request and then poll to learn our external address.

    ```python
    import json
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()
    sf_client.float_interface('b1981e81-b37a-4176-ba37-b61bc7208012')

    iface = sf_client.get_interface('b1981e81-b37a-4176-ba37-b61bc7208012')
    while not iface.get('floating'):
        print('Waiting...')
        time.sleep(5)
        iface = sf_client.get_interface('b1981e81-b37a-4176-ba37-b61bc7208012')

    print('The interface is externally accessible at %s' % iface['floating'])
    ```

    ```bash
    $ python3 example.py
    The interface is externally accessible at 192.168.10.5
    ```

??? example "Python API client: defloat a network interface"

    ```python
    import json
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()
    sf_client.defloat_interface('b1981e81-b37a-4176-ba37-b61bc7208012')
    ```

## Metadata

All objects exposed by the REST API may have metadata associated with them. This
metadata is for storing values that are of interest to the owner of the resources,
not Shaken Fist. Shaken Fist does not attempt to interpret these values at all,
with the exception of the [instance affinity metadata values](/user_guide/affinity/).
The metadata store is in the form of a key value store, and a general introduction
is available [in the user guide](/user_guide/metadata/).

???+ tip "REST API calls"

    * [GET ​/interfaces​/{interface_uuid}​/metadata](https://openapi.shakenfist.com/#/interfaces/get_interfaces__interface_uuid__metadata): Get metadata for an interface.
    * [POST /interfaces/{interface_uuid}/metadata](https://openapi.shakenfist.com/#/interfaces/post_interfaces__interface_uuid__metadata): Create a new metadata key for an interface.
    * [DELETE /interfaces/{interface_uuid}/metadata/{key}](https://openapi.shakenfist.com/#/interfaces/delete_interfaces__interface_uuid__metadata__key_): Delete a specific metadata key for an interface.
    * [PUT /interfaces/{interface_uuid}/metadata/{key}](https://openapi.shakenfist.com/#/interfaces/put_interfaces__interface_uuid__metadata__key_): Update an existing metadata key for an interface.

??? example "Python API client: set metadata on an interface"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_interface_metadata_item(img_uuid, 'foo', 'bar')
    ```

??? example "Python API client: get metadata for an interface"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    md = sf_client.get_interface_metadata(img_uuid)
    print(json.dumps(md, indent=4, sort_keys=True))
    ```

??? example "Python API client: delete metadata for an interface"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_interface_metadata_item(img_uuid, 'foo')
    ```