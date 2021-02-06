The big ideas of v0.4
=====================

The focus of v0.4 is reliability -- we've used behaviour in the continuous integration pipeline as a proxy for that, but it should be a significant improvement in the real world as well. This has included:

* much more extensive continuous integration coverage, including several new jobs.
* checksumming image downloads, and retrying images where the checksum fails.
* reworked locking.
* etcd reliability improvements.
* refactoring instances and networks to a new "non-volatile" object model where only immutable values are cached.
* images now track a state much like instances and networks.
* a reworked state model for instances, where its clearer why an instance ended up in an error state. This is documented in [our developer docs](../development/state_machine.md).

In terms of new features, we also added:

* a network ping API, which will emit ICMP ping packets on the network node onto your virtual network. We use this in testing to ensure instances booted and ended up online.
* networks are now checked to ensure that they have a reasonable minimum size.
* addition of a simple etcd backup and restore tool (sf-backup).
* improved data upgrade of previous installations.
* VXLAN ids are now randomized, and this has forced a new naming scheme for network interfaces and bridges.
* we are smarter about what networks we restore on startup, and don't restore dead networks.

We also now require python 3.8.

Changes between v0.4.0 and v0.4.1
=================================

v0.4.1 was released on 26 January 2021.

* Remove stray persist() call from floating network setup. shakenfist#667
* Networks should enter the 'created' state once setup on the network node. shakenfist#669

Changes between v0.4.1 and v0.4.2
=================================

v0.4.2 was released on 6 February 2021.

* Improved CI for image API calls.
* Improved upgrade CI testing.
* Improved network state tracking.
* Floating IPs now work, and have covering CI. shakenfist#257
* Resolve leaks of floating IPs from both direct use and NAT gateways. shakenfist#256
* Resolve leaks of IPManagers on network delete. shakenfist#675
* Use system packages for ansible during install.