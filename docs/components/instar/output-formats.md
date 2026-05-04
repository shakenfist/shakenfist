# qemu-img Output Formats

This document describes the output formats supported by `qemu-img info` and how
instar handles version differences across qemu releases.

## Overview

`qemu-img info` supports multiple output formats via the `--output` flag:

| Format | Flag | Description |
|--------|------|-------------|
| Human-readable | `--output=human` (default) | Formatted text for terminal display |
| JSON | `--output=json` | Machine-parseable JSON structure |

instar supports both formats and can be configured to match the output of specific
qemu-img versions.

## Output Profiles

Analysis of 80+ qemu-img versions (6.0.0 through 10.2.0) revealed that outputs
fall into distinct profiles based on structural changes in the output format.
All versions within a profile produce byte-identical output for the same input
image.

### Human-Readable Output Profiles

| Profile | qemu-img Versions | Key Difference |
|---------|-------------------|----------------|
| `profile-6-0-0` | 6.0.0 - 7.2.x | No "Child node '/file'" section |
| `profile-8-0-0` | 8.0.0 - 10.x+ | Includes "Child node '/file'" section |

The only structural difference between these profiles is the presence of the
"Child node '/file'" section, which was added in qemu-img 8.0.0.

**Example: qemu-img 7.2.x output (profile-6-0-0)**

```
image: /path/to/image.qcow2
file format: qcow2
virtual size: 10 GiB (10737418240 bytes)
disk size: 196 KiB
cluster_size: 65536
Format specific information:
    compat: 1.1
    compression type: zlib
    lazy refcounts: false
    refcount bits: 16
    corrupt: false
    extended l2: false
```

**Example: qemu-img 8.0+ output (profile-8-0-0)**

```
image: /path/to/image.qcow2
file format: qcow2
virtual size: 10 GiB (10737418240 bytes)
disk size: 196 KiB
cluster_size: 65536
Format specific information:
    compat: 1.1
    compression type: zlib
    lazy refcounts: false
    refcount bits: 16
    corrupt: false
    extended l2: false
Child node '/file':
    filename: /path/to/image.qcow2
    protocol type: file
    file length: 200 KiB (204800 bytes)
    disk size: 200 KiB
```

### JSON Output Profiles

| Profile | qemu-img Versions | Key Difference |
|---------|-------------------|----------------|
| `profile-6-0-0` | 6.0.0 - 7.2.x | No `children` array |
| `profile-8-0-0` | 8.0.0 - 10.x+ | Includes `children` array with file node info |

**Example: qemu-img 7.2.x JSON output (profile-6-0-0)**

```json
{
    "virtual-size": 10737418240,
    "filename": "/path/to/image.qcow2",
    "cluster-size": 65536,
    "format": "qcow2",
    "actual-size": 200704,
    "format-specific": {
        "type": "qcow2",
        "data": {
            "compat": "1.1",
            "compression-type": "zlib",
            "lazy-refcounts": false,
            "refcount-bits": 16,
            "corrupt": false,
            "extended-l2": false
        }
    },
    "dirty-flag": false
}
```

**Example: qemu-img 8.0+ JSON output (profile-8-0-0)**

```json
{
    "children": [
        {
            "name": "file",
            "info": {
                "children": [],
                "virtual-size": 204800,
                "filename": "/path/to/image.qcow2",
                "format": "file",
                "actual-size": 204800,
                "format-specific": {
                    "type": "file",
                    "data": {}
                },
                "dirty-flag": false
            }
        }
    ],
    "virtual-size": 10737418240,
    "filename": "/path/to/image.qcow2",
    "cluster-size": 65536,
    "format": "qcow2",
    "actual-size": 200704,
    "format-specific": {
        "type": "qcow2",
        "data": {
            "compat": "1.1",
            "compression-type": "zlib",
            "lazy-refcounts": false,
            "refcount-bits": 16,
            "corrupt": false,
            "extended-l2": false
        }
    },
    "dirty-flag": false
}
```

## Version Detection

instar can detect the installed qemu-img version at runtime and automatically
emit output matching that version's format. This ensures true drop-in
replacement compatibility.

### Automatic Detection

By default, instar detects the qemu-img version by running `qemu-img --version`
and parsing the version string. It then selects the appropriate output profile.

```bash
# Automatic version detection (matches installed qemu-img)
instar info image.qcow2
```

### Explicit Version Override

The `--qemu-version` flag allows forcing a specific output format:

```bash
# Force qemu-img 7.2 compatible output (no Child node section)
instar info --qemu-version 7.2 image.qcow2

# Force qemu-img 10.0 compatible output (includes Child node section)
instar info --qemu-version 10.0 image.qcow2
```

### Version Mapping

| Specified Version | Output Profile |
|-------------------|----------------|
| 6.x, 7.x | profile-6-0-0 |
| 8.x, 9.x, 10.x+ | profile-8-0-0 |

## JSON Output

JSON output is enabled with the `--output=json` flag:

```bash
instar info --output=json image.qcow2
```

The JSON structure matches qemu-img exactly, including:
- Field names use hyphens (e.g., `virtual-size`, not `virtual_size`)
- Nested `format-specific` object with `type` and `data` fields
- Boolean `dirty-flag` field
- Version-appropriate `children` array (8.0+ only)

### Parsing JSON Output

The JSON output is designed for machine parsing:

```python
import json
import subprocess

result = subprocess.run(
    ['instar', 'info', '--output=json', 'image.qcow2'],
    capture_output=True,
    text=True
)
info = json.loads(result.stdout)

print(f"Format: {info['format']}")
print(f"Virtual size: {info['virtual-size']} bytes")
print(f"Actual size: {info['actual-size']} bytes")
```

## Testing and Validation

The [instar-testdata](https://github.com/shakenfist/instar-testdata) repository
contains baseline outputs for multiple qemu-img versions and output formats.
This enables:

1. **Profile detection**: Grouping versions that produce identical output
2. **Regression testing**: Ensuring instar matches expected baselines
3. **Drift detection**: CI jobs that compare instar output against stored baselines

### Baseline Structure

```
instar-testdata/expected-outputs/
  qemu-img-human/
    raw/<version>/          # Raw captured outputs per version
    profiles/<profile>/     # Deduplicated profile outputs
    version-map.json        # Version to profile mapping
  qemu-img-json/
    raw/<version>/
    profiles/<profile>/
    version-map.json
```

Each output format is processed independently, allowing for different profile
groupings if the formats diverge in future qemu releases.

## Implementation Notes

### Child Node Section

The "Child node '/file'" section (and corresponding `children` array in JSON)
was added in qemu-img 8.0.0 to expose information about the underlying protocol
layer. For file-backed images, this includes:

- The absolute path to the file
- Protocol type (`file` for local files)
- File length (raw file size)
- Disk size (block-allocated size)

This information was previously only available by examining the image file
directly. The addition improves transparency for debugging and scripting.

### Compatibility Considerations

When writing scripts that parse qemu-img/instar output:

1. **Human-readable format**: Use regex patterns that handle optional sections
2. **JSON format**: Check for `children` key existence before accessing
3. **Version detection**: Use `--qemu-version` for consistent output across systems

Example robust JSON parsing:

```python
info = json.loads(output)

# Handle both old and new versions
if 'children' in info:
    file_info = next(
        (c['info'] for c in info['children'] if c['name'] == 'file'),
        None
    )
    if file_info:
        print(f"File length: {file_info['virtual-size']}")
```
