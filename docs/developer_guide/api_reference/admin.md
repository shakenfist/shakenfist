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

## Resources

The scheduler's view of the cluster's capacity: per-node headroom for each
hypervisor the scheduler would consider, the nodes it would not and why
(`excluded`), and the cluster-wide capacity ledger (`cluster_capacity`).
The meaning of every field is in the
[scheduler operator guide](/operator_guide/scheduler/#diagnosing-a-placement-decision).
The ledger and the exclusions are advertised by the `cluster-capacity` and
`cluster-resources-excluded` capabilities, and a server without them omits
those keys.

???+ tip "REST API calls"

    * [GET /admin/resources](https://openapi.shakenfist.com/#/admin/get_admin_resources): Summarize the cluster's schedulable resources.

??? example "Python API client: summarize cluster resources"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    resources = sf_client.get_cluster_resources()

    print('Nodes with headroom: %s' % ', '.join(resources['per_node']))
    for node, why in resources.get('excluded', {}).items():
        print('Excluded %s: %s' % (node, why['reason']))

    ledger = resources.get('cluster_capacity', {})
    if ledger.get('row_present'):
        print('Unallocated vCPUs: %d' % ledger['unallocated_cpus'])
    print(json.dumps(resources['total'], indent=4, sort_keys=True))
    ```

## Claims

Every namespace capacity claim in the cluster, in one request. The
per-namespace listing is under `/auth/namespaces/`. The optional
`coverage_state` is `active` or `expired`; anything else is a 400. The
[scheduler operator guide](/operator_guide/scheduler/#listing-every-namespaces-claims)
explains what the listing includes and why. This needs a client with
`get_cluster_claims`, and a server advertising the `cluster-claims`
capability.

???+ tip "REST API calls"

    * [GET /admin/claims](https://openapi.shakenfist.com/#/admin/get_admin_claims): List the capacity claims held by every namespace.

??? example "Python API client: list every active claim"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    for claim in sf_client.get_cluster_claims(coverage_state='active'):
        print('%s: %s vCPUs' % (claim['namespace'], claim['limit_cpus']))
    ```

    Omit `coverage_state` (or pass `None`) to list claims in every state.

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
