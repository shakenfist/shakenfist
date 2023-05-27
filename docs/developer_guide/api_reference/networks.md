# Networks (/networks/)

Not yet documented.



??? example "Python API client: create a network"
    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    n = sf_client.allocate_network('10.0.0.0/24', True, True, 'example')
    print(json.dumps(n, indent=4, sort_keys=True))
    ```


    ```
    $ python3 example.py
    {
        "floating_gateway": "192.168.10.16",
        "metadata": {},
        "name": "example",
        "namespace": "system",
        "netblock": "10.0.0.0/24",
        "provide_dhcp": true,
        "provide_nat": true,
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

    ```
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

    ```
    $ python3 example.py
    [
        {
            "name": "sfcbr-7YWeQo4BoqLjASDd",
            "namespace": "sfcbr-7YWeQo4BoqLjASDd",
            "netblock": "10.0.0.0/24",
            "provide_dhcp": true,
            "provide_nat": true,
            "state": "created",
            "uuid": "759b742d-6140-475e-9553-ac120b56c1ef",
            "vxlan_id": 0
        }
    ]
    ````