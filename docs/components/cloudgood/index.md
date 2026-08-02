---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/index.md
---

# Welcome to the Derek Zoolander Centre For Kids Who Want To Cloud Good

This is essentially a set of working notes on cloud and distributed compute
topics that I think are of interest or are important to succeed as a cloud
engineer. They've been collected as a response to the questions I receive
from time to time, and come from a place of trying to be helpful. It is
completely fine to pick and choose which parts of all this you read as your
interests guide you.

By definition this work will never be complete. I welcome suggestions
(preferably as issues on the git repository at
https://github.com/shakenfist/cloudgood), but would also happily accept
contributions (as pull requests against the same repository please)
for review and discussion. Much of this documentation was co-authored with
Claude, an AI assistant. I guided the content and verified technical accuracy.
Specific contributions are noted in commit messages.

!!! note

    Please note that unlike the rest of the Shaken Fist projects, this work
    is licensed under
    [Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/)
    license, which seemed more appropriate for a "book like" work than the
    usual Apache2.

This document is also by definition Linux specific, and to a large
extent x86 specific. This is because this is where my own experience lies.

First off, we need to discuss some fundamentals.

[The technology primer](/components/cloudgood/technology-primer/) provides a relatively fast
paced introduction to many of the fundamentals of modern virtualization
and containers. This includes what a process is, `fork()` and `exec()`,
virtual memory and page tables, context switching between processes,
protection rings, virtualization and its costs, containers and their
isolation weaknesses, and unikernels. This document is derived from the
documentation for a not yet released project, so there are some gaps here
but its a reasonable primer. If you've done a computer science degree
or whatever more technical YouTube videos than your partner thinks is
reasonable, you can probably just dive in right here. It wont _harm_ you
to read this part if you don't have a super technical background, you
might just not understand everything. That's ok though, because the goal
is that by the end of this content _you_ _will_ and that will be cool.

The next set of pages provide a slower paced but much more detailed
introduction, while not repeating too much of the content of the technology
primer.

[Fundamentals](/components/cloudgood/fundamentals/) discusses some of the fundamentals of
computing that we're going to keep coming upon.

[CPU and resource accounting](/components/cloudgood/accounting/) explores how the Linux
kernel measures CPU time for both regular processes and virtual
machines. It turns out the mechanism is fundamentally the same in
both cases -- timer interrupts give the kernel regular opportunities
to note what was running and charge the time. This chapter also covers
steal time (how guests detect lost CPU) and cgroups (how the kernel
enforces resource limits).

[Virtualization history](/components/cloudgood/virtualization-history/) takes a slower journey
through the history of virtualization and how we got to where we are now.
Virtualization has been around in various forms for a lot longer than many
people realize, and has seen various iterations along the way. This matters
because some of the tooling we use today dates back to the heady days of
full system emulation, and understanding the history can help build a mental
model for how things work and why.

[More fundamentals](/components/cloudgood/more-fundamentals/) then circles back to talking
through some more fundamental things you would have learned in a university
level operating systems class, but without taking six months of your life.
There is also no exam, you're welcome.

[Instance video](/components/cloudgood/instance-video/) is an exploration of how video inside
instances works, why you would even want video from inside an instance,
and the history of computer graphics for x86 computers.

Next we move on to talking about virtual networking, with
[virtual networking](/components/cloudgood/virtual-networking/) is an overview of virtual
networking in general, and then we walk through a series of real world
examples of incrementing complexity starting with
[Shaken Fist's implementation](/components/cloudgood/networking-shakenfist/), the now
obsolete [OpenStack Neutron Linux Bridge networking](/components/cloudgood/neutron-linuxbridge/),
then [OpenStack Neutron's Legacy non-high availability routing](/components/cloudgood/neutron-legacy/),
with a solid intention to one day cover OpenStack Neutron's HA and DVR
options at a future date.

We then turn to the control plane's own state.
[Galera and WSREP replication](/components/cloudgood/galera/) explains how the database
underneath almost every OpenStack deployment actually works: how a
transaction is turned into a write set, broadcast in a global total order,
and certified independently on every node without any of them talking to
each other. That mechanism explains most of the things operators find
surprising -- deadlock errors at commit time, one slow node pausing the
whole cluster, and why a backup taken carelessly on a "spare" node can hurt
the busy one. It closes with what all of this puts on the network, and why
the replication network is the cluster's real security boundary.

--8<-- "docs-include/abbreviations.md"
