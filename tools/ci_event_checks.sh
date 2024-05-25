#!/bin/bash -e

# Intended to be run on the primary via run_remote.
failures=0

revisions=$(etcdctl get / --write-out=json | jq .header.revision)
echo "Number of etcd revisions in this test run: ${revisions}"
if [ ${revisions} -gt 175000 ]; then
    echo "FAILURE: Too many etcd writes!"
    failures=1
fi

# We only check etcd statistics for version 0.8.0 onwards
version=$(sf-client --simple node show primary | grep release | cut -f 2 -d ":")
echo "Detected version ${version}"

if [ $(echo ${version} | egrep -c "^0.[1234567]") -eq 0 ]; then
    echo
    export SHAKENFIST_ETCD_HOST=10.0.0.10
    /srv/shakenfist/venv/bin/python3 tools/event_statistics.py
    echo

    if [ $failures -gt 0 ]; then
        echo "...failures detected."
        exit 1
    fi
else
    echo "Skipping event statistics checks, version too old."
fi