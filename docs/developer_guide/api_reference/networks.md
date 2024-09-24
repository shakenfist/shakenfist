# Networks (/networks/)

Networks exist so that instances can have connectivity to other instances, or
services outside the Shaken Fist cluster. Networks are implemented as a VXLAN
mesh between the hypervisor nodes which host the instances on that network, and
the network node. The network node exists on every VXLAN mesh, and provides
services to the network such as DHCP, ping, and egress NAT. If a floating IP
is assigned to a network interface or a routed IP to a network, then that is
also implemented on the network node.

???+ note

    It is not required that an instance be on any networks. However, this is
    by far the most common deployment pattern.

    For a detailed reference on the state machine for networks, see the
    [developer documentation on object states](/developer_guide/state_machine/#networks).

    For a detailed overview of how networks are constructed on the network node
    and hypervisors, see the [operator guide networking overview](/operator_guide/networking/overview).

## Network lifecycle

???+ tip "REST API calls"

    * [POST /networks](https://openapi.shakenfist.com/#/networks/post_networks): Create a network.
    * [DELETE /networks](https://openapi.shakenfist.com/#/networks/delete_networks): Delete all networks in a given namespace.
    * [DELETE /networks/networks/{network_ref}](https://openapi.shakenfist.com/#/networks/delete_networks__network_ref_): Delete a specific network.
    * [GET /networks](https://openapi.shakenfist.com/#/networks/get_networks): List the networks visible to the currently authenticated namespace.
    * [GET /networks/{network_ref}](https://openapi.shakenfist.com/#/networks/get_networks__network_ref_): Get details of a single network

??? example "Python API client: create a network"
    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    n = sf_client.allocate_network('10.0.0.0/24', True, True, 'example')
    print(json.dumps(n, indent=4, sort_keys=True))
    ```

    ```json
    {
        "floating_gateway": "192.168.10.16",
        "metadata": {},
        "name": "example",
        "namespace": "system",
        "netblock": "10.0.0.0/24",
        "provide_dhcp": true,
        "provide_nat": true,
        "provide_dns": false,
        "state": "created",
        "uuid": "1e9222c5-2d11-4ada-b258-ed1838bd774b",
        "version": 4,
        "vxid": 4882442
    }
    ```

??? example "Python API client: delete a network"
    ```python
    import json
    from shakenfist_client import apiclient
    import time

    sf_client = apiclient.Client()
    n = sf_client.allocate_network('10.0.0.0/24', True, True, 'example')

    n = sf_client.delete_network(n['uuid'])
    while n['state'] != 'deleted':
        print('Waiting...')
        time.sleep(1)
        n = sf_client.get_network(n['uuid'])

    print(json.dumps(n, indent=4, sort_keys=True))
    ```

    ```bash
    $ python3 example.py
    Waiting...
    {
        "floating_gateway": null,
        "metadata": {},
        "name": "example",
        "namespace": "system",
        "netblock": "10.0.0.0/24",
        "provide_dhcp": true,
        "provide_nat": true,
        "provide_dns": false,
        "state": "deleted",
        "uuid": "d56ae6e4-2592-43cd-b614-2dc7ca04970a",
        "version": 4,
        "vxid": 15408371
    }
    ```

??? example "Python API client: get a single network"
    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    n = sf_client.get_network('...uuid...')
    ```

??? example "Python API client: list networks"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    nets = sf_client.get_networks()
    print(json.dumps(nets, indent=4, sort_keys=True))
    ```

    ```json
    [
        {
            "name": "sfcbr-7YWeQo4BoqLjASDd",
            "namespace": "sfcbr-7YWeQo4BoqLjASDd",
            "netblock": "10.0.0.0/24",
            "provide_dhcp": true,
            "provide_nat": true,
            "provide_dns": false,
            "state": "created",
            "uuid": "759b742d-6140-475e-9553-ac120b56c1ef",
            "vxlan_id": 0
        }
    ]
    ```

## Other network information

We can also request other information for a network. For example, we can list the
networks's network interfaces, or the events for the network. See the
[user guide](/user_guide/events/) for a general introduction to the Shaken Fist
event system.

???+ tip "REST API calls"

    * [GET /networks/{network_ref}/events](https://openapi.shakenfist.com/#/networks/get_networks__network_ref__events): Fetch events for a network.
    * [GET /networks/{network_ref}/interfaces](https://openapi.shakenfist.com/#/networks/get_networks__network_ref__interfaces): Get the network interfaces present on a network.
    * [GET /networks/{network_ref}/ping/{address}](https://openapi.shakenfist.com/#/networks/get_networks__network_ref__ping__address_): Ping an address on a network from the network node.

??? example "Python API client: list events for a network"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    events = sf_client.get_network_events('e0c2ee96-2b61-4d58-abd4-2cdef522b7a6')
    print(json.dumps(events, indent=4, sort_keys=True))
    ```

    Note that events are returned in reverse chronological order and are limited
    to the 100 most recent events.

    ```json
    [
        ...
        {
            "duration": null,
            "extra": {
                "rx": {
                    "bytes": 2146364,
                    "dropped": 0,
                    "errors": 0,
                    "multicast": 0,
                    "over_errors": 0,
                    "packets": 13127
                },
                "tx": {
                    "bytes": 152367092,
                    "carrier_errors": 0,
                    "collisions": 0,
                    "dropped": 0,
                    "errors": 0,
                    "packets": 96644
                }
            },
            "fqdn": "sf-1",
            "message": "usage",
            "timestamp": 1685229103.9690208,
            "type": "usage"
        },
        ...
    ]
    ```

??? example "Python API client: list interfaces on a network"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    events = sf_client.get_network_interfaces('91b88200-ab4c-4ac4-9709-459504d1da0a')
    print(json.dumps(events, indent=4, sort_keys=True))
    ```

    ```json
    [
        {
            "floating": "192.168.10.84",
            "instance_uuid": "fffaa23b-c38b-484b-b58e-22eedc6ba94f",
            "ipv4": "10.0.0.20",
            "macaddr": "02:00:00:19:e4:b4",
            "metadata": {},
            "model": "virtio",
            "network_uuid": "91b88200-ab4c-4ac4-9709-459504d1da0a",
            "order": 0,
            "state": "created",
            "uuid": "24e636b4-b60c-4fcc-89d3-e717667a8c83",
            "version": 3
        },
        {
            "floating": null,
            "instance_uuid": "1762820a-1e44-41b3-9174-44412481d873",
            "ipv4": "10.0.0.57",
            "macaddr": "02:00:00:4b:dc:5f",
            "metadata": {},
            "model": "virtio",
            "network_uuid": "91b88200-ab4c-4ac4-9709-459504d1da0a",
            "order": 0,
            "state": "created",
            "uuid": "0c790a6e-a4de-4518-84e7-11d1421cd4df",
            "version": 3
        }
    ]
    ```

??? example "Python API client: ping an address on a network."

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    events = sf_client.ping('55ea5f3b-d671-4d7f-9b8c-1e8f217a74f4', '10.0.0.187')
    print(json.dumps(events, indent=4, sort_keys=True))
    ```

    ```json
    {
        "stderr": [
            ""
        ],
        "stdout": [
            "PING 10.0.0.187 (10.0.0.187) 56(84) bytes of data.",
            "64 bytes from 10.0.0.187: icmp_seq=1 ttl=64 time=0.393 ms",
            "64 bytes from 10.0.0.187: icmp_seq=2 ttl=64 time=0.273 ms",
            "64 bytes from 10.0.0.187: icmp_seq=3 ttl=64 time=0.227 ms",
            "64 bytes from 10.0.0.187: icmp_seq=4 ttl=64 time=0.252 ms",
            "64 bytes from 10.0.0.187: icmp_seq=5 ttl=64 time=0.269 ms",
            "64 bytes from 10.0.0.187: icmp_seq=6 ttl=64 time=0.252 ms",
            "64 bytes from 10.0.0.187: icmp_seq=7 ttl=64 time=0.228 ms",
            "64 bytes from 10.0.0.187: icmp_seq=8 ttl=64 time=0.265 ms",
            "64 bytes from 10.0.0.187: icmp_seq=9 ttl=64 time=0.246 ms",
            "64 bytes from 10.0.0.187: icmp_seq=10 ttl=64 time=0.257 ms",
            "",
            "--- 10.0.0.187 ping statistics ---",
            "10 packets transmitted, 10 received, 0% packet loss, time 9213ms",
            "rtt min/avg/max/mdev = 0.227/0.266/0.393/0.044 ms",
            ""
        ]
    }
    ```

## Listing network address usage

Each network is associated with a single IP address block which is used to allocate
addresses for that network. You can view the state of allocations on that address
block using the REST API.

???+ tip "REST API calls"

    * [GET ​/networks​/{network_ref}​/addresses](https://openapi.shakenfist.com/#/networks/get_networks__network_ref__addresses): list address allocations for a given network.

??? example "Python API client: list address allocations for a network"

    ```python
    import ipaddress
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    addresses = sf_client.get_network_addresses(network_ref)

    info_by_addr = {}
    for addr in addresses:
        info_by_addr[int(ipaddress.IPv4Address(addr['address']))] = addr

    print('address,type,user,comment')
    for addr in sorted(info_by_addr.keys()):
        if not info_by_addr[addr]['user']:
            user = ''
        elif type(info_by_addr[addr]['user']) is list:
            user = ' '.join(info_by_addr[addr]['user'])
        else:
            user = info_by_addr[addr]['user']

        print('%s,%s,%s,%s'
              % (info_by_addr[addr]['address'],
                 info_by_addr[addr]['type'],
                 ' '.join(info_by_addr[addr]['user']),
                 info_by_addr[addr]['comment']))
    ```

## Routed IPs

Unlikely floating IPs, which are associated with a network interface, routed IPs
are associated with a network -- its up to the virtual network to decide who will
answer for that address via ARP responses. The API calls for routed IPs are therefore
documented here instead of on the network interfaces page.

???+ tip "REST API calls"

    * [POST /networks/{network_ref}/route](https://openapi.shakenfist.com/#/networks/post_networks__network_ref__route): route an address to this network.
    * [DELETE ​/networks​/{network_ref}​/route​/{address}](https://openapi.shakenfist.com/#/networks/delete_networks__network_ref__route__address_): unroute an address.

??? example "Python API client: add a routed address"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    routed = sf_client.route_network_address(network_ref)
    ```

??? example "Python API client: remove a routed address"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.unroute_network_address(network_ref, address)
    ```

## Metadata

All objects exposed by the REST API may have metadata associated with them. This
metadata is for storing values that are of interest to the owner of the resources,
not Shaken Fist. Shaken Fist does not attempt to interpret these values at all,
with the exception of the [instance affinity metadata values](/user_guide/affinity/).
The metadata store is in the form of a key value store, and a general introduction
is available [in the user guide](/user_guide/metadata/).

???+ tip "REST API calls"

    * [GET ​/networks/{network_ref}​/metadata](https://openapi.shakenfist.com/#/networks/get_networks__network_ref__metadata): Get metadata for a network.
    * [POST /networks/{network_ref}/metadata](https://openapi.shakenfist.com/#/networks/post_networks__network_ref__metadata): Create a new metadata key for a network.
    * [DELETE /networks/{network_ref}/metadata/{key}](https://openapi.shakenfist.com/#/networks/delete_networks__network_ref__metadata__key_): Delete a specific metadata key for a network.
    * [PUT /networks/{network_ref}/metadata/{key}](https://openapi.shakenfist.com/#/networks/put_networks__network_ref__metadata__key_): Update an existing metadata key for a network.

??? example "Python API client: set metadata on a network"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_network_metadata_item(net_uuid, 'foo', 'bar')
    ```

??? example "Python API client: get metadata for a network"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    md = sf_client.get_network_metadata(net_uuid)
    print(json.dumps(md, indent=4, sort_keys=True))
    ```

??? example "Python API client: delete metadata for a network"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_network_metadata_item(net_uuid, 'foo')
    ```