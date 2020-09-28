Changes between v0.2.7 and v0.2.8
=================================

v0.2.8 has not yet been released.

* Allow the setting of numeric configuration values. [Bug 324](https://github.com/shakenfist/shakenfist/issues/324).
* Backing file information must be provided with modern libvirts. [Bug 326](https://github.com/shakenfist/shakenfist/issues/326).


Changes between v0.2.6 and v0.2.7
=================================

v0.2.7 was released on 26 September 2020.

* Fix import error in scheduler.py

Changes between v0.2.5 and v0.2.6
=================================

v0.2.6 was released on 26 September 2020.

* Fix typo in gunicorn command line.

Changes between v0.2.4 and v0.2.5
=================================

v0.2.5 was released on 3 August 2020.

* API requests without a JSON payload fail when made to a non-network node. [Bug 261](https://github.com/shakenfist/shakenfist/issues/261).
* Resolve error during instance start. [Bug 263](https://github.com/shakenfist/shakenfist/issues/263).
* Resolve a crash in the network monitor. [Bug 264](https://github.com/shakenfist/shakenfist/issues/264).

Changes between v0.2.3 and v0.2.4
=================================

v0.2.4 was released on 2 August 2020.

* Remove stray networks when detected. [Bug 161](https://github.com/shakenfist/shakenfist/issues/161).
* Display video details of an instance in sf-client. [Bug 216](https://github.com/shakenfist/shakenfist/issues/216).
* The login prompt trigger is now more reliable. [Bug 223](https://github.com/shakenfist/shakenfist/issues/223).
* Rapid creates from terraform can crash Shaken Fist with a race condition. [Bug 225](https://github.com/shakenfist/shakenfist/issues/225).
* Instance deletes could leave internal IP addresses allocated to deleted instances. [Bug 227](https://github.com/shakenfist/shakenfist/issues/227).
* sf-client now displays network card model. [Bug 228](https://github.com/shakenfist/shakenfist/issues/228).
* Missing node metrics could cause a scheduler crash. [Bug 236](https://github.com/shakenfist/shakenfist/issues/236).
* A missing import caused a scheduler crash. [Bug 237](https://github.com/shakenfist/shakenfist/issues/236).
* You can now list the instances on a network with sf-client. [Bug 240](https://github.com/shakenfist/shakenfist/issues/240).
* Disabling DHCP for networks did not work correctly. [Bug 245](https://github.com/shakenfist/shakenfist/issues/245).
* Ubuntu 18.04's cloud-init would issue warnings about network interface types. [Bug 250](https://github.com/shakenfist/shakenfist/issues/250).
* Network interfaces were sometimes leaked. [Bug 252](https://github.com/shakenfist/shakenfist/issues/252).
* sf-client would sometimes crash if the disk bus was the default. [Bug 253](https://github.com/shakenfist/shakenfist/issues/253).
* Load balancers were causing annoying log messages which have been demoted to debug level. [Bug 258](https://github.com/shakenfist/shakenfist/issues/258).

Changes between v0.2.2 and v0.2.3
=================================

v0.2.3 was released on 25 July 2020.

* Run CI tests multiple times to try and shake out timing errors. [Bug 191](https://github.com/shakenfist/shakenfist/issues/191).
* Be more flexible in what VDI video configurations we allow. [Bug 201](https://github.com/shakenfist/shakenfist/issues/201).
* Log power state changes as instance events. [Bug 203](https://github.com/shakenfist/shakenfist/issues/203).
* Handle bad video card choices more gracefully. [Bug 219](https://github.com/shakenfist/shakenfist/issues/219).

Changes between v0.2.1 and v0.2.2
=================================

v0.2.2 was released on 23 July 2020.

* Support for cascading delete of resources when you delete a namespace. [Bug 157](https://github.com/shakenfist/shakenfist/issues/157).
* Shaken Fist now tracks the power state of instances and exposes that via the REST API and command line client. We also kill stray instances which are left running after deletion. [Bug 173](https://github.com/shakenfist/shakenfist/issues/173); [bug 184](https://github.com/shakenfist/shakenfist/issues/184); [bug 192](https://github.com/shakenfist/shakenfist/issues/192); and [bug 197](https://github.com/shakenfist/shakenfist/issues/197).
* The API (and command line client) now display the version of Shaken Fist installed on each node. [Bug 175](https://github.com/shakenfist/shakenfist/issues/175).
* The network event cleanup code handles larger numbers of events needing to be cleaned on upgrade. [Bug 176](https://github.com/shakenfist/shakenfist/issues/176).
* Kernel Shared Memory is now configured by the deployer and used by Shaken Fist to over subscribe memory in cases where pages are successfully being shared. [Bug 177](https://github.com/shakenfist/shakenfist/issues/177); [deployer bug 28](https://github.com/shakenfist/deploy/issues/28); and [deployer bug 33](https://github.com/shakenfist/deploy/issues/33).
* Instance nodes are now correctly reported in cases where placement was forced. [Bug 179](https://github.com/shakenfist/shakenfist/issues/179).
* Instance scheduling is retried on a different node in cases where the churn rate is sufficient for the cached resource availability information to be inaccurate. [Bug 186](https://github.com/shakenfist/shakenfist/issues/186).
* A permissions denied error was corrected for shakenfist.json accesses in the comment line client. [Bug 187](https://github.com/shakenfist/shakenfist/issues/187).
* Incorrect database information for instances or network interfaces no longer crashes the start of new instances. [Bug 194](https://github.com/shakenfist/shakenfist/issues/194).
* Instance names must now be DNS safe. [Bug 200](https://github.com/shakenfist/shakenfist/issues/200).
* The deployer now checks that the configured MTU on the VXLAN mesh interface is sane. [Deployer bug 30](https://github.com/shakenfist/deploy/issues/30), and [deployer bug 32](https://github.com/shakenfist/deploy/issues/32).

Changes between v0.2.0 and v0.2.1
=================================

v0.2.1 was released on 16 July 2020.

* Fix crash in cleaner daemon when an etcd compaction fails. [Bug 152](https://github.com/shakenfist/shakenfist/issues/152).
* Fix HTTP 500 errors when malformed authorization headers are passed on API calls. [Bug 154](https://github.com/shakenfist/shakenfist/issues/154).
* Avoid starting instances on the network node if possible. [Bug 156](https://github.com/shakenfist/shakenfist/issues/156).
* Track and expose instance power states. Show instance state and power state in instance listings. [Bug 159](https://github.com/shakenfist/shakenfist/issues/159).
* Correct resource-in-use errors for specific IP requests. [Bug 162](https://github.com/shakenfist/shakenfist/issues/162).
* Network mesh event logging was too verbose. Only log additions and removals from the mesh. Also cleanup old mesh events on upgrade. [Bug 163](https://github.com/shakenfist/shakenfist/issues/163).
* Nodes now report what version of Shaken Fist they are running via etcd. [Bug 164](https://github.com/shakenfist/shakenfist/issues/164).