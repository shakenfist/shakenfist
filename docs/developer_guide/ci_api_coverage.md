# CI API Coverage

This document tracks the CI coverage for each of our public APIs. The intent to make it clear what is "sufficiently tested", and what needs further effort. This list is manually updated, so we'll need to show discipline in ensuring that we update it as we change APIs or CI.

For this document we use the python API client as a model of what to cover, as its simpler to extract a list of calls from than the API implementation itself. We list whether we have CI which calls the API directly, CI which uses the python command line client to call the API, or both. The gold standard is both.

## get_instances

Whilst being implied by every test tearDown(), this call is explicitly used in:

* TestCirros.test_cirros_boot_no_network
* TestCirros.test_cirros_boot_network
* TestPlacement.test_local_placement_works
* TestPlacement.test_remote_placement_works
* TestSystemNamespace.test_system_namespace
* TestUbuntu.test_ubuntu_pings

## delete_all_instances

*Not tested.*

## get_instance

All callers of await_instance_event call get_instance(). However, these more explicit tests exist as well:

* TestCacheImage.test_instance_invalid_image

## get_instance_interfaces

* TestMultipleNics.test_simple
* TestNetworking.test_virtual_networks_are_separate
* TestNetworking.test_overlapping_virtual_networks_are_separate
* TestNetworking.test_single_virtal_networks_work
* TestNetworking.test_specific_ip_request
* TestPlacement.test_local_placement_works
* TestPlacement.test_remote_placement_works
* TestStateChanges.test_lifecycle_events
* TestUbuntu.test_ubuntu_pings

## get_instance_metadata

* TestInstanceMetadata.test_simple

## set_instance_metadata_item

* TestInstanceMetadata.test_simple

## delete_instance_metadata_item

*Not tested.*

## create_instance

Tested extensively in most other tests, so tests are not listed here.

## snapshot_instance

* TestSnapshots.test_single_disk_snapshots
* TestSnapshots.test_multiple_disk_snapshots

## get_instance_snapshots

* TestSnapshots.test_single_disk_snapshots
* TestSnapshots.test_multiple_disk_snapshots

## reboot_instance

* TestStateChanges.test_lifecycle_events

## power_off_instance

* TestStateChanges.test_lifecycle_events

## power_on_instance

* TestStateChanges.test_lifecycle_events

## pause_instance

* TestStateChanges.test_lifecycle_events

## unpause_instance

* TestStateChanges.test_lifecycle_events

## delete_instance

Whilst being implied by every test tearDown(), this call is explicitly used in:

* TestCirros.test_cirros_boot_no_network
* TestCirros.test_cirros_boot_network
* TestPlacement.test_local_placement_works
* TestPlacement.test_remote_placement_works
* TestSnapshots.test_single_disk_snapshots
* TestSnapshots.test_multiple_disk_snapshots
* TestSystemNamespace.test_system_namespace
* TestUbuntu.test_ubuntu_pings

## get_instance_events

All callers of await_instance_event call get_instance(). *However, more testing of this method is required.*

## cache_image

* TestImages.test_cache_image

## get_images

(Formerly get_image_meta, old name to be removed in 0.5).

* TestImages.test_cache_image

## get_image_events

*Not tested.*

## get_networks

Whilst being implied by every test tearDown(), this call is explicitly used in:

* TestSystemNamespace.test_system_namespace

## get_network

*Not tested.*

## delete_network

Whilst being implied by every test tearDown(), this call is explicitly used in:

* TestSystemNamespace.test_system_namespace

## delete_all_networks

*Not tested.*

## get_network_events

*Not tested.*

## allocate_network

Tested extensively in most other tests, so tests are not listed here.

## get_network_interfaces

*Not tested.*

## get_network_metadata

*Not tested.*

## set_network_metadata_item

*Not tested.*

## delete_network_metadata_item

*Not tested.*

## get_nodes

*Not tested.*

## get_interface

*Not tested.*

## float_interface

*Not tested.*

## defloat_interface

*Not tested.*

## get_console_data

* TestConsoleLog.test_console_log

## get_namespaces

As well as being tested as a side effect of most other tests, there is the following explicit test:

* TestAuth.test_namespaces

## create_namespace

As well as being tested as a side effect of most other tests, there is the following explicit test:

* TestAuth.test_namespaces

## delete_namespace

As well as being tested as a side effect of most other tests, there is the following explicit test:

* TestAuth.test_namespaces

## get_namespace_keynames

*Not tested.*

## add_namespace_key

* TestAuth.test_namespaces

## delete_namespace_key

* TestAuth.test_namespaces

## get_namespace_metadata

*Not tested.*

## set_namespace_metadata_item

*Not tested.*

## delete_namespace_metadata_item

*Not tested.*

## get_existing_locks

*Not tested.*

## ping

Tested as a side effect of many other tests, but no explicit test.