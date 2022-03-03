#!/bin/bash

# Don't run this on a cluster with real workloads, you'd be sad.
# This script assumes the cluster is idle and empty at the start.

echo "$(date) Run started"
sleep 900
echo "$(date) Creation started"

for i in $(seq 0 250); do
    sf-client network create deleteme 10.0.0.0/24 > /dev/null
    echo -n "."
    sleep 10
done

echo
echo "$(date) Creation finished"
sleep 900
echo "$(date) Deletion started"

for uuid in $(sf-client --simple network list | grep deleteme | cut -f 1 -d ","); do
    sf-client network delete $uuid > /dev/null
    echo -n "."
done

echo
echo "$(date) Deletion finished"
echo
sleep 900
echo "$(date) Run finished"
