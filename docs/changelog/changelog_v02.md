Changes between v0.2.7 and v0.2.8
=================================

v0.2.8 has not yet been released.

* Allow the setting of numeric configuration values. shakenfist#324
* Backing file information must be provided with modern libvirts. shakenfist#326


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

* API requests without a JSON payload fail when made to a non-network node. shakenfist#261
* Resolve error during instance start. shakenfist#263
* Resolve a crash in the network monitor. shakenfist#264

Changes between v0.2.3 and v0.2.4
=================================

v0.2.4 was released on 2 August 2020.

* Remove stray networks when detected. shakenfist#161
* Display video details of an instance in sf-client. shakenfist#216
* The login prompt trigger is now more reliable. shakenfist#223
* Rapid creates from terraform can crash Shaken Fist with a race condition. shakenfist#225
* Instance deletes could leave internal IP addresses allocated to deleted instances. shakenfist#227
* sf-client now displays network card model. shakenfist#228
* Missing node metrics could cause a scheduler crash. shakenfist#236
* A missing import caused a scheduler crash. shakenfist#236
* You can now list the instances on a network with sf-client. shakenfist#240
* Disabling DHCP for networks did not work correctly. shakenfist#245
* Ubuntu 18.04's cloud-init would issue warnings about network interface types. shakenfist#250
* Network interfaces were sometimes leaked. shakenfist#252
* sf-client would sometimes crash if the disk bus was the default. shakenfist#253
* Load balancers were causing annoying log messages which have been demoted to debug level. shakenfist#258

Changes between v0.2.2 and v0.2.3
=================================

v0.2.3 was released on 25 July 2020.

* Run CI tests multiple times to try and shake out timing errors. shakenfist#191
* Be more flexible in what VDI video configurations we allow. shakenfist#201
* Log power state changes as instance events. shakenfist#203
* Handle bad video card choices more gracefully. shakenfist#219

Changes between v0.2.1 and v0.2.2
=================================

v0.2.2 was released on 23 July 2020.

* Support for cascading delete of resources when you delete a namespace. shakenfist#157
* Shaken Fist now tracks the power state of instances and exposes that via the REST API and command line client. We also kill stray instances which are left running after deletion. shakenfist#197
* The API (and command line client) now display the version of Shaken Fist installed on each node. shakenfist#175
* The network event cleanup code handles larger numbers of events needing to be cleaned on upgrade. shakenfist#176
* Kernel Shared Memory is now configured by the deployer and used by Shaken Fist to over subscribe memory in cases where pages are successfully being shared. shakenfist#177, deploy#28 and deploy#33.
* Instance nodes are now correctly reported in cases where placement was forced. shakenfist#179
* Instance scheduling is retried on a different node in cases where the churn rate is sufficient for the cached resource availability information to be inaccurate. shakenfist#186
* A permissions denied error was corrected for shakenfist.json accesses in the comment line client. shakenfist#187
* Incorrect database information for instances or network interfaces no longer crashes the start of new instances. shakenfist#194
* Instance names must now be DNS safe. shakenfist#200
* The deployer now checks that the configured MTU on the VXLAN mesh interface is sane. deploy#30, and deploy#32.

Changes between v0.2.0 and v0.2.1
=================================

v0.2.1 was released on 16 July 2020.

* Fix crash in cleaner daemon when an etcd compaction fails. shakenfist#152
* Fix HTTP 500 errors when malformed authorization headers are passed on API calls. shakenfist#154
* Avoid starting instances on the network node if possible. shakenfist#156
* Track and expose instance power states. Show instance state and power state in instance listings. shakenfist#159
* Correct resource-in-use errors for specific IP requests. shakenfist#162
* Network mesh event logging was too verbose. Only log additions and removals from the mesh. Also cleanup old mesh events on upgrade. shakenfist#163
* Nodes now report what version of Shaken Fist they are running via etcd. shakenfist#164