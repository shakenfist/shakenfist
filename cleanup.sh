#!/bin/bash

. /etc/sf/sfrc

for c in `docker ps | grep dhcpd | cut -f 1 -d " "`
do
    docker rm -f $c
done

for vm in `virsh list --all | tr -s " " | grep "sf:" | cut -f 3 -d " "`
do
    virsh destroy $vm
    virsh undefine $vm
done

rm -rf /srv/shakenfist/instances
rm -rf /srv/shakenfist/snapshots
rm -rf /srv/shakenfist/dhcp
rm -rf /srv/shakenfist/image_cache/*.qcow2.*G

for table in network_interfaces instances networks nodes
do
  echo "Clearing table $table"
  docker exec -it sfdb mysql --user sf --password=$SHAKENFIST_DB_PASSWORD sf \
      -e "delete from $table;"
done

for i in `seq 10`
do
  ip link del dhcpd-$i
  ip link del br-vxlan-$i
  ip link del vxlan-$i
done

exit 0