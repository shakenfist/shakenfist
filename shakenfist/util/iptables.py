# Copyright 2026 Michael Still and contributors

"""The iptables rules a NAT providing network needs in its namespace.

The rules are data here rather than statements inside sf-privexec, so
that the set can be walked -- which is what makes installing each of
them at most once a loop rather than several hand written copies of the
same check.

A rule is the chain name followed by the match and target arguments
exactly as iptables writes them after ``-A``, which is also exactly
what it wants after ``-C``. So the same list serves both.

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
    ]
