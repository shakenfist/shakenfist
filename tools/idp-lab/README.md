# The identity provider lab

Two OpenID Connect providers in Docker — Keycloak and Authentik —
configured for a human login the way `docs/operator_guide/authentication.md`
describes, so that the worked examples in that guide can be re-run
rather than remembered, and so the captured provider documents under
`shakenfist/tests/files/idp/` can be regenerated.

Those captures are the committed evidence for what two real providers
emit: the discovery documents the cluster's discovery code is tested
against, and the decoded claim sets a group-bound mapping rule is
tested against. A capture nobody can reproduce is a capture nobody can
check, which is the first reason this exists. The second is that the
provider configuration is the part of a walkthrough which rots: both
providers rename settings between releases, and a lab with pinned
image tags is how a stale instruction gets noticed.

## This is a development lab and not a deployment

Every port is bound to loopback. The certificates are self signed and
valid for two days. The databases are throwaway volumes which the
teardown deletes. The administrator credentials are generated per lab
and kept outside this repository. There is no backup, no upgrade path,
no hardening, and no second node. Do not copy any of it into
anything real — the operator guide, not this directory, is what
describes a deployment.

## What you need

Docker with the compose plugin, `openssl`, `python3` and about 2GB of
memory. Nothing else: no Python packages, no `jq`, and nothing from
this repository's test environment.

## Bringing it up

```bash
tools/idp-lab/lab-up.sh
tools/idp-lab/configure-keycloak.sh
tools/idp-lab/configure-authentik.sh
```

`lab-up.sh` generates the lab's credentials and certificate, starts the
four containers, and waits for both providers to answer. Authentik's
first boot runs its database migrations and takes a few minutes on a
cold host; the script waits on its readiness endpoint rather than
sleeping, so it finishes as soon as the provider does.

The two configure scripts then create, through each provider's own
administrative API, everything a login needs: a realm, a group, a
public client, a group membership mapper writing the claim into the
**identity** token and a person in that group at one provider; and a
provider, an application, a device code flow at the brand, the grant
types, the property mappings and a signing key at the other. Each
script says what it created and why in its own header comment, which
is also where the settings whose defaults do not work are written
down. Both are safe to re-run.

Afterwards:

| | Keycloak | Authentik |
|---|---|---|
| Interface | <https://localhost:8443/admin/> | <https://localhost:9443/if/admin/> |
| Administrator | `admin` | `akadmin` |
| Issuer | `https://localhost:8443/realms/shakenfist` | `https://localhost:9443/application/o/sf/` |
| Group claim | `"groups": ["/sf-admins"]` | `"groups": ["sf-admins"]` |

The leading slash on one of them is not a typo, and neither is the
trailing slash on the other issuer. Both are explained in the operator
guide, and both are why there are two walkthroughs rather than one.

## Credentials

There is no credential in this repository and there is not meant to be
one. `lab-up.sh` generates every password, token and signing key with
`openssl rand` into `${IDP_LAB_STATE_DIR}/lab.env` — by default
`${TMPDIR:-/tmp}/sf-idp-lab/lab.env`, mode 0600, outside the working
tree — and the other scripts read them from there.

Export any of these before the first run to choose one instead, which
is the documented way to get a lab with a password you can type:

| Variable | What it is | Default |
|---|---|---|
| `IDP_LAB_STATE_DIR` | where credentials and certificates live | `${TMPDIR:-/tmp}/sf-idp-lab` |
| `IDP_LAB_KEYCLOAK_ADMIN_USER` | Keycloak's bootstrap administrator | `admin` |
| `IDP_LAB_KEYCLOAK_ADMIN_PASSWORD` | its password | generated |
| `IDP_LAB_AUTHENTIK_ADMIN_PASSWORD` | `akadmin`'s password | generated |
| `IDP_LAB_AUTHENTIK_API_TOKEN` | the bootstrap API token | generated |
| `IDP_LAB_AUTHENTIK_SECRET_KEY` | Authentik's own secret key | generated |
| `IDP_LAB_POSTGRES_PASSWORD` | the database password | generated |
| `IDP_LAB_PERSON_PASSWORD` | the lab person's password, which is the one a human login needs | generated |

Delete `lab.env` to generate a new set. Anything you export is a
throwaway for a loopback container, so treat it as one: these are
written to a file, passed to containers as environment variables, and
visible to anything else running as you.

## Regenerating the captured provider documents

With both providers configured:

```bash
tools/idp-lab/capture-fixtures.sh
```

That drives a complete device login at each provider — the flow
`sf-client login` uses, including the confirmation step a person has to
press at each of them — and overwrites the files under
`shakenfist/tests/files/idp/`, so the result is a diff in the working
tree. The same Keycloak login also saves the two HTML pages a person sees
during it, the sign-in page and the device confirmation page, as
`keycloak-device-signin-page.html` and `keycloak-device-confirm-page.html`.
Those are the pages the cluster suite's device login test is written
against, and are scrubbed on the way out: the session code, the
`execution` and `tab_id` identifiers, the grant code and the argument to
`checkAuthSession(...)`, which is an auth session identifier in a script
that a scrub of the forms alone would miss. `--output-dir` writes them
somewhere else instead, and `--provider` does one provider.

**The result will not be byte-identical to what is committed, and is
not meant to be.** Signing keys, timestamps (`exp`, `iat`,
`auth_time`), token and session identifiers (`jti`, `sid`, `at_hash`)
and the subject are per-login or per-container, and one provider
serves its `scopes_supported` in a different order every time. What
*should* be identical, and what is worth reading a regenerated capture
for, is:

* the set of member names in each discovery document, and in
  particular the set of endpoint names;
* each issuer string, character for character;
* the set of claim names in each identity token;
* the group claim: an array of strings in both, with a leading slash
  in one of them and not the other.

A difference in any of those is a change in a provider or in the lab's
configuration. It is a thing to understand before committing, not a
thing to commit.

Two rules the capture tool enforces rather than trusts. It refuses to
write a claim set whose person-identifying members are not the
invented lab person's, so a lab pointed at a directory of real people
cannot write somebody's name and group memberships into a public
repository; and it replaces the subject, which is the one member
either provider generates, with an obvious placeholder. No signed
token and no credential is written anywhere.

The realm name, the application slug and both ports are not free
choices: the committed captures carry the issuer URLs they produce,
and the tests which read those captures compare the issuers character
for character. `lab-common.sh` says so where it sets them.

## Taking it down

```bash
tools/idp-lab/lab-down.sh          # containers, volumes and network
tools/idp-lab/lab-down.sh --purge  # those, and the generated credentials
```

The teardown checks afterwards that no lab container is left and says
so, because a provider still listening on 8443 tomorrow is the one
failure mode of a lab like this one.
