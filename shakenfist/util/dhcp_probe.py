"""Ask a dnsmasq whether it is still answering DHCP.

``is_running()`` on a managed executable is a liveness check: it reads
the pid file and asks whether that process exists. A dnsmasq which is
alive but wedged passes it, and a network whose guests cannot get a
lease looks healthy (issue 730).

The probe here is a DHCPINFORM. Three properties make it the right
request to send, all of them measured against a real dnsmasq
configured the way Shaken Fist configures one (``dhcp-range=...,static``
with a ``dhcp-hostsfile``), rather than assumed:

* It is answered for a MAC address the server has never heard of. A
  DHCPDISCOVER is not -- in ``static`` mode dnsmasq ignores a client
  which is not in its hosts file -- so a DISCOVER probe would have
  reported every healthy network as broken.
* It allocates nothing. The reply carries ``yiaddr`` 0.0.0.0 and the
  lease file is untouched, so the probe can run on every maintenance
  pass without consuming addresses or disturbing a real client.
* A wedged dnsmasq fails it. With the process stopped but still
  present (state ``T``, which ``is_running()`` reports as healthy) the
  probe times out.

A fourth property was measured because a review doubted it: dnsmasq
answers a DHCPINFORM at the source address *and port* it came from,
not at the client port (68). ``dhcp_packet()`` in dnsmasq's
``src/dhcp.c`` says so -- "If we're replying to a DHCPINFORM, trust
the source address always" -- so the probe needs no privileged bind.
Measured on dnsmasq 2.90 (Debian 12) and 2.91 (Ubuntu 24.04, Debian
13). ``tools/dhcp-probe-against-dnsmasq.sh`` repeats the measurement,
and the smoke CI suite probes the real dnsmasq on its network node.

This module is executed inside the network's namespace, because the
server listens on an address which only exists in there. It is run by
the interpreter of the process asking (``sys.executable``), never a
bare ``python3``: sf-privexec runs commands with a system ``PATH``, on
which ``python3`` is an interpreter with no shakenfist installed, and
the import failure that produced read as "no answer" for every network
on every pass. It deliberately imports nothing from shakenfist: it runs
as a short-lived subprocess, and the cost of importing the package
into one on every maintenance pass is the kind of thing the database
load work spent a plan removing.

The exit status separates "dnsmasq did not answer" from "the probe
could not be run", because only the first is a reason to rebuild a
network. Neither may be 1: that is what Python itself exits with for
an import failure or an uncaught exception, which is exactly the
failure that has to stay distinguishable from silence.
"""

import random
import socket
import struct
import sys
import time


# The BOOTP magic cookie which introduces the DHCP options (RFC 2132).
MAGIC_COOKIE = b'\x63\x82\x53\x63'

# DHCP message types we send and expect (RFC 2131 table 2).
DHCPACK = 5
DHCPINFORM = 8

# Option codes, and the end-of-options marker.
OPTION_MESSAGE_TYPE = 53
OPTION_PARAMETER_REQUEST_LIST = 55
OPTION_PAD = 0
OPTION_END = 255

# A DHCP packet is 236 bytes of fixed BOOTP fields, then the cookie,
# then options.
BOOTP_HEADER_BYTES = 236

DHCP_SERVER_PORT = 67

# The MAC the probe claims. Locally administered (the 0x02 bit) and not
# a real card, so it cannot collide with a guest's address in the hosts
# file. Nothing is allocated to it either way.
PROBE_MAC = b'\x02\x00\x5f\x70\x72\x62'

DEFAULT_TIMEOUT = 2.0

# Exit statuses, see the module docstring. EXIT_COULD_NOT_PROBE is what
# main() returns for anything it can name; anything else non-zero (1 for
# an import failure, say) means the same thing to the caller.
EXIT_ANSWERED = 0
EXIT_COULD_NOT_PROBE = 2
EXIT_NO_ANSWER = 3


def build_inform(client_address, mac=PROBE_MAC, xid=None):
    """A DHCPINFORM asking for the subnet mask and the domain name."""
    if xid is None:
        xid = random.getrandbits(32)

    packet = struct.pack(
        '!BBBBIHH4s4s4s4s16s64s128s',
        1,                                  # op: BOOTREQUEST
        1,                                  # htype: ethernet
        6,                                  # hlen
        0,                                  # hops
        xid,
        0,                                  # secs
        0,                                  # flags
        socket.inet_aton(client_address),   # ciaddr, required by INFORM
        b'\x00' * 4,                        # yiaddr
        b'\x00' * 4,                        # siaddr
        b'\x00' * 4,                        # giaddr
        mac + b'\x00' * 10,                 # chaddr
        b'\x00' * 64,                       # sname
        b'\x00' * 128)                      # file

    options = bytes([OPTION_MESSAGE_TYPE, 1, DHCPINFORM])
    options += bytes([OPTION_PARAMETER_REQUEST_LIST, 2, 1, 15])
    options += bytes([OPTION_END])
    return xid, packet + MAGIC_COOKIE + options


def parse_message_type(packet):
    """The DHCP message type of a reply, or None if it has none."""
    if len(packet) < BOOTP_HEADER_BYTES + len(MAGIC_COOKIE):
        return None
    if packet[BOOTP_HEADER_BYTES:
              BOOTP_HEADER_BYTES + len(MAGIC_COOKIE)] != MAGIC_COOKIE:
        return None

    options = packet[BOOTP_HEADER_BYTES + len(MAGIC_COOKIE):]
    offset = 0
    while offset < len(options):
        code = options[offset]
        if code == OPTION_END:
            return None
        if code == OPTION_PAD:
            offset += 1
            continue
        if offset + 1 >= len(options):
            return None
        length = options[offset + 1]
        if code == OPTION_MESSAGE_TYPE and length >= 1:
            return options[offset + 2]
        offset += 2 + length
    return None


def parse_transaction_id(packet):
    if len(packet) < 8:
        return None
    return struct.unpack('!I', packet[4:8])[0]


def probe(server_address, client_address, timeout=DEFAULT_TIMEOUT,
          sock=None, server_port=DHCP_SERVER_PORT):
    """True if ``server_address`` answered a DHCPINFORM with a DHCPACK.

    ``client_address`` is the address the INFORM claims, which must sit
    inside the server's configured range for it to be answered -- the
    network's own router address does.

    False means nothing answered within ``timeout`` seconds, in total
    rather than per packet read. An error which says nothing about the
    server (a socket which cannot be sent from, say) is raised rather
    than reported as silence, so that the caller can tell the two
    apart.
    """
    xid, packet = build_inform(client_address)

    owned = sock is None
    if owned:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        deadline = time.monotonic() + timeout
        sock.sendto(packet, (server_address, server_port))

        # A reply whose transaction id is not ours belongs to a real
        # client's exchange, which we may see because the server
        # broadcasts. Keep reading until the deadline rather than
        # reporting somebody else's packet as our answer -- or, worse,
        # reporting a healthy server as wedged because a guest was
        # mid-handshake. The deadline is overall: a socket timeout
        # applies afresh to each read, so on a busy network every
        # foreign packet would otherwise restart the wait.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            sock.settimeout(remaining)
            try:
                reply, _ = sock.recvfrom(2048)
            except socket.timeout:
                return False
            except ConnectionRefusedError:
                # Nothing is listening on the server port: an answer
                # about the server, and the answer is no.
                return False
            if parse_transaction_id(reply) != xid:
                continue
            return parse_message_type(reply) == DHCPACK
    finally:
        if owned:
            sock.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print('usage: dhcp_probe <server address> <client address> '
              '[timeout]', file=sys.stderr)
        return EXIT_COULD_NOT_PROBE

    try:
        timeout = float(argv[2]) if len(argv) > 2 else DEFAULT_TIMEOUT
        answered = probe(argv[0], argv[1], timeout=timeout)
    except Exception as e:
        print(f'dhcp_probe: could not probe: {e.__class__.__name__}: {e}',
              file=sys.stderr)
        return EXIT_COULD_NOT_PROBE
    return EXIT_ANSWERED if answered else EXIT_NO_ANSWER


if __name__ == '__main__':
    sys.exit(main())
