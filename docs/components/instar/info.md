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
decodes the parent name and the parent locator entries from the image's
own header and prints what it finds; it does not open the result.
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

### A Windows-convention parent makes "actual path" unresolvable

`info`'s "actual path" resolution treats any backing-file string that
is not POSIX-absolute as relative and joins it onto the image's
directory. A parent recorded in Windows convention therefore comes out
as a single literal filename containing backslashes, which cannot exist
on the filesystem:

```
backing file: \\attacker\share\probe (actual path: /images/\\attacker\share\probe)
```

This affects **both** differencing formats, not just VHDX: a VHD parent
unicode name and a VHDX `absolute_win32_path` or `volume_path` locator
key are all reported exactly as stored. A relative VHDX locator is the
one case that is *not* affected — `info` renders the `relative_path`
key back into POSIX convention, so `create -f vhdx -b parent.vhdx`
followed by `info` reports `parent.vhdx` and resolves correctly.

The `backing file:` field itself is correct in every case — it is what
the image's own header records, and reporting it is the point. Fixing
the "actual path" resolution is path normalisation, which belongs with
the composition work in
[PLAN-differencing.md](/components/instar/plans/PLAN-differencing/), not this output
path; suppressing the field instead would paper over the underlying
issue rather than close it. The full account, including which locator
key `info` prefers and why the relative case is rendered back, is the
"VHD/VHDX differencing" section of [quirks.md](/components/instar/quirks/).
