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

## VDI token public keys

Since v0.8, Shaken Fist signs Kerbside VDI console tokens with a cluster
Ed25519 key and publishes the **public** half here so the Kerbside proxy can
verify tokens offline. The response is the active key id and every currently
published public key (`kid`, `alg`, `public_pem`, and `created`); the private
key material is never served. It returns HTTP 404 until a signing key exists —
bootstrap one with `sf-ctl ensure-kerbside-signing-key`. See the
[VDI console tokens operator guide](/operator_guide/vdi_console_tokens/) for
custody and rotation.

???+ tip "REST API calls"

    * [GET /admin/vditokenpubkey](https://openapi.shakenfist.com/#/admin/get_admin_vditokenpubkey): Retrieve the public half of the Kerbside VDI console token signing key(s).

??? example "Python API client: fetch the VDI token public keys"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    keys = sf_client.get_vdi_token_public_keys()
    print(json.dumps(keys, indent=4, sort_keys=True))
    ```

## CA certificate

You can retrieve the CA certificate used for TLS in this cluster, for example
to configure a SPICE client to trust the hypervisors' SPICE server
certificates. The response is a PEM encoded certificate.

???+ tip "REST API calls"

    * [GET /admin/cacert](https://openapi.shakenfist.com/#/admin/get_admin_cacert): Retrieve the CA certificate used for TLS in this cluster.
