# Kerbside in containers

This page is for running Kerbside in containers, with compose or on
Kubernetes. Most of it says what does not exist yet, so that you can
tell before you start how much of the work is yours.

## Who this is for

An operator who wants Kerbside in containers. The short answer is
that Kerbside has no first-party container image
([#553](https://github.com/shakenfist/kerbside/issues/553)), no
production compose file, and nothing for Kubernetes: no chart, no
manifests and no operator.

Three mechanisms do exist and are documented.
[By hand](/components/kerbside/deployment/manual/) installs Kerbside on a host with pip and
systemd, and every step on it says what to create and why, which is
what a container image of your own has to reproduce.
[Ansible](/components/kerbside/deployment/ansible/) does the same on hosts you choose, with
Kerbside's own role.
[Kolla-Ansible](/components/kerbside/deployment/kolla-ansible/) deploys Kerbside in containers as
part of an OpenStack cloud, with Kolla's images.

This page is about how Kerbside is run, not about why. The pieces
any running Kerbside needs are described once, in
[What a running Kerbside needs](/components/kerbside/installation/#what-a-running-kerbside-needs),
and this page assumes you have read it.

## What it deploys

Two things exist, and neither is a production container deployment.

**The demo stack.** `demo/` is a compose file for three containers:
MariaDB, a disk-less qemu with a SPICE server, and Kerbside with both
of its processes. Its image is built on your machine at
`docker compose up`, from the latest Kerbside release on PyPI. It is
for watching a proxied console work before you decide how to deploy
Kerbside, and
[Try it: the demo stack](/components/kerbside/installation/#try-it-the-demo-stack)
walks through it.

**Kolla's images.** Kolla builds Kerbside images and publishes them
on quay.io. Checked on **2026-10-28**:

| Fact | Value |
|------|-------|
| Repositories | `quay.io/openstack.kolla/kerbside-api` and `quay.io/openstack.kolla/kerbside-proxy` |
| Tags | `master` and `2026.2`, each for `debian-trixie`, `rocky-10` and `ubuntu-noble`, and each of those also as `-aarch64`: twelve per repository |
| Last pushed | 2026-10-28 for the x86-64 tags; the `-aarch64` tags are about a week older |
| Built from | Kerbside's `develop` branch, on both Kolla master and Kolla's `stable/2026.2` |

Kolla's
[`kolla/common/sources.yaml`](https://opendev.org/openstack/kolla/src/branch/master/kolla/common/sources.yaml)
is the authority on what is built: its `kerbside-base` entry names
`https://github.com/shakenfist/kerbside` at reference `develop`. So
an image holds whatever `develop` held when Kolla last built it, not
a Kerbside release, and two pulls of the same tag a day apart can
hold different Kerbsides. That is the opposite of the advice in
[Installing with pip](/components/kerbside/installation/#installing-with-pip), which
is to install a tagged release because a release pins the exact
`kerbside-proxy` it was tested with.

The images are thin. Each is Kolla's `openstack-base` with a
`kerbside` user and Kerbside pip-installed, and each runs as
`kerbside`. They hold no Kerbside configuration and no entrypoint of
Kerbside's own. They start through Kolla's `kolla_start`, which runs
`kolla_set_configs` to copy files into place as a
`/var/lib/kolla/config_files/config.json` describes, and then runs
the command that file names. `kolla_set_configs` refuses to run
unless `KOLLA_CONFIG_STRATEGY` is set in the container's environment,
and the image does not set it. In a Kolla-Ansible deployment,
Kolla-Ansible sets the strategy, and its `kerbside` role writes the
`config.json` and Kerbside's configuration for each container, as
[What it deploys](/components/kerbside/deployment/kolla-ansible/#what-it-deploys) on the
Kolla-Ansible page describes.

Nothing runs these images outside Kolla-Ansible, and this page gives
no recipe for doing so.

## How to use it

For the demo, follow
[Try it: the demo stack](/components/kerbside/installation/#try-it-the-demo-stack).
[What the demo is not](/components/kerbside/installation/#what-the-demo-is-not)
lists the shortcuts it takes, and
[demo/README.md](https://github.com/shakenfist/kerbside/blob/develop/demo/README.md)
gives the reason for each. For Kolla's images, follow
[the Kolla-Ansible page](/components/kerbside/deployment/kolla-ansible/).

### What a production container deployment has to settle

There is no production compose file here, not even as an example. A
compose file has to name an image, and the only candidates are the
demo's, whose own comments say it is not for production, and Kolla's,
which are built from `develop` and started by `kolla_start`. An
example built on either would be a second, untested deployment
mechanism. The file belongs with a first-party image, in #553.

If you build your own, these are the four things the demo does not
answer for you:

- **The image.** The demo builds one locally, from PyPI, and keeps
  its build dependencies: `mysqlclient` has no wheel, so it is
  compiled at install time against the MariaDB client headers. The
  demo stays in one stage because a split build has to keep the
  `libmariadb` the result links against at runtime. A production
  image has to pin a Kerbside release, decide whether to split the
  build from the runtime, and add the client libraries your sources
  need from
  [Client libraries for console sources](/components/kerbside/installation/#client-libraries-for-console-sources).
- **The split.** The daemon (`kerbside daemon run`) and the Rust
  proxy it launches as its child talk over a unix socket,
  `API_SOCKET_PATH`
  ([configuration.md](/components/kerbside/configuration/#control-plane-grpc-service)),
  so they share a container: the proxy is not a separate service.
  The REST API never dials that socket and reaches the daemon only
  through the database, so it can be its own container, anywhere
  that can reach the database and has the same `sources.yaml` and
  CA certificate as the daemon.
- **Configuration and secrets.** Kerbside reads
  `/etc/kerbside/kerbside.ini`, a path it hardcodes, and any
  `KERBSIDE_*` environment variable overrides a setting in it. The
  sources are in the file `sources_path` names. The token signing
  seed, the database password, each source's credentials and the
  proxy's private key are all secrets, and both processes read both
  files. Mount the secrets as files rather than passing them as
  `KERBSIDE_*` variables: a variable shows in `docker inspect`, and
  in the compose file that sets it.
  [By hand](/components/kerbside/deployment/manual/#what-it-deploys) gives the ownership and modes
  it uses, and the reasons, which carry over to mounted secrets.
- **The certificate lifecycle.** The demo issues its own
  certificates for 30 days, inside the container, and regenerates
  them when they near expiry. A production deployment takes the
  proxy's certificate, key and CA from whatever issues certificates
  for you. The proxy reads its certificate and key once, when it
  starts, so a renewed certificate takes effect only when the proxy
  restarts. The proxy is the daemon's child, so in practice that is a
  restart of the daemon, and it ends every live SPICE session the
  container carries.

### Kubernetes

Nothing exists for Kubernetes: no Helm chart, no manifests and no
operator. Each would need an image to deploy, so all of them wait on
#553. The four points above apply to a pod as much as to a compose
service.

## Status and limitations

Kerbside is [experimental](/components/kerbside/index/#kerbside-is-experimental)
overall. The `demo-compose` lane builds and runs the demo stack on
pushes and pull requests that touch it. It is advisory and
path-filtered ([testing.md](/components/kerbside/testing/#ci-tiers)).

| Not covered | Why |
|-------------|-----|
| No first-party image | Kerbside publishes no container image of its own. [#553](https://github.com/shakenfist/kerbside/issues/553) tracks one. |
| No production compose file | A compose file needs an image to name, and neither candidate is built or tested for that. |
| The documented demo build is not run | `demo-compose` builds from the checkout, with `KERBSIDE_SOURCE=/src`. The PyPI default the demo documents is run by no lane, as `demo/docker-compose.yml` says itself. |
| Kolla's images are built from `develop` | They carry unreleased code, and nothing tests them outside Kolla-Ansible. They start only through `kolla_start` and a `config.json` that Kolla-Ansible writes. |
| No Kubernetes support | No chart, manifests or operator exist, and none can until there is an image. |
| Certificate renewal ends sessions | The proxy loads its certificate and key only at start, and restarting it drops every session it carries. There is no handover. |
