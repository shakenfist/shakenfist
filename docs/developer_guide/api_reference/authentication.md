# Authentication (/auth/)

## Create an API access token

Access to the REST API is granted via an access token. These tokens expire, so
you may also have to request new tokens for long lived applications from time
to time. You will receive a HTTP 401 status code if an access token has expired.

???+ note
    For further details of the authentication scheme, see the
    [developer guide](/developer_guide/authentication/).


???+ tip "REST API calls"

    * [POST /auth](https://openapi.shakenfist.com/#/auth/post_auth): Create an access token.

??? example "Python API client: creating an access token"

    The Python API client handles creating access tokens and refreshing them
    for you, so not specific action is required for this API call. The following
    code implies creation of an access token:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ```

??? example "curl: creating an access token"

    ```bash
    $ curl -X POST https://shakenfist/api/auth -d '{"namespace": "system", "key": "oisoSe7T"}'
    {
        "access_token": "eyJhbG...IkpXVCJ9.eyJmc...wwQ",
        "token_type": "Bearer",
        "expires_in": 900
    }
    ```

    This token is then used by passing it as a HTTP Authorization header with
    "Bearer " prepended:

    ```bash
    $ curl -X GET https://shakenfist/api/auth/namespaces \
        -H 'Authorization: Bearer eyJhbG...IkpXVCJ9.eyJmc...wwQ' \
        -H 'Content-Type: application/json'
    [
        {
            "name": "adhoc",
            "state": "created",
            "trust": {"full": ["system"]}
        }, {
            "name": "ci",
            "state": "created",
            "trust": {"full": ["system"]}
        }, {
            "name": "system",
            "state": "created",
            "trust": {"full": ["system"]}
        }
    ]
    ```

## Namespaces

Resources in a Shaken Fist cluster are divided up into logical groupings called
namespaces. All namespaces have equal permissions, except for the `system`
namespace, which is used for administrative tasks.

???+ note

    For a detailed reference on the state machine for namespaces, see the
    [developer documentation on object states](/developer_guide/state_machine/#namespaces).

???+ tip "REST API calls"

    * [GET /auth/namespaces](https://openapi.shakenfist.com/#/auth/get_auth_namespaces): List all namespaces visible to your currently authenticated namespace.
    * [POST /auth/namespaces](https://openapi.shakenfist.com/#/auth/post_auth_namespaces): Create a namespace, if you have permissions to do so.
    * [DELETE /auth/namespaces/{namespace}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace_): Delete a namespace.
    * [GET /auth/namespaces/{namespace}](https://openapi.shakenfist.com/#/auth/get_auth_namespaces__namespace_): Get details of a single namespace.

??? example "Python API client: list namespaces"

    This example lists all namespaces visible to the caller:

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ns = sf_client.get_namespaces()
    print(json.dumps(ns, indent=4, sort_keys=True))
    ```

    Which returns something like:

    ```json
    [
        {
            "keys": [
                "jenkins"
            ],
            "metadata": {},
            "name": "ci",
            "state": "created",
            "trust": {
                "full": [
                    "system"
                ]
            },
            "version": 5
        },
        ...
    ]
    ```

??? example "Python API client: create a namespace"

    This example creates a new namespace, which is only possible if you are
    currently authenticated as the `system` namespace:

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ns = sf_client.create_namespace('demo')
    print(json.dumps(ns, indent=4, sort_keys=True))
    ```

    Which returns something like:

    ```json
    {
        "keys": [],
        "metadata": {},
        "name": "demo",
        "state": "created",
        "trust": {
            "full": [
                "system"
            ]
        },
        "version": 5
    }
    ```

??? example "Python API client: delete a namespace"

    This example deletes a namespace, which is only possible if you are
    currently authenticated as the `system` namespace:

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ns = sf_client.delete_namespace('demo')
    print(json.dumps(ns, indent=4, sort_keys=True))
    ```

    The call does not return anything.

??? example "Python API client: get details of a single namespace"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    ns = sf_client.get_namespace('demo')
    print(json.dumps(ns, indent=4, sort_keys=True))
    ```

    Which returns something like:

    ```json
    {
        "keys": [],
        "metadata": {},
        "name": "demo",
        "state": "created",
        "trust": {
            "full": [
                "system"
            ]
        },
        "version": 5
    }
    ```

## Namespace keys

Callers authenticate to a namespace by providing a key to a call to `/auth/` as
discussed above. The calls discussed in this section relate to the management of
the keys used to authenticate to a namespace.

???+ tip "REST API calls"

    * [GET /auth/namespaces/{namespace}/keys](https://openapi.shakenfist.com/#/auth/get_auth_namespaces__namespace__keys): List all authentication keys for a given namespace.
    * [POST /auth/namespaces/{namespace}/keys](https://openapi.shakenfist.com/#/auth/post_auth_namespaces__namespace__keys): Create a new key for a namespace.
    * [DELETE /auth/namespaces/{namespace}/keys/{key_name}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace__keys__key_name_): Delete a specific key for a namespace.
    * [PUT /auth/namespaces/{namespace}/keys/{key_name}](https://openapi.shakenfist.com/#/auth/put_auth_namespaces__namespace__keys__key_name_): Update a key for a namespace.

??? example "Python API client: list all keys for a namespace"

    This example lists all the keys in a namespace:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    keys = sf_client.get_namespace_keynames('ci')
    print(keys)
    ```

    Which returns something like:

    ```json
    ['jenkins']
    ```

??? example "Python API client: create a new key for a namespace"

    This example adds a key to a namespace and then lists all keys:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.add_namespace_key('ci', 'newkey', 'thesecretvalue')

    # Fetch the list of keys to make sure the new one exists
    keys = sf_client.get_namespace_keynames('ci')
    print(keys)
    ```

    Which returns something like:

    ```json
    ['jenkins', 'newkey']
    ```

???+ info "Key expiry"

    The create (`POST`) and update (`PUT`) calls accept an optional `expiry`
    body parameter, as epoch seconds. A key with no expiry never expires,
    which is the default and the behaviour of every key created before this
    parameter existed.

    The value must be a number in the future. An expiry in the past is
    rejected with a 400 rather than creating a key which is unusable the
    instant it exists, since that is far more likely to be a units mistake
    -- milliseconds instead of seconds, say -- than an intent.

    An expired key can neither mint new tokens nor validate a request, from
    the moment it lapses. Tokens already minted from it remain valid until
    their own expiry; delete the key if you need those invalidated
    immediately.

    Note that updating a key rotates it, and rotation replaces the whole
    mutable attribute set. Updating a key without passing `expiry` clears
    any expiry it previously carried.

    The `sf-client` command line does not expose a flag for this yet, so use
    the REST API or the Python client directly for now.

??? example "Python API client: remove a specific key from a namespace"

    This example deletes a key from the namespace and then lists all keys:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_namespace_key('ci', 'newkey')

    # Fetch the list of keys to make sure the new one exists
    keys = sf_client.get_namespace_keynames('ci')
    print(keys)
    ```

    Which returns something like:

    ```json
    ['jenkins']
    ```

??? example "Python API client: update the secret portion of an existing namespace key"

    This example updates the secret portion of an existing namespace key to a new value:

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.update_namespace_key('ci', 'newkey', 'newsecret')
    ```

## Trusted issuers

A **trusted issuer** is an external identity provider this cluster will
believe: GitHub Actions, an Authentik instance, or anything else that
signs OIDC-style JWTs. Issuers are cluster-wide and administrative --
deciding who may vouch for identities here is not a per-namespace
decision -- so every call below requires the `system` namespace.

An issuer records four things: a `name` used to refer to it, the
`issuer_url` that must match a token's `iss` claim exactly, the
`jwks_uri` its signing keys are published at, and the `audience` its
tokens must be minted for. The `jwks_uri` always comes from this
record and never from the token, because a token naming its own key
source is a token vouching for itself.

???+ tip "REST API calls"

    * [GET /auth/issuers](https://openapi.shakenfist.com/#/auth/get_auth_issuers): List all trusted issuers.
    * [POST /auth/issuers](https://openapi.shakenfist.com/#/auth/post_auth_issuers): Configure a new trusted issuer.
    * [GET /auth/issuers/{issuer_name}](https://openapi.shakenfist.com/#/auth/get_auth_issuers__issuer_name_): Fetch one trusted issuer.
    * [PUT /auth/issuers/{issuer_name}](https://openapi.shakenfist.com/#/auth/put_auth_issuers__issuer_name_): Update a trusted issuer.
    * [DELETE /auth/issuers/{issuer_name}](https://openapi.shakenfist.com/#/auth/delete_auth_issuers__issuer_name_): Remove a trusted issuer.

??? example "Configuring GitHub Actions as a trusted issuer"

    ```bash
    curl -X POST https://sf.example.com/auth/issuers \
      -H "Authorization: Bearer ${SF_ADMIN_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{
            "name": "github",
            "issuer_url": "https://token.actions.githubusercontent.com",
            "jwks_uri": "https://token.actions.githubusercontent.com/.well-known/jwks",
            "audience": "https://sf.example.com"
          }'
    ```

???+ warning "Deleting an issuer"

    Mapping rules reference their issuer by name. Deleting an issuer
    does not delete the rules that name it -- those rules simply stop
    working, because the exchange can no longer resolve the issuer.
    Recreating an issuer under the same name rebinds every rule that
    named it, so treat the name as the durable identifier it is.

## Mapping rules

A **mapping rule** is a namespace's standing offer: "an identity from
this issuer, carrying these claims, may mint a key here with these
scopes". Rules are owned by the namespace they mint into and are gated
by namespace ownership, exactly as key creation is -- a rule is the
same class of privilege as `add-key`, granted in advance and gated on
claims.

Rules are deleted with their namespace, so "who can get into this
namespace" is answered by listing its rules. That listing is the
inbound sibling of the trust list.

A rule carries:

| Field | Meaning |
|-------|---------|
| `name` | Unique within the namespace, and named by the exchange request |
| `issuer` | The trusted issuer whose tokens this rule accepts |
| `bound_claims` | Claims a token must carry, and the values they must have |
| `scopes` | The scopes minted keys receive |
| `key_ttl` | How long a minted key lives, in seconds |
| `key_name_prefix` | Prefix for minted key names; the cluster appends a random discriminator |

`bound_claims` values are exact strings, or lists of exact strings
meaning "any of these". There is no globbing and no pattern matching:
`shakenfist/*` looks reasonable until somebody registers
`shakenfist-evil`, and the anchored patterns needed to make that safe
are exactly what reviewers get wrong. A rule must bind at least one
claim and grant at least one scope, both enforced at creation, because
a rule that binds nothing matches every identity the issuer will ever
sign.

???+ tip "REST API calls"

    * [GET /auth/namespaces/{namespace}/rules](https://openapi.shakenfist.com/#/auth/get_auth_namespaces__namespace__rules): List the mapping rules for a namespace.
    * [POST /auth/namespaces/{namespace}/rules](https://openapi.shakenfist.com/#/auth/post_auth_namespaces__namespace__rules): Create a mapping rule.
    * [GET /auth/namespaces/{namespace}/rules/{rule_name}](https://openapi.shakenfist.com/#/auth/get_auth_namespaces__namespace__rules__rule_name_): Fetch one mapping rule.
    * [PUT /auth/namespaces/{namespace}/rules/{rule_name}](https://openapi.shakenfist.com/#/auth/put_auth_namespaces__namespace__rules__rule_name_): Replace a mapping rule's policy.
    * [DELETE /auth/namespaces/{namespace}/rules/{rule_name}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace__rules__rule_name_): Delete a mapping rule.

??? example "A rule for one repository and two branches"

    ```bash
    curl -X POST https://sf.example.com/auth/namespaces/ci/rules \
      -H "Authorization: Bearer ${SF_TOKEN}" \
      -H "Content-Type: application/json" \
      -d '{
            "name": "ryll",
            "issuer": "github",
            "bound_claims": {
              "repository": "shakenfist/ryll",
              "ref": ["refs/heads/develop", "refs/heads/main"]
            },
            "scopes": ["blob.read", "artifact.*"],
            "key_ttl": 3600,
            "key_name_prefix": "ryll-ci"
          }'
    ```

???+ info "Updating a rule does not touch keys already minted"

    A minted key stands alone. Its provenance records the claims that
    were actually satisfied, so the audit trail describes the grant as
    it was made rather than as the rule reads today. Narrowing a
    rule's scopes therefore does not retroactively narrow keys it
    minted earlier -- delete those keys if that is what you need.

## Federated exchange

`POST /auth/federated` trades an [identity token](/glossary/#identity-token)
from a trusted issuer for a namespace key. It is unauthenticated by
nature: the caller has no Shaken Fist credential yet, which is the
entire point. What stands in place of authentication is the token's
signature, checked against the issuer's published keys, plus a
mapping rule the namespace owner wrote in advance.

The request names three things, and the response returns the minted
secret exactly once:

```json
{"token": "eyJ...", "namespace": "ci", "rule": "ryll"}
```

```json
{"namespace": "ci", "key_name": "ryll-ci-8fJ2mQ", "key": "sfk_..."}
```

The secret is never returned again and is never written to an event or
a log -- only its bcrypt hash is stored. Use it immediately to call
`POST /auth` for an access token, exactly as you would any other
namespace key.

???+ tip "REST API calls"

    * [POST /auth/federated](https://openapi.shakenfist.com/#/auth/post_auth_federated): Exchange an identity token for a namespace key.

???+ info "What a refusal tells you"

    | Status | Meaning |
    |--------|---------|
    | 400 | A required field is missing |
    | 401 | The exchange was refused, with a category but no detail |
    | 413 | The request body exceeds `FEDERATION_MAX_TOKEN_BYTES` |
    | 429 | Too many attempts from this source address |
    | 503 | The database was unavailable, so the exchange could not be checked |

    A 401 deliberately says less than the audit log records. Telling
    an anonymous caller *which* claim missed would turn the endpoint
    into an oracle for guessing a rule's contents, one request at a
    time. The namespace that owns the rule sees the detail in its
    events, which is where a stream of near-miss claim failures --
    what probing looks like -- belongs.

???+ warning "An identity token is single-use per rule"

    Once a token has been exchanged through a given rule it cannot be
    exchanged through that rule again. The same token *can* still be
    exchanged through a different rule to reach a second namespace,
    which is a legitimate pattern: a workflow needing two namespaces
    exchanges its token twice against two rules.

    A refusal for any other reason does not consume the token, so
    fixing a rule and retrying with a still-valid token works.

## Metadata

All objects exposed by the REST API may have metadata associated with them. This
metadata is for storing values that are of interest to the owner of the resources,
not Shaken Fist. Shaken Fist does not attempt to interpret these values at all,
with the exception of the [instance affinity metadata values](/user_guide/affinity/).
The metadata store is in the form of a key value store, and a general introduction
is available [in the user guide](/user_guide/metadata/).

???+ tip "REST API calls"

    * [GET ​/namespaces/{namespace}​/metadata](https://openapi.shakenfist.com/#/auth/get_auth_namespaces__namespace__metadata): Get metadata for a namespace.
    * [POST /namespaces/{namespace}/metadata](https://openapi.shakenfist.com/#/auth/post_auth_namespaces__namespace__metadata): Create a new metadata key for a namespace.
    * [DELETE /namespaces/{namespace}/metadata/{key}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace__metadata__key_): Delete a specific metadata key for a namespace.
    * [PUT /namespaces/{namespace}/metadata/{key}](https://openapi.shakenfist.com/#/auth/delete_auth_namespaces__namespace__trust__external_namespace_): Update an existing metadata key for a namespace.

??? example "Python API client: set metadata on a namespace"

    ```python
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.set_artifact_metadata_item(img_uuid, 'foo', 'bar')
    ```

??? example "Python API client: get metadata for a namespace"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    md = sf_client.get_artifact_metadata(img_uuid)
    print(json.dumps(md, indent=4, sort_keys=True))
    ```

??? example "Python API client: delete metadata for a namespace"

    ```python
    import json
    from shakenfist_client import apiclient

    sf_client = apiclient.Client()
    sf_client.delete_artifact_metadata_item(img_uuid, 'foo')
    ```