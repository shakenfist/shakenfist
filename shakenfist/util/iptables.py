# Copyright 2026 Michael Still and contributors

"""The iptables rules a NAT providing network needs in its namespace.

These live here rather than beside the daemon which installs them
because they have two users, not one. sf-privexec writes them when a
network is created on the network node, and ``Network.is_okay()`` asks
whether the hairpin rule is still there -- which is how a cluster that
upgraded while all of its networks were healthy ever acquires it.
Written out in both places they would drift; derived from here they
cannot.

A rule is the chain name followed by the match and target arguments
exactly as iptables writes them after ``-A``, which is also exactly
what it wants after ``-C``. So the same list serves both the install
and the audit.

This module deliberately imports nothing. sf-privexec runs as root and
is kept as small as we can manage.
"""


def veth_names(vxid: int) -> tuple[str, str]:
    """The inside ends of a network's egress and virtual network veths.

    Interface names are limited to 15 characters on Linux, which is why
    the vxid is rendered in hex here (and in ``Network.subst_dict``,
    which is where these same names come from on the object side).
    """
    return 'egr-%06x-i' % vxid, 'veth-%06x-i' % vxid


def stale_nat_rules(vxid: int) -> list[tuple[str, list[str]]]:
    """Rules an older Shaken Fist installed here which are now wrong.

    The return-direction FORWARD rule used to name the literal string
    ``vx_veth_inner`` rather than the interface the variable of that
    name held. ``network_nat_rules`` fixes that, but a ``-C`` for the
    corrected rule cannot match the broken one, so on an existing
    cluster the fixed rule is appended beside a wrong rule which stays
    forever. It is harmless -- the namespace's FORWARD policy is
    ACCEPT, which is why nobody noticed the bug in the first place --
    but leaving it there means the namespace never converges on what
    this file says it should hold.

    Deleting is tolerant of absence, so a namespace which never had the
    broken rule pays one failed exec per network create and nothing
    else. It is also repeated until there is nothing left to delete: the
    ``_enable_nat`` which wrote this rule appended it unconditionally
    every time it ran, so an old namespace can hold several copies and
    removing one of them would leave the rest.
    """
    egress_veth_inner, _ = veth_names(vxid)
    return [
        ('filter', ['FORWARD', '-i', egress_veth_inner,
                    '-o', 'vx_veth_inner', '-j', 'ACCEPT']),
    ]


def hairpin_masquerade_rule(
        network_address: str, network_mask: str, vxid: int) -> list[str]:
    """Masquerade a floating address connection which turns around here.

    An instance reaching another instance's floating address sends it to
    its default gateway, which is the network's namespace; the
    PREROUTING DNAT rewrites the destination to the holder's address and
    the packet goes straight back out the veth it came in on. The reply
    then travels directly over the virtual network's L2, never returns
    through the namespace, and so is never un-DNATed: the client sees a
    packet from an address it never spoke to and drops it, and the
    connection hangs until it times out (issue 3662).

    Masquerading the u-turn to the namespace's own address on the
    network puts the namespace back on the return path, where conntrack
    can undo the destination rewrite. The cost is that the holder sees
    the connection as coming from the network's gateway rather than from
    the calling instance -- which is inherent to hairpin NAT, and is the
    price of the floating address being invisible to its holder in the
    first place. An instance which needs to know who is calling should
    be reached on a routed address, which is not rewritten at all.

    Both matches beside the target are load bearing, and each excludes a
    different thing:

    * ``-s`` is what preserves the caller's address for clients outside
      the cluster. Their traffic is DNATed here too, so the conntrack
      match alone would masquerade it and the holder would lose the
      real source address it sees today.
    * ``--ctstate DNAT`` is what excludes the routed address u-turn, and
      anything else this namespace forwards back into the network
      without rewriting. A routed address is never DNATed, and its whole
      point is that it arrives unrewritten.
    """
    _, vx_veth_inner = veth_names(vxid)
    return [
        'POSTROUTING', '-s', f'{network_address}/{network_mask}',
        '-o', vx_veth_inner, '-m', 'conntrack', '--ctstate', 'DNAT',
        '-j', 'MASQUERADE']


# The table the hairpin rule lives in, named here so the audit does not
# have to know it independently of the install.
HAIRPIN_TABLE = 'nat'


def network_nat_rules(
        network_address: str, network_mask: str,
        vxid: int) -> list[tuple[str, list[str]]]:
    """Every rule a NAT providing network needs, as (table, rule) pairs."""
    egress_veth_inner, vx_veth_inner = veth_names(vxid)

    return [
        # Traffic from the virtual network out to the world, and the
        # replies to it coming back. Both are decorative while the
        # namespace's FORWARD policy is ACCEPT, which is what a fresh
        # namespace has and nothing here changes -- which is why nobody
        # noticed that the return direction used to match the literal
        # string 'vx_veth_inner' rather than the interface named by the
        # variable of that name.
        ('filter', ['FORWARD', '-o', egress_veth_inner,
                    '-i', vx_veth_inner, '-j', 'ACCEPT']),
        ('filter', ['FORWARD', '-i', egress_veth_inner,
                    '-o', vx_veth_inner, '-j', 'ACCEPT']),

        # Instances reach the world as the network's floating gateway.
        ('nat', ['POSTROUTING', '-s', f'{network_address}/{network_mask}',
                 '-o', egress_veth_inner, '-j', 'MASQUERADE']),

        (HAIRPIN_TABLE, hairpin_masquerade_rule(
            network_address, network_mask, vxid)),
    ]
