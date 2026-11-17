#!/bin/bash
# Run shakenfist/util/dhcp_probe.py against a real dnsmasq, configured the
# way shakenfist/deploy/collection/roles/network/files/dhcp.tmpl configures
# one, on each distribution Shaken Fist supports.
#
# The probe's correctness rests on how dnsmasq answers a DHCPINFORM, which
# no unit test can establish: that it answers a client it has never heard of,
# that it answers at the request's source port rather than port 68 (so the
# probe needs no privileged bind), that it allocates no lease, and that a
# stopped dnsmasq does not answer. This checks all four.
#
# Usage: tools/dhcp-probe-against-dnsmasq.sh [image ...]
#
# Needs docker. Each image gets a container with NET_ADMIN, so the test
# interface lives in the container's own network namespace.

set -u

if [ "${1:-}" == "--inside" ]; then
    apt-get -qq update > /dev/null
    apt-get -qq install -y dnsmasq-base python3 iproute2 > /dev/null 2>&1
    dnsmasq --version | head -1

    ip link add veth-i type veth peer name veth-o
    ip addr add 10.10.0.1/24 dev veth-i
    ip link set veth-i up
    ip link set veth-o up

    mkdir -p /cfg
    echo '02:00:00:00:00:11,guest1,10.10.0.11' > /cfg/hosts
    cat > /cfg/dnsmasq.conf <<CONF
domain-needed
bogus-priv
no-hosts
no-resolv
filterwin2k
port=0
pid-file=/cfg/pid
interface=veth-i
listen-address=10.10.0.1
domain=ns.example.com
local=/ns.example.com/
dhcp-leasefile=/cfg/leases
dhcp-range=10.10.0.2,static,255.255.255.0,10.10.0.255,1h
dhcp-option=1,255.255.255.0
dhcp-option=15,ns.example.com
dhcp-option=26,1450
dhcp-hostsfile=/cfg/hosts
CONF
    dnsmasq --conf-file=/cfg/dnsmasq.conf
    sleep 1

    failed=0
    cd /probe
    python3 dhcp_probe.py 10.10.0.1 10.10.0.1 2
    rc=$?
    if [ ${rc} -ne 0 ]; then
        echo "FAIL: a healthy dnsmasq did not answer (exit ${rc})"
        failed=1
    else
        echo "ok: a healthy dnsmasq answered at the probe's source port"
    fi

    if [ -s /cfg/leases ]; then
        echo "FAIL: the probe allocated a lease:"
        cat /cfg/leases
        failed=1
    else
        echo "ok: no lease was allocated"
    fi

    kill -STOP "$(cat /cfg/pid)"
    python3 dhcp_probe.py 10.10.0.1 10.10.0.1 2
    rc=$?
    if [ ${rc} -ne 3 ]; then
        echo "FAIL: a stopped dnsmasq was not reported as no answer (exit ${rc})"
        failed=1
    else
        echo "ok: a stopped dnsmasq was reported as no answer"
    fi
    exit ${failed}
fi

images=("$@")
if [ ${#images[@]} -eq 0 ]; then
    images=(debian:bookworm ubuntu:24.04 debian:trixie)
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
probe_dir="$(cd "${here}/../shakenfist/util" && pwd)"

overall=0
for image in "${images[@]}"; do
    echo "=== ${image}"
    if ! timeout 600 docker run --rm --cap-add NET_ADMIN \
            -v "${here}/dhcp-probe-against-dnsmasq.sh:/rig.sh:ro" \
            -v "${probe_dir}/dhcp_probe.py:/probe/dhcp_probe.py:ro" \
            "${image}" /bin/bash /rig.sh --inside; then
        overall=1
    fi
done
exit ${overall}
