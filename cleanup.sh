#!/bin/bash

. /etc/sf/sfrc

for vm in `virsh list --all | tr -s " " | grep "sf:" | cut -f 3 -d " "`
do
    virsh destroy $vm
    virsh undefine $vm
done

rm -rf /srv/shakenfist/instances
rm -rf /srv/shakenfist/snapshots
rm -rf /srv/shakenfist/dhcp
rm -rf /srv/shakenfist/image_cache/*.qcow2.*G

ip link del phy-br-ens4
for i in `seq 10`
do
  ip link del br-vxlan-$i
  ip link del vxlan-$i
  ip link del phy-$i-o
  ip link del veth-$i-o
done

for ns in `ls /var/run/netns`
do
  ip netns del $ns
done

exit 0