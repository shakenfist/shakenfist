# Admin (/admin/)

## Locks

As discussed in the [operator guide](/operator_guide/locks/), you can query
what locks exist in a Shaken Fist cluster, as well as who is currently holding
those locks (machine and process id).

???+ tip "REST API calls"

    * [GET /admin/locks](https://openapi.shakenfist.com/#/admin/get_admin_locks): List locks currently held in the cluster.

??? example "Python API client: list cluster locks"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    locks = sf_client.get_existing_locks()

    print('lock,pid,node,operation')
    for ref, meta in locks.items():
        print('%s,%s,%s,%s' % (ref, meta['pid'], meta['node'], meta.get('operation')))
    ```

    ```bash
    $ python3 example.py
    lock,pid,node,operation
    /sflocks/sf/network/d2950d74-50c7-4790-a985-c43d9eb9bad9,2834066,sf-3,Network ensure mesh
    ```