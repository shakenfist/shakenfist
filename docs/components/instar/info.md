# Info

`instar info` displays image format information, as a drop-in
replacement for `qemu-img info`.

```bash
# Display image format information (matches qemu-img info output)
instar info image.qcow2

# Discover and display the complete backing file chain
instar info --chain image.qcow2

# Inspect LUKS container with inner format detection
instar info --luks-passphrase 'secret' encrypted.luks
```

The `--chain` flag iteratively runs the sandboxed info operation on each image
in the backing chain, validating paths against a security allowlist to prevent
directory traversal attacks. See [chain-discovery.md](/components/instar/chain-discovery/) for
the full chain discovery design.

## Version compatibility

Different qemu-img versions produce slightly different output formats:

- **qemu-img 6.0-7.2** (Debian 12 bookworm): No "Child node '/file'" section
- **qemu-img 8.0+** (Debian 13 trixie): Includes "Child node '/file'" section

By default, instar detects the installed qemu-img version and emits matching
output. This ensures true drop-in replacement compatibility.

To explicitly specify which qemu-img version's output format to use:

```bash
# Emit output compatible with qemu-img 7.2 (no Child node section)
instar info --qemu-version 7.2 image.qcow2

# Emit output compatible with qemu-img 10.0 (includes Child node section)
instar info --qemu-version 10.0 image.qcow2
```

See [output-formats.md](/components/instar/output-formats/) for detailed documentation on
output format profiles.

## VHDX sizes

A VHDX's virtual size and block size (reported as the cluster size)
are read from the File Parameters and Virtual Disk Size metadata
items, which `info` finds by GUID in the metadata table. SPEC(VHDX)
2.6.1.2 gives metadata items no fixed order or position, so `info`
never assumes one. If the metadata cannot be parsed (a missing
required item, an item outside the file, or a block size that is not
a power of two between 1 MiB and 256 MiB), `info` reports the file's
size as the virtual size and no cluster size, rather than a value it
could not trust.

## Differencing images

A differencing VHD (`disk_type == 4`) or differencing VHDX (`HasParent`
set) has its parent reported as a backing file, in both human
(`backing file: ...`) and `--output json` (`backing-filename` /
`full-backing-filename`) forms — the same fields qcow2's backing file
already uses. `convert`, `dd`, `compare`, `bench` and `rebase` compose a
differencing source against its parent; `measure` and `check` still
refuse one outright, like `map`. `instar info` composes no sector data
either way, so it has no wrong answer to give regardless of which of
those applies, and it is what a user reaches for when an operation that
does refuse declines to read the image. See the "VHD/VHDX differencing"
section of [quirks.md](/components/instar/quirks/) for the full per-op record.

`backing-filename-format` reports `vpc` or `vhdx` to match the child,
not the `qcow2` that field defaults to when no backing format is
recorded. A differencing image records no backing-format field of its
own, but SPEC(VHD) and SPEC(VHDX) both require a parent to be the same
format as its child, so the format is known without one.

**A reported parent is not a resolved parent.** `info` on its own
decodes a parent reference from the image's own header and prints what it
finds; it does not open the result. For VHD that reference is the parent
unicode name in the dynamic header, and only that -- the eight parent
locator entries beside it are parsed by the `vhd` crate but no read path
consults them, so a differencing VHD whose name field is blank reports no
parent even when its locator table names one. For VHDX, which has no name
field, it is the parent locator item.
`--chain` does resolve a VHD or VHDX parent and walk into it, under the
same allowlist and depth checks a qcow2 backing file gets — but only
when it can: whenever a parent cannot be resolved, the walk ends at the
last image it did resolve, and that image's own unresolved reference is
reported rather than the command erroring (see
[chain-discovery.md](/components/instar/chain-discovery/) for the reasons and the exact
stderr line). A parent locator is attacker-controlled
data — it can name an absolute path, a relative traversal, a UNC share
or a URL — so treat `backing-filename` on a differencing image as a
string the image claims, not a file instar has validated.

## Known limitations

### A Windows-convention parent has no "actual path"

`info` shows where a relative backing reference resolves, joined onto
the image's directory, as qemu-img does. A parent recorded as a Windows
absolute path -- a drive letter (`C:\images\parent.vhdx`) or anything
rooted at `\\` (a UNC share, or a `\\?\Volume{...}` path) -- is
absolute in its own convention and names no file on this host, so it
is treated as a POSIX absolute reference is: reported exactly as the
image records it, with no "actual path", and JSON's
`full-backing-filename` repeats it verbatim.

```
backing file: \\attacker\share\probe
```

It used to be joined onto the image's directory, producing an "actual
path" such as `/images/\\attacker\share\probe` that cannot exist
([issue #658](https://github.com/shakenfist/instar/issues/658)). This
applies to both differencing formats: a VHD parent unicode name and a
VHDX `absolute_win32_path` or `volume_path` locator key are all
reported as stored. A relative VHDX locator is rendered back into POSIX
convention, so `create -f vhdx -b parent.vhdx` followed by `info`
reports `parent.vhdx` and resolves correctly. A *relative* reference
already in Windows convention, such as a VHD parent name of
`..\parent.vhd`, is still joined onto the image directory without
being normalised. The full account, including which locator key `info`
prefers and why the relative case is rendered back, is the "VHD/VHDX
differencing" section of [quirks.md](/components/instar/quirks/).

For a qcow2 or VMDK backing reference, `info` and the reading operations
can still disagree. A name such as `C:\images\base.qcow2` is also a
legal POSIX file name, and chain discovery treats it as relative: if a
file of exactly that name sits in the image's directory, `convert` and
`compare` read it, while `info` reports the reference with no "actual
path". Making discovery refuse a Windows absolute reference instead
would change what those operations open, so it is left for its own
change ([issue #899](https://github.com/shakenfist/instar/issues/899)).
