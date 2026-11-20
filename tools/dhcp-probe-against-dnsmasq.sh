#!/bin/bash
# Run shakenfist/util/dhcp_probe.py against a real dnsmasq, configured the
# way shakenfist/deploy/collection/roles/network/files/dhcp.tmpl configures
# one, on each distribution Shaken Fist supports.
#
# The probe's correctness rests on things no unit test can establish: how
# dnsmasq answers a DHCPINFORM (that it answers a client it has never heard
# of, that it answers at the request's source port rather than port 68, so
# the probe needs no privileged bind, that the answer comes from the address
# and port the request was sent to, since the probe ignores anything else,
# that it allocates no lease, and that a stopped dnsmasq does not answer),
# and that the request reaches it at all from inside the network namespace.
#
# That last one is why the namespace here is built with "ip netns add",
# exactly as sf-privexec builds it, rather than using the container's own
# namespace. A namespace from "ip netns add" has its loopback interface
# down, and the probe asks an address which is local to the namespace, so
# the request goes over loopback and a down loopback drops it silently.
# An earlier version of this rig ran in the container's namespace, where
# the loopback was already up, and so reported a probe which could not
# work on any real node as working on every distribution (issue 730).
#
# Usage: tools/dhcp-probe-against-dnsmasq.sh [image ...]
#
# Needs docker. Each container is privileged, because "ip netns add" mounts
# a filesystem; it is torn down at the end of the run.

set -u

NS=sfprobe
CFG=/cfg

if [ "${1:-}" == "--inside" ]; then
    apt-get -qq update > /dev/null
    apt-get -qq install -y dnsmasq-base python3 iproute2 > /dev/null 2>&1
    dnsmasq --version | head -1

    # The namespace and its inner veth, as bridged_vxlan_network.py makes
    # them: the router address sits on the inner end of a veth pair whose
    # outer end is on a bridge, and nothing touches the loopback.
    ip netns add ${NS}
    ip link add veth-o type veth peer name veth-i
    ip link set veth-i netns ${NS}
    ip link set veth-o up
    ip netns exec ${NS} ip link set veth-i up
    ip netns exec ${NS} ip addr add 10.10.0.1/24 dev veth-i

    mkdir -p ${CFG}
    echo '02:00:00:00:00:11,guest1,10.10.0.11' > ${CFG}/hosts
    cat > ${CFG}/dnsmasq.conf <<CONF
domain-needed
bogus-priv
no-hosts
no-resolv
filterwin2k
port=0
pid-file=${CFG}/pid
interface=veth-i
listen-address=10.10.0.1
domain=ns.example.com
local=/ns.example.com/
dhcp-leasefile=${CFG}/leases
dhcp-range=10.10.0.2,static,255.255.255.0,10.10.0.255,1h
dhcp-option=1,255.255.255.0
dhcp-option=15,ns.example.com
dhcp-option=26,1450
dhcp-hostsfile=${CFG}/hosts
CONF
    ip netns exec ${NS} dnsmasq --conf-file=${CFG}/dnsmasq.conf
    sleep 1

    probe () {
        ip netns exec ${NS} python3 /probe/dhcp_probe.py 10.10.0.1 10.10.0.1 2
        echo $?
    }

    failed=0
    check () {
        local want="$1" got="$2" what="$3"
        if [ "${got}" != "${want}" ]; then
            echo "FAIL: ${what} (exit ${got}, wanted ${want})"
            failed=1
        else
            echo "ok: ${what}"
        fi
    }

    # The loopback as "ip netns add" leaves it. The probe cannot reach
    # dnsmasq, and must say so rather than report silence: exit 3 is read
    # as a wedged dnsmasq and rebuilds the network. Exit 4 is the probe
    # naming the down loopback, which the network node then repairs.
    echo "--- loopback as the namespace was born: $(
        ip netns exec ${NS} ip link show lo | head -1)"
    check 4 "$(probe)" \
        "a down loopback is reported as such, not as silence"

    # What privexec_util.create_network_namespace() now does.
    ip netns exec ${NS} ip link set lo up
    check 0 "$(probe)" "a healthy dnsmasq answered at the probe's source port"

    if [ -s ${CFG}/leases ]; then
        echo "FAIL: the probe allocated a lease:"
        cat ${CFG}/leases
        failed=1
    else
        echo "ok: no lease was allocated"
    fi

    # A dnsmasq which is stopped but still present: state T, which
    # is_running() reports as healthy.
    kill -STOP "$(cat ${CFG}/pid)"
    check 3 "$(probe)" "a stopped dnsmasq was reported as no answer"

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
    if ! timeout 600 docker run --rm --privileged \
            -v "${here}/dhcp-probe-against-dnsmasq.sh:/rig.sh:ro" \
            -v "${probe_dir}/dhcp_probe.py:/probe/dhcp_probe.py:ro" \
            "${image}" /bin/bash /rig.sh --inside; then
        overall=1
    fi
done
exit ${overall}
