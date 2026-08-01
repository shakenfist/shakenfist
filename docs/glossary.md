# Glossary

This page is the single authoritative definition of the vocabulary that Shaken
Fist's code, command line, and documentation use. Where a word is overloaded --
inside Shaken Fist, or against the vocabulary of an external tool -- the entry
names the sibling meaning so that a reader who arrived by search is not misled.
Other documents should link here rather than redefining these terms.

Definitions describe what the code does *today*. A few entries describe concepts
that are planned but not yet implemented (the workload identity federation
work): these carry a
*(planned -- see [PLAN-auth-federation](plans/PLAN-auth-federation.md))*
marker, and will lose it as the features land. An entry without the marker
describes something that exists in the current release.

Entries are alphabetical. Each carries a short definition, a "Defined in"
pointer to the authoritative code or documentation, and, where the term is
overloaded, a "Not to be confused with" note.

**access token**{#access-token} -- A Shaken Fist-issued JWT, minted from a
[namespace key](#namespace-key) via `/auth` and nonce-bound to that key. The
token's identity is the string `<namespace uuid>:<keyname>`, and it carries the
minting key's [nonce](#nonce) as a claim so that revoking the key immediately
invalidates the token. Defined in `shakenfist/util/access_tokens.py`. Not to be
confused with the release pipeline's OIDC token (see [token](#token) and
[Release pipeline vocabulary](#release-pipeline)).

**agent**{#agent} -- The in-guest agent, `sf-agent`, which runs inside an
instance and is reachable from the hypervisor over the
[side channel](#side-channel). Defined in
`docs/developer_guide/agent_protocol.md`. Not to be confused with: an *agent
operation*, which is the queued command object executed by the in-guest agent
(`docs/developer_guide/api_reference/agentoperations.md`); or the unrelated
sense in `AGENTS.md`, where "agent" means an AI coding assistant such as Claude
Code or GitHub Copilot.

**artifact**{#artifact} -- A named, versioned, namespaced *reference* to a
sequence of [blobs](#blob). An artifact has a type: `image`, `snapshot`,
`label`, or `other`. Lookup by reference accepts either a UUID or a name.
Defined in `shakenfist/artifact.py` (see also
`docs/plans/PLAN-artifact-ux-rework.md`). Not to be confused with a GitHub
Actions / release "artifact", a build output of the CI and release pipeline
(`docs/developer_guide/release_process.md`).

**blob**{#blob} -- An immutable, content-addressed, reference-counted lump of
bytes, identified only by UUID. Blobs are the storage that artifact versions
point at, are replicated across nodes, and are deleted only when their reference
count reaches zero. Defined in `shakenfist/blob.py` (see also
`docs/plans/PLAN-artifact-ux-rework.md`). Note that documentation occasionally
uses the lowercase word "blob" generically to mean "a lump of data"; the object
type is the sense meant here.

**capability**{#capability} -- The client's server-feature-probe mechanism: the
client asks the server whether an optional feature is present before relying on
it, for example `sf_client.check_capability('blob-search-by-hash')`. Defined in
`docs/user_guide/usage.md` (the client "knows how to probe the server for
capabilities") and `docs/developer_guide/api_reference/blobs.md`. Not to be
confused with an authorization term: capabilities do not grant or restrict
access in Shaken Fist. The authorization noun is [scope](#scope).

**cluster operation**{#cluster-operation} -- The internal work-queue object that
tracks a unit of cluster-wide work through its own state machine. Defined in
`ARCHITECTURE.md` ("Cluster Operation Tracking" and "Cluster Operation Storage
and Work Queues"). Not to be confused with an *agent operation*, which is
executed by the in-guest [agent](#agent) rather than by cluster daemons.

**credential**{#credential} -- In the documentation this word appears mainly for
MariaDB database credentials -- the username and password an operator configures
for the database tier. Defined in `docs/operator_guide/database.md`. The
platform's own credential concept is the [namespace key](#namespace-key), from
which callers derive access tokens.

**DatabaseBackedObject / DBO**{#dbo} -- The base class that gives an object type
its UUID identity, state machine, attributes, and events. Most user-visible
object types (instances, artifacts, blobs, networks, uploads, nodes) derive from
it. Defined in `shakenfist/baseobject.py`.

**event**{#event} -- The umbrella audit-log mechanism. Many operations -- object
creation and deletion, authentication, any change to an object's data -- record
an event against the relevant object. Events have typed categories: `audit`,
`mutate`, `status`, `usage`, `resources`, `prune`, and `historic`, each with its
own retention period. Defined in `docs/user_guide/events.md`. Note: "audit
event" strictly means an event of type `audit`; documentation sometimes uses the
phrase loosely to mean "the event that serves as the audit record", which may be
of another type.

**identity token**{#identity-token}
*(planned -- see [PLAN-auth-federation](plans/PLAN-auth-federation.md))*
-- An externally-issued JWT proving workload or user identity, for example a
GitHub Actions OIDC token or an Authentik-issued token. It is presented to
Shaken Fist to be exchanged for a scoped [namespace key](#namespace-key).
Defined in `docs/plans/PLAN-auth-federation.md` ("Authentication terms to pin").

**instance**{#instance} -- The virtual machine object: a running (or startable)
VM with disks, network interfaces, and a lifecycle. Defined in
`shakenfist/instance.py` and `docs/developer_guide/api_reference/instances.md`.
Not to be confused with the generic "daemon instance" / "replica" sense used in
`ARCHITECTURE.md` and `docs/operator_guide/database.md`, where "instance" means
one running copy of the `sf-database` daemon.

**key**{#key} -- Primary sense: a [namespace key](#namespace-key). Other in-tree
senses of the word that a reader may land on: the inter-node *service keys* used
for daemon-to-daemon authentication (the reserved `_service_key*` name prefix,
`docs/developer_guide/authentication.md`); SSH keys used by the deployment tools
(`SSH_KEY_FILENAME`, `docs/operator_guide/installation.md`); *metadata keys*,
the names in an object's metadata map (`docs/user_guide/metadata.md`);
configuration keys / options; and SQL primary keys. When this documentation says
"key" without qualification it means a namespace key.

**label**{#label} -- An [artifact](#artifact) of type `label`: a
manually-curated pointer where the user explicitly chooses which blob each
version uses. Its name is path-like (`label` or `namespace/label`), and it is
created implicitly on first update. Defined in `shakenfist/artifact.py` (see
also `docs/plans/PLAN-artifact-ux-rework.md`). Not to be confused with
Prometheus/Loki metric and log labels (`docs/operator_guide/events.md`,
`docs/operator_guide/logging.md`) or GitHub issue labels such as `ci-failure`
(`docs/developer_guide/workflow.md`).

**mapping rule**{#mapping-rule}
*(planned -- see [PLAN-auth-federation](plans/PLAN-auth-federation.md))*
-- A first-class object, owned by the namespace it targets, that is a standing
claim-gated authorization to mint keys there: a [trusted issuer](#trusted-issuer)
reference, bound claims, [scopes](#scope), and an expiry. Defined in
`docs/plans/PLAN-auth-federation.md` ("Authentication terms to pin").

**namespace**{#namespace} -- The tenancy and authorization unit: objects belong
to a namespace, and a caller acts within one. Visibility extends to a caller's
own namespace and to namespaces that [trust](#trust) it. Defined in
`shakenfist/namespace.py` and `docs/user_guide/objects.md`. Not to be confused
with Linux network namespaces, which the networking documentation uses heavily
(`docs/operator_guide/networking/overview.md`).

**namespace key**{#namespace-key} -- The stored credential from which
[access tokens](#access-token) are minted: a bcrypt hash, a [nonce](#nonce),
and an optional expiry. [Scopes](#scope) are recorded on the object and
enforced on every request made with a token minted from it; a key with no
scopes recorded mints wildcard tokens, which is every key predating the
federation work. Provenance is reserved but not yet populated -- it arrives
with the federation exchange. `/auth` bcrypt-compares a presented secret
against the namespace's
unexpired keys and mints a token bound to the matching key's nonce. A key is a
first-class object owned by its namespace, with its own lifecycle, events and
soft delete. Defined in `shakenfist/namespace_key.py` (the object) and
`shakenfist/namespace.py` (the `keys`, `lookup_key`, `add_key` and `remove_key`
members), and described in `docs/developer_guide/authentication.md`.

**node**{#node} -- A cluster member machine. A node carries one or more *roles*,
which are configuration, not object types:

* *primary node* -- the node that runs the API and on which `sf-client` and
  authentication details are installed; one per cluster
  (`docs/operator_guide/installation.md`).
* *network node* -- the elected, singular ingress and egress point for all
  virtual networks, where floating IPs live; one per cluster
  (`docs/operator_guide/installation.md`, `ARCHITECTURE.md`).
* *hypervisor node* -- a node that runs virtual machine instances
  (`docs/operator_guide/installation.md`).
* *database-tier node* -- a node running an `sf-database` daemon against the
  shared MariaDB; none is elected (`ARCHITECTURE.md`,
  `docs/operator_guide/database.md`).
* *storage-only node* -- a node that stores blobs but runs no VMs, used to hold
  blobs that are not needed by currently running instances (`docs/features.md`).

The node object itself is defined in `shakenfist/node.py`; it is the only object
type that is never hard-deleted (`docs/developer_guide/state_machine.md`).

**nonce**{#nonce} -- The per-key value embedded in the
[access tokens](#access-token) derived from a [namespace key](#namespace-key)
and re-verified on every request. When a key is updated, deleted, or expires,
its nonce changes or disappears; a request whose token carries a stale nonce is
rejected. The nonce is therefore the revocation mechanism for derived tokens.
Defined in `docs/developer_guide/authentication.md` (lines 203 and 218-224) and
enforced in `shakenfist/external_api/base.py`.

**object / by reference**{#object} -- Everything a caller interacts with is an
object, usually referred to by a version-4 UUID string; the exceptions named by
name are nodes, namespaces, and keys. Referring to an object *by reference* means
passing either its name or its UUID, and a by-name lookup searches every object
visible to the caller -- those in the caller's own namespace, and those in
namespaces that trust it (plus shared artifacts for artifact commands). Defined
authoritatively in `docs/user_guide/objects.md`.

**Release pipeline vocabulary**{#release-pipeline} -- The release process signs
and publishes releases using Sigstore keyless signing, PyPI Trusted Publishers,
and a GitHub Actions OIDC token (issuer `token.actions.githubusercontent.com`).
This vocabulary -- "trusted publisher", "OIDC token", "keyless signing", release
"artifacts" -- is entirely unrelated to the runtime authentication system
(namespace [trust](#trust), [trusted issuers](#trusted-issuer),
[identity tokens](#identity-token)). Defined in
`docs/developer_guide/release_process.md`.

**scope**{#scope} -- A `resource-family.verb` string naming a class of
operation that a [namespace key](#namespace-key) (and the tokens derived from
it) may perform, for example `blob.read` or `instance.delete`. Scope is the
authorization noun in Shaken Fist. Families and verbs are derived mechanically
from the resource class and the HTTP method, so an endpoint is governed the
moment it exists. Three forms are special: `*` grants everything and is what a
key with no scopes recorded carries; `<family>.*` grants every verb in one
family; and `cluster-admin` is required, alongside the `system` namespace, for
administrative endpoints. `cluster-admin` is hyphenated rather than dotted
because it names no family, and so no family wildcard can synthesise it.
Defined in `shakenfist/external_api/scopes.py` and
`docs/developer_guide/authentication.md`. Not to be
confused with (a) [capability](#capability), which already names the client's
server-feature-probe mechanism -- scope is not "capability"; or (b) the Linux
`ip` scope attribute (`scope global`, `scope link`, `scope host`) that appears
verbatim in `docs/operator_guide/networking/overview.md`.

**side channel**{#side-channel} -- The virtio-serial / virtio-vsock transport
between a hypervisor and the in-guest [agent](#agent), over which the agent
protocol's serialized messages flow. Defined in
`docs/developer_guide/agent_protocol.md` (and used by name in
`docs/developer_guide/api_reference/instances.md`). Not to be confused with the
security-research sense of "side channel" (timing or cache side channels).

**snapshot**{#snapshot} -- An [artifact](#artifact) of type `snapshot`, produced
from an instance's disk(s). Snapshots are created and listed through *instance*
routes, not artifact routes. Defined in `shakenfist/artifact.py` (see also
`docs/plans/PLAN-artifact-ux-rework.md`).

**state / state machine**{#state-machine} -- Shaken Fist enforces a state
machine per object type, defined by the `state_targets` map in each object
class. States are per-type, so the same label can mean different things:
`created` means "running" for an instance, "has at least one version" for an
artifact, and "checked in at least once" for a node. Defined in
`docs/developer_guide/state_machine.md`. Note the soft-delete state `deleted`
(the object is hidden from the API) is distinct from the later hard deletion
that removes it from MariaDB; and that instances additionally have an orthogonal
power-state axis (`docs/operator_guide/power_states.md`).

**system namespace**{#system-namespace} -- The reserved administrative
[namespace](#namespace). Membership of `system` is exactly what `caller_is_admin`
checks today: a caller whose token resolves to the `system` namespace is treated
as an administrator. Defined in `shakenfist/external_api/base.py`.

**token**{#token} -- Primary sense: an [access token](#access-token). Not to be
confused with the release pipeline's GitHub Actions / Sigstore OIDC token
(`docs/developer_guide/release_process.md`), which is unrelated to runtime
authentication and is closest in nature to the planned
[identity token](#identity-token).

**trust**{#trust} -- The existing namespace-to-namespace visibility grant: if
namespace A trusts namespace B, then callers in B can see A's objects by
reference. Defined in `shakenfist/namespace.py` (the `trust` member and
`add_trust`/`remove_trust`) and `docs/operator_guide/authentication.md`. Not to
be confused with: a [trusted issuer](#trusted-issuer), which is about
accepting external token issuers rather than sharing objects between namespaces;
the release pipeline's Sigstore / PyPI "Trusted Publisher" vocabulary (see
[Release pipeline vocabulary](#release-pipeline)); or generic network-trust prose
elsewhere in the documentation.

**trusted issuer**{#trusted-issuer} -- An external token issuer the cluster is
configured to accept, recorded with its JWKS location and expected audience.
Cluster level rather than namespaced, because deciding who may vouch for
identities is an administrative decision, and managed through `/auth/issuers`
by administrators only. Defined in `shakenfist/trusted_issuer.py` and
`docs/operator_guide/authentication.md`. Not to be
confused with namespace [trust](#trust), nor with the release pipeline's "Trusted
Publisher" (see [Release pipeline vocabulary](#release-pipeline)).

**upload**{#upload} -- The short-lived staging object used to stream bytes to a
node before they are turned into a [blob](#blob). It backs the `/upload` REST
resource. Defined in `shakenfist/upload.py`. Not to be confused with
`sf-client instance upload` -- the agent-protocol file upload into a running
instance (`docs/developer_guide/agent_protocol.md`) -- which is a different
mechanism that happens to share the word.
</content>
