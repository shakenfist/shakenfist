#!/bin/bash

failures=0

echo
echo "Running log checks for branch ${1} and job ${2}."
echo
etcd_conns=`grep -c "Building new etcd connection" /var/log/syslog || true`
echo "This CI run created $etcd_conns etcd connections."
if [ $etcd_conns -gt 5000 ]; then
echo "FAILURE: Too many etcd clients!"
failures=1
fi

echo
sigterms=`grep -c "Sent SIGTERM to " /var/log/syslog || true`
echo "This CI run sent $sigterms SIGTERM signals while shutting down."
if [ $sigterms -gt 50 ]; then
echo "FAILURE: Too many SIGTERMs sent!"
failures=1
fi

# NOTE(mikal): online upgrades are forbidden in these fresh install
# tests.
echo
FORBIDDEN=("Traceback (most recent call last):"
            "ERROR sf"
            "ERROR gunicorn"
            " died"
            "Extra vxlan present"
            "Fork support is only compatible with the epoll1 and poll polling strategies"
            "not using configured address"
            "Dumping thread traces"
            "because it is leased to"
            "not committing online upgrade"
            "Received a GOAWAY with error code ENHANCE_YOUR_CALM"
            "ConnectionFailedError"
            "invalid JWT in Authorization header"
            "Libvirt Error: XML error"
            "Cleaning up leaked IPAM"
            "Cleaning up leaked vxlan"
            "invalid salt"
            "unable to execute QEMU command")

if [ $(echo "${1}" | grep -c "v0.7" || true) -lt 1 ]; then
    echo "INFO: Including forbidden strings for v0.8 onwards."
    FORBIDDEN+=("Waiting to acquire lock")
    FORBIDDEN+=('apparmor="DENIED"')
    FORBIDDEN+=("Ignoring malformed cache entry")
    FORBIDDEN+=("WORKER TIMEOUT")
fi

if [ $(echo "${2}" | grep -c "upgrade" || true) -lt 1 ]; then
    echo "INFO: Including forbidden strings for non-upgrade jobs."
    FORBIDDEN+=("online upgrade")
fi

IFS=""
for forbid in ${FORBIDDEN[*]}
do
    echo "    Check for >>${forbid}<< in logs."
    count=$(grep -c -i "$forbid" /var/log/syslog || true)
    if [ ${count} -gt 0 ]
    then
        echo "FAILURE: Forbidden string found in logs ${count} times."
        failures=1
    fi
done

# Forbidden once stable, which we currently define as after the first 1,000
# lines of the syslog file.
FORBIDDEN_ONCE_STABLE=("Failed to send event with gRPC")
IFS=""
for forbid in ${FORBIDDEN_ONCE_STABLE[*]}
do
    echo "    Check for >>${forbid}<< in stable logs."
    count=$(tail -n +1000 /var/log/syslog | grep -c -i "$forbid" || true)
    if [ ${count} -gt 0 ]
    then
        echo "FAILURE: Forbidden once stable string found ${count} times."
        failures=1
    fi
done

echo
if [ $failures -gt 0 ]; then
    echo "...failures detected."
    exit 1
fi