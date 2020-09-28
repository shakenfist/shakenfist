---
title: Overview
---
# Networking Overview

Shaken Fist networking is complicated. Its actually less complicated that OpenStack Neutron networking, and its about as simple as we can get away with, but in order to allow virtual networks to use overlapping network ranges we are forced to do some vaguely complicated things with network namespaces.  
This document attempts to incrementally describe how Shaken Fist networking works, so that I can remember later.

## VXLAN

Shaken Fist networking is based on a VXLAN mesh. VXLAN is like a successor to VLANs, except that you can have 1.6 million virtual networks, it doesn't use an IP header field to divide the networks up, and it is transported inside UDP packets between the members of the mesh.
 
Normally VXLAN meshes are implemented using multicast UDP, but that doesn't work in public clouds where Shaken Fist was born, so we instead use unicast meshes that we lovingly hand maintain.

!!! note
    The virtual mesh manages itself and changes state over time. If your mesh entries do not look like mine, it does not nessesarily indicate that your networking is broken. This is a dynamic environment!

## Our worked examples

For this document, we will assume there are three Shaken Fist nodes, named **sf-1**, **sf-2**, and **sf-3**. Its a total coincidence that this is the default size for the installer ansible at the time of writing and the exact size of all of the production clusters we are aware of.  
**sf-1** is configured as the "network node", which is just a hypervisor like every other node, except that it is also where packets to and from the virtual networks route in and out of the mesh.

* [First, off let's start with a simple network with one instance and no DHCP or NAT](single_instance.md).