---
title: Kerbside
---

# Running Kerbside beside a cluster

This page is for an operator who has read [Installation](installation.md)
and wants to decide where Kerbside runs: on a hypervisor, on a host of its
own, somewhere you deploy yourself, or behind a load balancer. It covers what
each placement needs from the network, where the proxy's certificate comes
from, and what the integration does not do yet. The variables themselves are
documented in [Installation](installation.md#set-the-deployment-variables)
for the Shaken Fist side, and in the
[shakenfist.kerbside collection's README](https://github.com/shakenfist/kerbside/tree/develop/ansible)
for Kerbside's own role. This page links to them rather than repeating them.

## What Kerbside is here

[Kerbside](/components/kerbside/) is a SPICE console proxy, and a separate
project. With it, a user's viewer connects to Kerbside rather than to a
hypervisor: `sf-api` mints a short lived signed token, Kerbside exchanges it
for a console file naming itself, and then relays the SPICE session to the
hypervisor that runs the instance. Users never need a route to a hypervisor.
The token flow, the signing key and the credential Kerbside scrapes the
cluster with are described in
[VDI console tokens](vdi_console_tokens.md). `examples/_shared/site.yml`
deploys Kerbside onto the hosts in an optional `kerbside` inventory group,
using the `kerbside` role from Kerbside's `shakenfist.kerbside` collection.
The role installs Kerbside 0.7.0 or newer, the first release whose API never
returns a source's password; an older Kerbside cannot be deployed this way.

## Topologies

Put Kerbside on a hypervisor unless you have a reason not to. That is the
shape Shaken Fist's weekly CI lane deploys, and it needs the fewest
decisions.
A dedicated host is the next simplest. Bring your own Kerbside only if you
already run one, because you then configure this cluster's source in it by
hand.

| Topology | How to get it | What it needs |
|----------|---------------|---------------|
| Co-located on a hypervisor | Put a host in both `allsf` and `kerbside`. | Nothing more. `api_url` may stay a loopback address, because the host's own `sf-api` answers it. |
| A dedicated host | Put a host in `kerbside` but not in `allsf`. | An `api_url` the host can reach that is not a loopback address. |
| Bring your own | Set `kerbside_url`, and leave `kerbside` absent or empty. | This cluster's source, configured in that Kerbside by hand. |
| One Kerbside for several clusters or platforms | Any of the above, plus `kerbside_extra_sources`. | Every Shaken Fist cluster among the sources uses the same `kerbside_url`. |

### Co-located on a hypervisor

A `kerbside` host which is also in `allsf` runs Kerbside beside Shaken
Fist's daemons. The playbook tells Kerbside's role which ports to leave
alone there: those Shaken Fist's daemons listen on (13000, 13001, 13005,
13006 and 13007), and every port from 30000 up, where instance consoles
live. Kerbside's defaults (5900 and 5901 for SPICE, 13002 for its API and
13003 for its metrics) clear all of them, so the deploy refuses only ports
you have moved into one of those ranges.

### A dedicated host

A `kerbside` host which is not in `allsf` runs nothing of Shaken Fist's. The
playbook still reaches it, probes it, and validates it with its own
variables. Kerbside fetches the console token signing key from `api_url`, so
on this host `api_url` must name an address of the Shaken Fist API which it
can reach. The deploy refuses a loopback `api_url` here, before it changes
any host. Every port from 30000 up is still reserved here, as on a
hypervisor, so Kerbside's ports must stay below 30000 wherever it runs.

### Bring your own

With `kerbside_url` set and no `kerbside` hosts, the deploy does only the
Shaken Fist side: it renders `KERBSIDE_URL`, ensures the signing key exists,
and, if you set `kerbside_system_key`, mints the credential. Everything on
the Kerbside side is yours:

* a console source of type `shakenfist` for this cluster, using `api_url`,
  the username `system`, the `kerbside_system_key` value as its password,
  and the CA which `/admin/cacert` serves;
* Kerbside's token audience set to exactly the `kerbside_url` value.

[Console sources](/components/kerbside/console-sources/#shaken-fist)
describes the source, and
[Kerbside's configuration](/components/kerbside/configuration/#shaken-fist-console-tokens)
the audience.

### One Kerbside for several clusters or platforms

`kerbside_extra_sources` appends further console sources after this
cluster's own, so one Kerbside can proxy oVirt, OpenStack, static consoles
or other Shaken Fist clusters as well. The collection README's
[Kerbside section](https://github.com/shakenfist/shakenfist/blob/develop/shakenfist/deploy/collection/README.md#kerbside)
has an example. Two things follow when an extra source is another Shaken
Fist cluster:

* **Every cluster uses the same `kerbside_url`.** A Kerbside has one token
  audience, and every cluster's `KERBSIDE_URL` must equal it exactly.
* **Deploy the other clusters first,** each with that `kerbside_url` and its
  own `kerbside_system_key`. The deploy which installs Kerbside waits until
  Kerbside has fetched the signing keys of every Shaken Fist source, so a
  cluster which has no key yet fails that deploy.

## The network

Kerbside needs routes to three places, and so do users.

| From | To | Why |
|------|----|-----|
| Kerbside | Every hypervisor's instance console ports (Shaken Fist allocates them from 30000 to 50000) | To relay SPICE sessions. |
| Kerbside | `api_url` | To scrape the cluster and fetch the signing keys. |
| Kerbside | Its database, at `kerbside_sql_url` | All of Kerbside's state. |
| Users | The Shaken Fist API | To ask for a console. |
| Users | `kerbside_url` | To exchange the token for a console file. |
| Users | `kerbside_public_fqdn`, on both SPICE ports | To run the session. |

The first row is the one most often missed. Scraping works over the API
alone, so a firewall between Kerbside and the hypervisors gives a console
list that looks healthy and connections that fail. Kerbside's
[Shaken Fist use case](/components/kerbside/use-cases/shakenfist/#network)
explains the reasoning, and its
[network ports page](/components/kerbside/network-ports/) lists every port.

Users need both SPICE ports, not only the TLS one. The console file names
both, and most SPICE clients connect to the plaintext port first, which only
tells them to move to TLS. [Why the insecure port cannot be
skipped](/components/kerbside/network-ports/#why-the-insecure-port-cannot-be-skipped)
explains why.

Kerbside's API serves plain HTTP, on port 13002 by default, and the deploy
does not put TLS in front of it. The console file it returns carries the
password for the SPICE session, so put a TLS-terminating reverse proxy in
front of the API even when there is only one Kerbside host. The load
balancer below is one.

## Load balancing

You can put several Kerbside hosts behind one name. Give them the same
database and the same settings, balance the API as HTTP, and pass the two
SPICE ports through as plain TCP. Neither leg needs session affinity.

!!! warning "Not tested"

    No CI lane runs more than one Kerbside host, in Shaken Fist or in
    Kerbside. What follows is reasoned from Kerbside's code and from the
    deploy, not proven by a test. Try it somewhere you can afford to be
    wrong first.

### What the hosts share

Kerbside's hosts coordinate only through the database. Nothing on one host
calls another. A console token is a row in that database, so it is not tied
to the host which issued it:

* the API on one host exchanges a Shaken Fist token and stores the
  console's session token in the database;
* the proxy on any host looks the token up there when a SPICE client
  presents it, and connects the client to the hypervisor;
* every channel of a session is authorised that way on its own, so a
  session's channels may land on different hosts;
* a token's single-use check, and a request to terminate a session, are
  both database rows too, and each host drops the channels it holds.

Every host in the `kerbside` group gets the same configuration from the same
inventory, and the deploy migrates the shared database once. Keep these the
same on every host. `group_vars/kerbside` does that for the settings only
Kerbside reads; `kerbside_url` and `api_url` belong in `group_vars/all`,
because Shaken Fist's nodes read them too:

| Setting | Why every host needs the same value |
|---------|-------------------------------------|
| `kerbside_sql_url` | It is the only state the hosts share. |
| `kerbside_auth_secret_seed` | It signs Kerbside's own API sessions, so a session one host issued is valid at another. |
| `kerbside_url` | It is the token audience each host checks. The deploy already refuses a value which differs from Shaken Fist's. |
| `kerbside_public_fqdn` | The console file names it, so a user reaches the load balancer whichever host issued the file. It is also the name in the proxy's certificate. |
| `api_url` | Every host stores this cluster's source in the shared database. Hosts which see different values rewrite it, and log a configuration change, every minute. |

The last row means a co-located host should not use the loopback `api_url`
when it shares a database with a dedicated one. Give every Kerbside host the
same routable address of the API.

Each host keeps its own name, `inventory_hostname`, which Kerbside records
against the channels that host holds. Do not give two hosts the same
inventory name.

### The API leg

Balance the API as ordinary HTTP. Terminate TLS at the load balancer with a
certificate your users' clients trust, for the host named in
`kerbside_url`, and forward to port 13002 on each Kerbside host. The token's
audience is the whole `kerbside_url`, and the exchange URL is
`<kerbside_url>/sf-console.vv`, so the simplest shape gives `kerbside_url`
no path and forwards `/` unchanged.

```nginx
upstream kerbside_api {
    # One server line per Kerbside host.
    server 10.0.0.21:13002 max_fails=2 fail_timeout=10s;
    server 10.0.0.22:13002 max_fails=2 fail_timeout=10s;
}

server {
    listen 443 ssl;
    server_name kerbside.example.com;

    ssl_certificate     /etc/ssl/certs/kerbside.example.com.pem;
    ssl_certificate_key /etc/ssl/private/kerbside.example.com.key;

    location / {
        proxy_pass http://kerbside_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Here `kerbside_url` is `https://kerbside.example.com` and
`kerbside_public_fqdn` is `kerbside.example.com`. A load balancer with
active health checks can probe `GET /` with `Accept: application/json`,
which returns HTTP 200 while the API is up. That is the check the deploy
itself waits on. The hop to port 13002 is plain HTTP, so run it over a
network you trust, as [Load balancing](load_balancing.md) says of
`sf-api`.

### The SPICE leg

Pass both SPICE ports through as TCP. Do not terminate TLS there. Kerbside's
proxy terminates the TLS itself, and the console file tells the client which
CA to trust and which certificate subject to expect, both of which belong to
the proxy, not to a load balancer.

Kerbside's role tells clients to connect to the same port numbers it
listens on: 5900 for TLS and 5901 for the plaintext redirect, unless you
moved them. So the load balancer must listen on those same port numbers,
and cannot run on a Kerbside host, where the proxy holds them.

```nginx
stream {
    upstream kerbside_spice_tls {
        # One server line per Kerbside host.
        server 10.0.0.21:5900;
        server 10.0.0.22:5900;
    }

    upstream kerbside_spice_redirect {
        server 10.0.0.21:5901;
        server 10.0.0.22:5901;
    }

    server {
        listen 5900;
        proxy_pass kerbside_spice_tls;
        # nginx closes a stream after 10 minutes without data by
        # default. Kerbside's proxy allows 15 (IDLE_READ_TIMEOUT, fixed
        # in its code), so do not cut sessions sooner.
        proxy_timeout 15m;
    }

    server {
        listen 5901;
        proxy_pass kerbside_spice_redirect;
    }
}
```

The `stream` block sits at the top level of `nginx.conf`, beside `http`,
not inside it. Each channel of a session is authorised separately, so plain
round robin works. If you would rather keep each client on one host while
this shape is untested, add `hash $remote_addr consistent;` to each
upstream.

Kerbside does not read the PROXY protocol, so with passthrough the client
address it records in its audit trail is the load balancer's.

### Redeploys

A redeploy which changes Kerbside restarts it on every host in the same
task, so it drops every console session at once, on every host. Kerbside's
role can be run with `serial` for a rolling restart; its README's
[Redeploys](https://github.com/shakenfist/kerbside/tree/develop/ansible#redeploys)
section says what that needs.

## Certificates

By default each Kerbside host's proxy certificate comes from the cluster's
own CA, the same `internal_ca` which issues the hypervisors' SPICE
certificates. Users need not trust that CA in advance, because the console
file carries it.

For each Kerbside host, the deploy issues a certificate:

* with the common name `kerbside_public_fqdn`, and the organisation
  `Shaken Fist CA for <deploy_name>`;
* with `kerbside_public_fqdn` as its one subject alternative name: a DNS
  name, or an IP address if `kerbside_public_fqdn` is an IPv4 or IPv6
  address;
* generated on the control node, as
  `/srv/shakenfist/pki/CA/<inventory_hostname>-kerbside-server-cert.pem`
  and its key;
* installed on the host as `server-cert.pem`, `server-key.pem` and
  `ca-cert.pem` in `/etc/kerbside/pki`, with the key readable by the
  `kerbside` group.

Each host has its own key, but every host's certificate has the same
subject and is signed by the same CA. The certificate lasts a year, and a
deploy renews it when fewer than 90 days remain, or when
`kerbside_public_fqdn` changes.

Kerbside reads the certificate's subject at deploy time and puts it, and the
CA, in every console file it issues. The SPICE client then checks the
proxy's certificate against both. This is why the SPICE leg of a load
balancer must pass TLS through.

### Bringing your own certificate

To use your own certificate, set all three of `kerbside_proxy_cert_path`,
`kerbside_proxy_key_path` and `kerbside_cacert_path` to files already on
each Kerbside host. Set all three or none: the deploy refuses anything in
between, and with all three set it issues nothing. The
[Certificates section of the role's README](https://github.com/shakenfist/kerbside/tree/develop/ansible#certificates)
describes them. The deploy stops if Kerbside cannot render the certificate's
subject, which happens when it has an attribute other than `C`, `ST`, `L`,
`O`, `OU`, `CN`, `DC` or `emailAddress`.

### Behind a load balancer

A console file is issued by one host and used at whichever host the load
balancer picks. So every Kerbside host must present a certificate which:

* is valid for `kerbside_public_fqdn`;
* has the same subject as every other host's;
* is signed by the CA in every other host's `kerbside_cacert_path`.

The certificates `internal_ca` issues meet all three by construction. With
your own, the simplest way to meet them is one CA and one subject for every
host, or the same certificate and key on every host.

## What you give up today

Consoles work. Kerbside's own management does not, without more work.

* **No admin login.** Kerbside's login is Keystone-only, and the deploy
  configures no Keystone, so its web UI and its management API cannot be
  logged in to
  ([kerbside#300](https://github.com/shakenfist/kerbside/issues/300)).
  Listing sessions or terminating one through Kerbside's API therefore
  needs a session token signed with `kerbside_auth_secret_seed`, which is a
  symmetric key that can mint a token for anyone
  ([kerbside#301](https://github.com/shakenfist/kerbside/issues/301)).
  Treat the seed as an administrator credential.
* **No pinned CA for the API.** Kerbside's Shaken Fist client verifies
  `api_url`'s TLS against the Kerbside host's system trust store, not
  against the CA in the source
  ([kerbside#136](https://github.com/shakenfist/kerbside/issues/136)). If
  `api_url` is HTTPS with a certificate from a private CA, add that CA to
  each Kerbside host's trust store. That workaround is not yet verified:
  the `requests` in Kerbside's virtualenv ships its own certificate bundle,
  and the units set no `REQUESTS_CA_BUNDLE`, so it may not consult the
  host's store at all
  ([#4699](https://github.com/shakenfist/shakenfist/issues/4699)).
* **A full `system` credential.** Kerbside scrapes the whole cluster with a
  key in the `system` namespace, which the deploy mints with no scopes, so
  it is a cluster administrator credential
  ([#4698](https://github.com/shakenfist/shakenfist/issues/4698) tracks
  narrowing it). Anyone who can read `kerbside_system_key`, including from a
  Kerbside host's `sources.yaml`, can act as `system`.
  [The Kerbside credential](vdi_console_tokens.md#the-kerbside-credential)
  covers rotating and revoking it.

## Related pages

* [VDI console tokens](vdi_console_tokens.md) -- the Shaken Fist side: the
  token, the signing key, and the credential.
* [Installation](installation.md#set-the-deployment-variables) -- the
  variables, and the database Kerbside needs.
* [Load balancing](load_balancing.md) -- the Shaken Fist API behind a load
  balancer.
* [Kerbside component documentation](/components/kerbside/) -- the proxy
  itself.
* [The shakenfist.kerbside collection's README](https://github.com/shakenfist/kerbside/tree/develop/ansible)
  -- the role's variables, ports and redeploy behaviour.
