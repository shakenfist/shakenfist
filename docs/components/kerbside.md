# Kerbside

Kerbside is a [SPICE](https://spice-space.org/) protocol native VDI proxy
responsible for providing rich VDI experiences to users of Shaken Fist, oVirt,
OpenStack, or any other cloud where the hypervisor is capable of providing SPICE
consoles over the network.

The SPICE protocol has existed for a long time, and still represents the
richest and most performant option for remote desktops using Open Source
technologies. Before Kerbside, consoles were generally provided by a HTML5
transcoded interface in a web client. Unfortunately, HTML5 interfaces do not
support many of the more novel features of the SPICE protocol, nor do they
support high resolution desktops. By avoiding transcoding to a HTML5 client,
we avoid these problems.

Novel features of SPICE include high resolution desktops, multi-screen desktops,
USB device passthrough, sound, multiple user connections to a single console,
adaptive compression, and more.

## Kerbside is experimental

Kerbside is currently considered experimental. While it works, it has not yet
seen large scale deployment and it is likely that it will needed modifications
as it is hardened for production use.

## Kerbside is a proxy, not a complete user interface

Whilst Kerbside presents a simple administrative interface over HTTP and has
REST APIs for orchestrating consoles, it is not intended as a complete SPICE
desktop VDI solution. It is intended that Kerbside itself is orchestrated by
an external system. That is, in order for a desktop to be presented to a user
the following steps need to occur:

* The user requests a desktop via an external user interface that we call "the
  Broker". In Shaken Fist's case the broker is embedded in Shaken Fist itself
  and is initiated via a Shaken Fist REST API. In the OpenStack case this role
  is likely performed by Horizon or Skyline, although this is not yet implemented.

* The cloud boots the instance that runs the desktop. The Broker waits for the
  instance to be booted.

* The Broker requests a `.vv` virt-viewer compatible ini file from Kerbside, and
  delivers that to the requesting user. The configuration file describes a
  connection to Kerbside, along with short lived access token.

* The user opens the `.vv` file with a SPICE client such as `remote-viewer`.
  `remote-viewer` connects to Kerbside.

* Kerbside uses the access token to determine which instance in the cloud is
  the requested desktop and initiates a proxied connection to the hypervisor.

* The user then happily uses their SPICE console, largely unaware of these
  various steps.

## Implementation in OpenStack

The Kerbside implementation in OpenStack is still experimental and has not yet
landed upstream. There is a
[proposed Nova specification](https://review.opendev.org/c/openstack/nova-specs/+/915190)
to add the APIs required to orchestrate the consoles, but it is not yet approved.
Apart from the modifications to Nova's APIs there are some minor changes to
the libvirt domain XML that must be made to expose full SPICE console support
in virtual machines managed by OpenStack.

Additionally, Kerbside needs to be deployed as a component of the OpenStack
cluster in order to provide a safe mechanism for users to interact with their
console. OpenStack is (wisely) unwilling to provide direct network connectivity
from a client network to TCP ports on the hypervisor, and so Kerbside acts as
an intermediary to protect those hypervisors. There is a sample implementation
of Kerbside deployment using Kolla-Ansible in the
[Kerbside Patches repository](https://github.com/shakenfist/kerbside-patches).

### What about Bumblebee?

The folks over at the NECTAR research cloud developed
[Bumblebee VDI](https://github.com/NeCTAR-RC/bumblebee), which is superficially
similar to Kerbside in that it provides a mechanism to make it easier to obtain
a virtual desktop as a user. The Kerbside description above would classify
Bumblebee as a Broker to our model -- it orchestrates the creation and then
access to virtual desktops for users. However, Bumblebee exclusive orchestrates
HTML5 consoles using Apache Guacamole as its HTML5 proxy at the moment, so misses
out on some of the richer features of SPICE and has the performance implications
of a HTML5 desktop environment.
