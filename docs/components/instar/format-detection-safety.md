# Format Auto-Detection Safety in Instar

This document explains why instar considers format auto-detection to be safe,
despite `qemu-img` historically warning against it. Understanding this requires
examining both the original security concerns and how instar's architecture
mitigates them.

## Background: Why qemu-img Warns About Auto-Detection

The `qemu-img` tool has long warned about format auto-detection due to several
security concerns documented in CVEs:

### 1. Format Confusion Attacks (CVE-2008-2004)

When `qemu-img` auto-detects format, a malicious file named `image.raw` could
actually contain QCOW2 headers. This causes qemu to parse it as QCOW2 and
potentially follow backing file references like `/etc/shadow`.

### 2. Parser Selection Based on Untrusted Input

Auto-detection means an attacker chooses which parser runs by crafting magic
bytes. If one parser has vulnerabilities, the attacker can trigger it.

### 3. Metadata Exposure via Backing Files

Some formats (QCOW2, VMDK) support features that reference external files:
- **Backing files**: Parent images for copy-on-write (CVE-2015-5163)
- **External data files**: Separate data storage (CVE-2024-32498)
- **Descriptor files**: VMDK metadata files pointing to arbitrary paths
  (CVE-2022-47951)

When these features are present in a malicious image, `qemu-img` running on a
server can be tricked into reading arbitrary files from the host filesystem.

### 4. The Parser Paradox

Even if you detect "this is format X", you still need to parse format X to
validate it. The parsing step is where vulnerabilities live, so detection
alone doesn't prevent exploitation.

## How Instar Mitigates These Concerns

Instar's architecture addresses each of these concerns through **KVM sandbox
isolation**:

### KVM Sandbox Model

```
┌─────────────────────────────────────────────────────────────┐
│                         Host System                          │
│                                                              │
│  ┌──────────────┐     ┌──────────────────────────────────┐  │
│  │    instar     │     │         KVM Sandbox               │  │
│  │    (VMM)     │     │  ┌────────────────────────────┐  │  │
│  │              │     │  │     Guest (no_std Rust)    │  │  │
│  │  - File I/O  │     │  │                            │  │  │
│  │  - KVM setup │◄───►│  │  - Format parsing          │  │  │
│  │  - Virtio    │     │  │  - Image operations        │  │  │
│  │              │     │  │  - NO filesystem access    │  │  │
│  │              │     │  │  - NO network access       │  │  │
│  │              │     │  └────────────────────────────┘  │  │
│  └──────────────┘     └──────────────────────────────────┘  │
│                                                              │
│  Input File ─────────► [virtio-block] ─────► Guest reads     │
│  Output File ◄──────── [virtio-block] ◄───── Guest writes    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Mitigation Analysis

| Security Concern | qemu-img Risk | Instar Mitigation |
|-----------------|---------------|------------------|
| Backing file path traversal | HIGH - Can read /etc/passwd | **NONE** - Guest has no filesystem access |
| External data file attacks | HIGH - Can read arbitrary files | **NONE** - Guest has no filesystem access |
| VMDK descriptor exploitation | HIGH - Descriptor can point anywhere | **NONE** - Guest cannot open files by path |
| Parser memory corruption | HIGH - Could lead to code execution | **CONTAINED** - Exploit confined to sandbox |
| Resource exhaustion DoS | MEDIUM - Unbounded memory use | **CONTROLLED** - Fixed 32MB guest memory |
| Format confusion | MEDIUM - Wrong parser selected | **HARMLESS** - Sandbox contains any parser bugs |

### Why Backing File Attacks Fail

In a traditional `qemu-img` scenario:

```
# Malicious QCOW2 with backing_file = "/etc/shadow"
$ qemu-img info malicious.qcow2
# qemu-img attempts to open /etc/shadow -> Information disclosure
```

In instar:

```
# Same malicious QCOW2
$ instar info malicious.qcow2
# Guest parses QCOW2, sees backing_file="/etc/shadow"
# Guest attempts to... do what exactly?
# - Cannot call open("/etc/shadow") - no syscalls
# - Cannot access host memory - EPT isolation
# - Can only read from virtio-block device
# Result: Guest reports "backing_file: /etc/shadow" as metadata
#         but cannot actually read the file contents
```

### Parser Vulnerability Containment

If a malicious image exploits a parser bug in the guest:

1. **Memory corruption stays in guest**: The guest has its own address space
   (32MB), completely isolated from the host via EPT (Extended Page Tables)

2. **No escape paths**: The guest can only:
   - Read/write to virtio-block devices (which are files the VMM already opened)
   - Write to a serial port (for status messages)
   - Execute HLT to shut down

3. **No syscalls**: The guest runs in `no_std` Rust with no operating system.
   There is literally no mechanism to request host services.

4. **Worst case**: The guest crashes, hangs, or produces garbage output. The
   host terminates it via timeout and reports an error.

## When Auto-Detection is Safe

Based on this analysis, instar's auto-detection is safe when:

1. **Format parsing runs in the sandbox**: All header parsing, magic number
   checking, and metadata extraction happens inside the KVM guest.

2. **Results are treated as untrusted data**: The information returned (format
   type, backing file paths, etc.) comes from an untrusted source and should
   be displayed to users, not acted upon by the host.

3. **No host filesystem access**: The guest never needs to open files by path.
   The VMM pre-opens input/output files and presents them as block devices.

## What We Report vs. What We Trust

Instar's info operation will **report** information found in image headers:
- Format type (QCOW2, VMDK, RAW, etc.)
- Virtual size
- Backing file paths (if present)
- External data file paths (if present)
- Encryption status
- Compression settings

However, the VMM **never acts** on this information:
- It does not attempt to open backing files
- It does not follow external data file references
- It does not validate paths against the host filesystem

This information is displayed to users for their awareness, with appropriate
warnings for potentially dangerous features.

## Security Warnings in Output

When instar detects potentially dangerous metadata, it should warn users:

```
$ instar info suspicious.qcow2

Format:         QCOW2 (version 3)
Virtual size:   10 GiB
Disk size:      2.5 GiB

⚠ WARNING: Image references external files
  Backing file:     /var/lib/images/base.qcow2
  External data:    /tmp/data.raw

These paths were found in the image metadata but have NOT been accessed.
If you did not expect these references, this image may be malicious.
```

## Comparison with qemu-img Recommendations

The standard advice for `qemu-img` is:

> Always specify image format explicitly:
> ```
> qemu-img info --format=qcow2 image.qcow2
> ```

With instar, this advice is **optional** rather than **required**:

| Approach | qemu-img | instar |
|----------|----------|-------|
| Auto-detect format | Unsafe | Safe (sandboxed) |
| Explicit format | Safe | Safe (sandboxed) |
| Benefit of explicit | Prevents parser selection attacks | Slightly faster (skip detection) |

Users can still specify format explicitly if they prefer, but there is no
security benefit in doing so.

## Conclusion

Instar's KVM sandbox architecture fundamentally changes the security model for
image processing. By isolating all format parsing and operations inside a
hardware-enforced virtual machine:

1. **Backing file attacks become information-only**: We can report what the
   image claims its backing file is, but we cannot be tricked into reading it.

2. **Parser vulnerabilities are contained**: Even a complete compromise of the
   guest cannot affect the host system.

3. **Auto-detection becomes safe**: The attacker can choose which parser runs,
   but all parsers run in the sandbox.

This is why instar enables format auto-detection by default, while providing
clear documentation of what information came from untrusted sources.

---

## Related Documents

- [security.md](/components/instar/security/) - Comprehensive CVE analysis
- [ARCHITECTURE.md](https://github.com/shakenfist/instar/blob/develop/ARCHITECTURE.md#security-model) - Overall security architecture
- [qcow2/qcow2-format.md](/components/instar/qcow2/qcow2-format/) - QCOW2 format details
- [vmdk/vmdk-format.md](/components/instar/vmdk/vmdk-format/) - VMDK format details

---

*Document created: January 2026*
