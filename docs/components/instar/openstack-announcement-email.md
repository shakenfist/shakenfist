# Announcement

The following is the email I propose to send to the openstack-discuss
mailing list when I am ready to publicly announce instar:

```
Subject: Is there a safer way to handle untrusted disk images
than qemu-img?

For a while now I've been wondering if there's a safer way to
handle untrusted disk images in various clouds. They're a frequent
cause of security vulnerability announcements, largely because
qemu is a fairly complicated code base where the block device
handling is entangled into the larger product, qemu also implements
a variety of features clouds don't even want, and poorly
intentioned people can spend as much time as they want crafting
custom images to find interesting edge conditions in the
implementation. I am not convinced that the current strategy of
closing gaps as they're found is really paying off.

Recent examples of such `qemu-img` related vulnerabilities
include:

* CVE-2026-24708 (OSSA-2026-002)
* CVE-2024-44082 (OSSA-2024-003)
* CVE-2024-4467 (OSSA-2024-002)
* CVE-2024-32498 (OSSA-2024-001)
* CVE-2022-47951 (OSSA-2023-002)

I toyed with the idea of a "stripped down" `qemu-img` which could
simply not have the features which cause these vulnerabilities,
but the complexity of the qemu codebase makes this approach
painful, while also not presenting any of the other opportunities
of a greenfields approach like memory safety and a tight sandbox.

So what might a safer approach look like? My answer is to try and
offload the processing of untrusted data to the virtual machine
implementation in silicon -- it has strong separation guarantees
and it can be orchestrated so that there is simply no access to
anything not required for the strict processing of the image. So
for example no access to a file system, a network, and so forth.
Specifically, what if processing this untrusted and potentially
malicious data was done as a KVM guest with a custom virtual
machine manager (VMM)?

So I wrote it, and by "I wrote it" I mean Claude Code mostly
wrote it. Instar is a from scratch rethink of how to handle
untrusted image data as well as an opportunity for me to become
more familiar with code generation LLMs. All data processing
happens in a custom KVM guest which does not run an operating
system or have any access to system resources apart from those
provided to it by the custom VMM which orchestrates it. The best
way to think of this guest is as an embedded system running on a
virtual CPU. This is similar in approach to how AWS Nitro
Enclaves work in terms of prior art, whilst also being the
opposite in intent: Nitro protects sensitive data *from* the
cloud; instar protects the cloud *from* malicious data. To be
clear, `instar info` doesn't detect or block malicious images -
it processes them safely and reports what it finds. The security
comes from containment, not detection. It is then up to the
caller to decide how to handle what is reported.

Instar currently requires Linux with KVM support on x86-64. Early
benchmarks suggest performance overhead from the KVM sandbox is
modest - in the noise for metadata operations like `info`.

Images are provided to this guest as block devices using the
virtio-block protocol, but it should be noted that while the
guest and the VMM are using that protocol, the Linux kernel is
not involved. Instar is implemented in rust because bare metal
golang turned out to be a bit hard, and because the existing
rust VMM crates are actually quite good and used by projects
like Firecracker already. You can read more about the technology
choices and concepts behind instar in painful detail at
https://github.com/shakenfist/instar/blob/main/docs/technology-primer.md

The primary use case I'm targeting is validating untrusted images
at upload time (e.g., in Glance), though the same approach could
apply anywhere qemu-img is invoked on user-supplied data.

The current goal of Instar is to be command line compatible with
qemu-img (especially `qemu-img info` and `qemu-img convert`),
but I also wonder about opportunities to get out of the game of
parsing command output. Whilst Instar can certainly emit JSON
formatted output, there are other options as well like running
it as a persistent daemon which services requests it receives
over a unix domain socket. I've played that game with other code
recently and find protobufs to be a reasonable protocol for such
things. I don't know how OpenStack feels about protobufs however.

By "command line compatible", I mean that my current goal is to
provide a version of `instar info` and `instar convert` that is
byte for byte identical to the output of the same `qemu-img info`
or `qemu-img convert` command. This has some interesting edge cases
in as much as `qemu-img` has changed its output format at least
once since 2020. Additionally, I suspect I've found at least one
`qemu-img` bug in this process. Instar resolves these issues by
having a test suite of sample images, recording the `qemu-img`
output for those images, and then generating a database of the
different output formats. When you run `instar info` on your
machine, instar attempts to detect the version of `qemu-img` you
have installed, and will match its output format. The interesting
little edge cases I've found along the way are documented at
https://github.com/shakenfist/instar/blob/main/docs/quirks.md,
and the output formats the `qemu-img info` produces are
documented at
https://github.com/shakenfist/instar/blob/main/docs/output-formats.md.

At the moment I'd describe Instar as a "vigorous prototype". It
implements `qemu-img info` and most of `qemu-img convert`, but
does not yet know how to resize, rebase, snapshot, and so forth.
I'd be interested in whether other people think this is an
interesting idea or not, although honestly it was just kind of
fun to build over the summer holidays. I'd particularly welcome
feedback on whether this approach would be useful for your
deployment, images that produce unexpected results, and thoughts
on the daemon / protobuf interface idea.

Instar is licensed under Apache 2.0. The code and extensive notes
are at https://github.com/shakenfist/instar/. Bug reports,
examples of images which didn't work as expected, and pull
requests should all go there please.

Michael
```
