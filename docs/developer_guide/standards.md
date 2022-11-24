Concepts and Standards
===
# Ensuring a Common Language within the code base

This document records the standards and common language used within the Shaken Fist software system.

It should also record why the choice was made.

(This is actually just notes to save our future selves from tripping over the same problems.)

## etcd keys

Key names in `etcd` should be in the singular, for example `/sf/namespace/` not `/sf/namespaces/` note that this is different than the REST API.

## Memory

Memory is measured in MiB in Shaken Fist. All references to memory size are stored and transmitted in MiB: Gigabytes can be too big if you want a lot of small machines. Kilobytes is just too many numbers to type. The ```libvirt``` API measures memory in KiB. Therefore, interactions with the library need to be careful to convert from MiB to KiB.
