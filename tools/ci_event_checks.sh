#!/bin/bash -e

# Intended to be run on the primary via run_remote.
failures=0

revisions=$(etcdctl get / --write-out=json | jq .header.revision)
echo "Number of etcd revisions in this test run: ${revisions}"
if [ ${revisions} -gt 175000 ]; then
    echo "FAILURE: Too many etcd writes!"
    failures=1
fi
echo

if [ $(echo ${1} | egrep -c "^0.[1234567]") -eq 0 ]; then

    export SHAKENFIST_ETCD_HOST=10.0.0.10
    /srv/shakenfist/venv/bin/python3 tools/event_statistics.py
else
    echo "Skipping event statistics checks, version too old."
fi
echo

acquired_locks=$(grep -c /var/log/syslog "Acquired lock")
echo "Number of locks acquired: ${acquired_locks}"
echo
echo "Top 20 locks by acquisition:"

IFS="\n"
for lock in $(grep "Acquired lock" /var/log/syslog | \
        sed -e 's/.*key=//' -e 's/;.*//' | \
        sort | uniq -c | sort -n | tail -20); do
    echo "${lock}"
    count=$(echo ${lock} | sed 's|/sflocks||')
    if [ ${count} -gt 1500 ]; then
        echo "   ... more than threshold of 1,500"
        failures=1
    fi
done
echo

if [ $failures -gt 0 ]; then
    echo "...failures detected."
    exit 1
fi