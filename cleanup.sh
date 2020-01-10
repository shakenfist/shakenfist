#!/bin/bash

for c in `docker ps | grep dhcp | cut -f 1 -d " "`
do
    docker rm -f $c
done

for vm in `virsh list | tr -s " " | grep running | cut -f 3 -d " "`
do
    virsh destroy $vm
done

rm -rf /srv/shakenfist/instances
rm -rf /srv/shakenfist/dhcp

ip link del dhcpd-$1
ip link del br-vxlan-$1
ip link del vxlan-$1
