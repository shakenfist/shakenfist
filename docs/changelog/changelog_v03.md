The big ideas of v0.3
=====================

v0.3 is a re-write of portions of v0.2 that we felt were holding Shaken Fist back. The two most obvious examples are that slow operations would often timeout because of our HTTP worker model, and locking simply didn't work at all. Some more detail on those:

* All operations in v0.2 are handled by the REST API HTTP workers (gunicorn). So, if you ask us to launch an instance, we use the HTTP worker to do that thing. That works great, unless the operation is going to take more time than the HTTP worker is allowed to run for. This is pretty easy to achieve if the image you're fetching is large. Instead, we now move to a model where the HTTP worker creates a queued job, and then polls its state for a small period of time before returning. That means if things are quick you get the same API behaviour as before, but for slow operations you'll be told that the job is incomplete but still executing.

* We also realised somewhere late in v0.2's life that the etcd library we were using was pretty buggy. Locking simply didn't work some of the time. Additionally, our locking code was pretty ad hoc and sometimes we would get the names of the locks wrong because they were just strings. This has now been completely re-written, but that has shaken out a number of bugs that are surfaced by locking actually working now. We have worked through those bugs and sought to resolve them.

Changes between v0.3.4 and v0.3.5 (hotfix)
==========================================

v0.3.5 was released on 17 January 2021.

* Pin Flask-JWT-Extended to resolve CI breakage.

Changes between v0.3.3 and v0.3.4 (hotfix)
==========================================

v0.3.4 was released on 15 January 2021.

* Package libvirt and DHCP templates in the python package, so that the modern installer can install the correct version of these templates when installing older releases.

Changes between v0.3.2 and v0.3.3
=================================

v0.3.3 was released on 13 November 2020.

* Remove incorrect warnings for extra VLANs. shakenfist#496
* Fix logging for queue workers. shakenfist#498
* Resolve network delete collisions and races. shakenfist#500, shakenfist#504
* Resolve resources daemon issue casued by race with instance deletion. shakenfist#507
* Image fetch should retry after bad checksum. shakenfist#509
* Add an option to delay deletion of failed CI jobs. shakenfist#511
* Enable full state change CI. shakenfist#514
* Fix etcd connection error on compaction. shakenfist#516
* CI fails when the Ubuntu mirror is slow. shakenfist#518
* Nightly large CI failing because of logic errors in grep. shakenfist#522
* Fix numeric conversion error in disk sizes. shakenfist#526
* Ensure requested IPs are within the ipblock of the virtual network. shakenfist#533, shakenfist#536, shakenfist#538

Changes between v0.3.1 and v0.3.2
=================================

v0.3.2 was released on 29 October 2020.

* Networking shake down as we work towards CI reliably passing. shakenfist#435, shakenfist#438, shakenfist#469, shakenfist#477
* Images improvements including moving towards cluster wide image management. shakenfist#440, shakenfist#442, shakenfist#451, shakenfist#453, shakenfist#460, shakenfist#465, shakenfist#472, shakenfist#481
* Admins can now list locks that Shaken Fist is holding and locks include a description of the operation being undertaken. Reduce the number of locks we hold to reduce etcd load. shakenfist#444, shakenfist#449, shakenfist#455, shakenfist#463
* Cleanup trigger daemon defunct processes. shakenfist#446
* Deployments were broken if you specified a local prometheus snippet. shakenfist#456
* Console and VDI ports were being checked for availability on the API node not the hypervisor node. shakenfist#487

Changes between v0.3.0 and v0.3.1
=================================

v0.3.1 was released on 21 October 2020.

* Reject power on while powering off. shakenfist#394
* Always use locally administered MAC addresses. shakenfist#409
* Fix unintentionally ignored exceptions. shakenfist#410
* Image downloads should verify checksums where possible. shakenfist#412
* Improved CI reliability. shakenfist#416, shakenfist3418, shakenfist#419, shakenfist#420, shakenfist#426, shakenfist#428
