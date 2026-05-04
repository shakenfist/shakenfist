# Image Handling and qemu-img Security Vulnerabilities

This document provides a comprehensive analysis of disclosed CVEs related to image
handling and qemu-img across major virtualization platforms: oVirt, Proxmox VE,
and OpenStack, as well as general qemu-img vulnerabilities.

## Executive Summary

Image handling represents a critical attack surface in virtualization platforms.
The vulnerabilities documented here fall into several categories:

1. **Arbitrary File Access** - Exploiting image format features (backing files,
   external data files, VMDK descriptors) to read arbitrary files from servers
2. **Denial of Service** - Resource exhaustion through maliciously crafted images
3. **Privilege Escalation** - Bypassing authorization or escalating to root
4. **Code Execution** - Memory corruption leading to arbitrary code execution

---

## Table of Contents

1. [Cross-Platform qemu-img Vulnerabilities](#cross-platform-qemu-img-vulnerabilities)
2. [OpenStack Vulnerabilities](#openstack-vulnerabilities)
3. [oVirt Vulnerabilities](#ovirt-vulnerabilities)
4. [Proxmox VE Vulnerabilities](#proxmox-ve-vulnerabilities)
5. [Image Format Specific Issues](#image-format-specific-issues)
6. [Recommendations](#recommendations)
7. [References](#references)

---

## Cross-Platform qemu-img Vulnerabilities

These vulnerabilities affect qemu-img directly and impact all platforms using it.

### CVE-2024-4467 - qemu-img 'info' Command Vulnerability

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2024-4467 |
| **CVSS Score** | 7.8 (High) |
| **CWE** | CWE-400: Uncontrolled Resource Consumption |
| **Affected Versions** | qemu < 7.2.12, < 8.2.5, < 9.0.1 |

**Description:** A flaw in qemu-img's 'info' command allows specially crafted
image files containing `json:{}` values describing block devices to cause
excessive memory/CPU consumption or unauthorized file read/write operations.

**Impact:**
- Denial of Service through resource exhaustion
- Unauthorized read/write to external files on the host
- Affects oVirt, Proxmox, and OpenStack deployments

**Mitigation:** Update to qemu 7.2.12, 8.2.5, or 9.0.1+.

---

### CVE-2014-0144 - Block Driver Input Validation Failure

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2014-0144 |
| **Severity** | High |
| **Affected Versions** | qemu < 2.0.0 |

**Description:** qemu block drivers for CLOOP, QCOW2 v2, and other formats are
vulnerable to memory corruptions, integer/buffer overflows due to missing input
validations.

**Impact:** Remote code execution on host with qemu process privileges.

---

### CVE-2014-0222 & CVE-2014-0223 - QCOW1 Integer Overflows

| Attribute | Value |
|-----------|-------|
| **CVE IDs** | CVE-2014-0222, CVE-2014-0223 |
| **CVSS Score** | 7.5 (High) |
| **Affected Versions** | qemu < 1.7.2 |

**Description:** Integer overflow flaws in QCOW version 1 handling allow
memory corruption and potential code execution.

---

### CVE-2008-2004 - RAW Image Format Probe Vulnerability

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2008-2004 |
| **Severity** | Moderate |

**Description:** When image format is not explicitly specified, qemu probes the
image header to guess format. A malicious guest can write a crafted header to
trick qemu into using a different format, enabling arbitrary file access.

**Key Lesson:** Always explicitly specify image format with `--format` flag.

---

## OpenStack Vulnerabilities

### CVE-2024-32498 (OSSA-2024-001) - QCOW2 External Data File Access

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2024-32498 |
| **CVSS Score** | 8.8 (High) |
| **Affected Services** | Cinder, Glance, Nova |
| **Published** | July 2024 |

**Affected Versions:**
- Cinder: <22.1.3, >=23.0.0 <23.1.1, ==24.0.0
- Glance: <26.0.1, ==27.0.0, >=28.0.0 <28.0.2
- Nova: <27.3.1, >=28.0.0 <28.1.1, >=29.0.0 <29.0.3

**Description:** QCOW2 images have two mechanisms to read external files:
backing files (fixed in 2015) and external data files (newly discovered).
Attackers can craft images referencing sensitive files on the target server.

**Impact:** Unauthorized read access to `/etc/passwd`, configuration files,
private keys, etc.

---

### CVE-2024-40767 (OSSA-2024-002) - Incomplete Fix Regression

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2024-40767 |
| **Affected Services** | Nova |
| **Published** | July 2024 |

**Description:** Incomplete fix for CVE-2024-32498 and regression of
CVE-2022-47951. Raw format images that are actually QCOW2/VMDK with backing
file references can bypass protections.

**Warning:** These fixes cannot block malicious images already in Nova's cache.

---

### CVE-2022-47951 (OSSA-2023-002) - VMDK Flat Descriptor File Access

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2022-47951 |
| **CVSS Score** | 5.7 (Medium) |
| **Affected Services** | Cinder, Glance, Nova |
| **Published** | January 2023 |

**Description:** VMDK flat images can reference backing files through
descriptor files, enabling unauthorized file access.

**Impact:** All Cinder and Nova deployments affected; Glance only if image
conversion is enabled.

---

### CVE-2024-44082 (OSSA-2024-003) - Ironic Image Processing

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2024-44082 |
| **CVSS Score** | 4.3 (Medium) |
| **Affected Services** | Ironic, Ironic-Python-Agent |
| **Published** | September 2024 |

**Description:** Running qemu-img on untrusted images without format
pre-specification allows exploitation of unsafe format features.

**New Configuration Options:**
- `conductor_always_validates_images = True`
- `permitted_image_formats = raw,qcow2`

---

### CVE-2015-5162 (OSSA-2016-012) - Resource Exhaustion DoS

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2015-5162 |
| **CVSS Score** | 5.5 (Medium) |
| **Affected Services** | Cinder, Glance, Nova |

**Description:** Maliciously crafted disk images cause excessive RAM and CPU
consumption. Fix ensures qemu-img runs with resource limits (2s CPU, 1GB RAM).

---

### CVE-2015-1851 - Cinder Format Guessing Exploit

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2015-1851 |
| **Affected Services** | Cinder |

**Description:** Cinder didn't specify input format to qemu-img convert.
Attackers could create volumes with QCOW2 signatures containing base file
references, causing Cinder (running as root) to embed arbitrary file contents.

**Exploitation:**
1. Create volume and attach to VM
2. Write QCOW2 signature with base-file reference from within VM
3. Trigger upload with `cinder upload-to-image --disk-type qcow2`
4. Uploaded image contains contents of referenced files

---

## oVirt Vulnerabilities

### CVE-2018-10908 - VDSM qemu-img Resource Exhaustion

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2018-10908 |
| **CVSS Score** | 6.3-6.5 (Medium) |
| **Affected Versions** | VDSM < 4.20.37 |

**Description:** VDSM invokes qemu-img on untrusted inputs without resource
limits. Specially crafted images cause unbounded memory/CPU consumption.

**Mitigation:** Upgrade VDSM to 4.20.37+.

---

### CVE-2019-3831 - VDSM Privilege Escalation

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2019-3831 |
| **CVSS Score** | 6.4-9.0 (Medium-High) |
| **Affected Versions** | VDSM 4.19 - 4.30.8 |

**Description:** The `systemd_run` function exposed to vdsm user can be abused
to execute arbitrary commands as root.

---

### CVE-2019-3879 - oVirt REST API Authorization Bypass

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2019-3879 |
| **CVSS Score** | 6.5-8.1 (Medium-High) |
| **Affected Versions** | oVirt < 4.3.2.1 |

**Description:** RemoveDiskCommand is triggered as internal command, skipping
permission validation. Users with Basic Operations role can delete disks.

---

### CVE-2015-3456 (VENOM) - qemu FDC Buffer Overflow

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2015-3456 |
| **Severity** | Important |

**Description:** Buffer overflow in qemu's Floppy Disk Controller emulation
allows guest-to-host escape. Exploitable even without explicit floppy
configuration.

---

## Proxmox VE Vulnerabilities

### CVE-2024-21545 - API Arbitrary File Read

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2024-21545 |
| **CVSS Score** | 8.2 (High) |
| **Affected Versions** | Proxmox VE <= 8.2.2 |

**Description:** Insufficient safeguards against malicious API response values
allow authenticated attackers with 'Sys.Audit' or 'VM.Monitor' privileges to
download arbitrary host files including `/etc/shadow` and authentication keys.

**Impact:** Session token forgery and complete system takeover.

---

### CVE-2022-35508 - SSRF and File Disclosure

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2022-35508 |
| **CVSS Score** | 9.8 (Critical) |
| **Affected Products** | Proxmox VE, Proxmox Mail Gateway |

**Description:** SSRF vulnerability when proxying HTTP requests between
pve(pmg)proxy and pve(pmg)daemon. Unprivileged accounts can achieve SSRF and
file disclosure.

**Verification:**
```bash
pveversion -v | grep libpve-http-server-perl
```

---

### CVE-2023-43320 - Two-Factor Authentication Bypass

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2023-43320 |
| **Severity** | High |
| **Affected Versions** | PVE 5.4-8.0, PBS 1.1-3.0, PMG 7.1-8.0 |

**Description:** Privilege escalation by bypassing 2FA component.

**Mitigation:** Upgrade to Proxmox VE 8.1+.

---

### CVE-2024-9486 - Kubernetes Image Builder Default Credentials

| Attribute | Value |
|-----------|-------|
| **CVE ID** | CVE-2024-9486 |
| **CVSS Score** | 9.8 (Critical) |
| **Affected Product** | Kubernetes Image Builder (Proxmox provider) |

**Description:** VM images built with Kubernetes Image Builder retain default
credentials from the build process.

**Mitigation:**
```bash
usermod -L builder
```

---

## Image Format Specific Issues

### QCOW/QCOW2 Encryption Weakness

The native AES encryption in QCOW/QCOW2 has design flaws:
- AES-CBC with predictable IVs based on sector number
- Vulnerable to chosen plaintext attacks
- Passphrase directly used as encryption key
- No key rotation capability

**Recommendation:** Use LUKS encryption instead.

### Recurring Vulnerability Patterns

| Pattern | Examples |
|---------|----------|
| Format auto-detection abuse | CVE-2008-2004, CVE-2015-1851, CVE-2024-44082 |
| Backing file exploitation | CVE-2015-5163, CVE-2024-32498, CVE-2024-40767 |
| VMDK descriptor exploitation | CVE-2022-47951, CVE-2024-40767 |
| External data file exploitation | CVE-2024-32498 |
| Resource exhaustion | CVE-2015-5162, CVE-2018-10908, CVE-2024-4467 |

### Root Cause: RAW as Fallback Format

A fundamental design decision in qemu-img enables many of these vulnerabilities:
**any file that doesn't match a known format is treated as a valid "raw" disk
image.**

This means when qemu-img follows a backing file reference to `/etc/shadow`:

1. It opens the file and attempts format detection
2. `/etc/shadow` has no QCOW2/VMDK/VHD magic number
3. qemu-img treats it as a "raw" disk image (the fallback)
4. The file contents are read as disk data

If qemu-img instead rejected files without recognized disk image headers, these
attacks would fail. A `/etc/shadow` file would be rejected as "not a valid disk
image" rather than being silently accepted as "raw."

A more defensive design would require:
- Known format magic numbers (QCOW2, VMDK, VHD, etc.), OR
- Valid partition table (MBR/GPT) for raw images

This is why oslo.utils `format_inspector` detects MBR/GPT partition tables -
to distinguish genuine raw disk images from arbitrary files that qemu-img would
happily accept. See [quirks.md](/components/instar/quirks/#raw-as-fallback-format) for details.

---

## Recommendations

### For All Platforms

1. **Always specify image format explicitly**
   ```bash
   qemu-img info --format=qcow2 image.qcow2
   qemu-img convert -f qcow2 -O raw input.qcow2 output.raw
   ```

2. **Apply resource limits to qemu-img operations**
   - CPU time: 2 seconds maximum
   - Memory: 1 GB maximum

3. **Validate images before processing**
   - Use format inspection before passing to qemu-img
   - Reject images with backing files or external references
   - Only accept images from trusted sources

4. **Keep systems updated**
   - Apply security patches promptly
   - Monitor vendor security advisories

5. **Use LUKS for encryption** instead of native QCOW/QCOW2 encryption

### OpenStack Specific

- Enable `conductor_always_validates_images = True` for Ironic
- Configure `permitted_image_formats = raw,qcow2`
- Use the format_inspector module to pre-screen images
- Disable image conversion in Glance if not required

### oVirt Specific

- Keep VDSM updated to latest version
- Monitor Red Hat security advisories
- Implement network segmentation for management networks

### Proxmox Specific

- Restrict API access for 'Sys.Audit' and 'VM.Monitor' privileges
- Ensure 2FA is properly configured on Proxmox VE 8.1+
- Only import disk images from trusted sources
- Disable builder account on Kubernetes Image Builder VMs

---

## Vulnerability Timeline

```
2008: CVE-2008-2004    - Format probing vulnerability (qemu-img)

2014: CVE-2014-0143    - Multiple block driver integer overflows
      CVE-2014-0144    - Block driver input validation
      CVE-2014-0145    - Block driver logic errors
      CVE-2014-0146    - QCOW2 NULL pointer dereference
      CVE-2014-0147    - QCOW2 snapshot crash
      CVE-2014-0148    - VHDX BAT entry flaw
      CVE-2014-0222/3  - QCOW1 integer overflows

2015: CVE-2015-1851    - Cinder format guessing
      CVE-2015-3456    - VENOM (qemu FDC buffer overflow)
      CVE-2015-5162    - Resource exhaustion DoS
      CVE-2015-5163    - QCOW2 backing file disclosure

2018: CVE-2018-10908   - VDSM qemu-img resource exhaustion

2019: CVE-2019-3831    - VDSM privilege escalation
      CVE-2019-3879    - oVirt API authorization bypass

2022: CVE-2022-35507   - Proxmox CRLF injection
      CVE-2022-35508   - Proxmox SSRF
      CVE-2022-47951   - VMDK flat descriptor file access

2023: CVE-2023-43320   - Proxmox 2FA bypass

2024: CVE-2024-4467    - qemu-img info json:{} vulnerability
      CVE-2024-21545   - Proxmox API file read
      CVE-2024-32498   - QCOW2 external data file access
      CVE-2024-40767   - Incomplete fix regression
      CVE-2024-44082   - Ironic image processing
      CVE-2024-9486    - K8s Image Builder default credentials
```

---

## References

### General Resources
- [qemu Security Process](https://www.qemu.org/contribute/security-process/)
- [NVD - National Vulnerability Database](https://nvd.nist.gov/)
- [CVE Details - qemu](https://www.cvedetails.com/vulnerability-list/vendor_id-7506/Qemu.html)

### OpenStack
- [OpenStack Security Advisories](https://security.openstack.org/)
- [OSSA-2024-001](https://security.openstack.org/ossa/OSSA-2024-001.html)
- [OSSA-2024-002](https://security.openstack.org/ossa/OSSA-2024-002.html)
- [OSSA-2024-003](https://security.openstack.org/ossa/OSSA-2024-003.html)
- [OSSA-2023-002](https://security.openstack.org/ossa/OSSA-2023-002.html)

### oVirt
- [oVirt Security Page](https://www.ovirt.org/community/security.html)
- [Red Hat CVE Database](https://access.redhat.com/security/security-updates/cve)
- [CVE Details - oVirt](https://www.cvedetails.com/vulnerability-list/vendor_id-12202/Ovirt.html)

### Proxmox
- [Proxmox Security Advisories](https://forum.proxmox.com/forums/security-advisories.26/)
- [Proxmox Security Reporting](https://pve.proxmox.com/wiki/Security_Reporting)
- [CVE Details - Proxmox](https://www.cvedetails.com/vulnerability-list/vendor_id-13186/Proxmox.html)

---

*Document compiled: January 2026*
