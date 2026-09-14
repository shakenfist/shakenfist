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

## Differencing images

A differencing VHD (`disk_type == 4`) or differencing VHDX (`HasParent`
set) has its parent reported as a backing file, in both human
(`backing file: ...`) and `--output json` (`backing-filename` /
`full-backing-filename`) forms — the same fields qcow2's backing file
already uses. `instar info` is the deliberate exception to the refusal
every other read op (`convert`, `dd`, `compare`, `bench`, `check`,
`measure`) applies to a differencing source: `info` composes no sector
data, so it has no wrong answer to give, and it is what a user reaches
for when the rest of the tool declines to read the image. See the
"VHD/VHDX differencing" section of [quirks.md](/components/instar/quirks/) for the full
per-op record.

`backing-filename-format` reports `vpc` or `vhdx` to match the child,
not the `qcow2` that field defaults to when no backing format is
recorded. A differencing image records no backing-format field of its
own, but SPEC(VHD) and SPEC(VHDX) both require a parent to be the same
format as its child, so the format is known without one.

**A reported parent is not a resolved parent.** `info` decodes the
parent name and the parent locator entries from the image's own header
and prints what it finds; it does not open the result, and `--chain`
stops at the one image (see [chain-discovery.md](/components/instar/chain-discovery/)).
A parent locator is attacker-controlled data — it can name an absolute
path, a relative traversal, a UNC share or a URL — so treat
`backing-filename` on a differencing image as a string the image
claims, not a file instar has validated.

## Known limitations

### VHDX parent "actual path" can contain a literal backslash

A VHDX differencing parent's locator is Windows-shaped — e.g.
`.\vhdx-diff-parent.vhdx` — and `info`'s "actual path" resolution treats
any non-absolute backing-file string as a POSIX-relative path, joining
it onto the image's directory. Because the backslash is not a path
separator on Linux, the result is a single, literal filename containing
a backslash that cannot exist on the filesystem, e.g.:

```
backing file: .\vhdx-diff-parent.vhdx (actual path: /images/.\vhdx-diff-parent.vhdx)
```

The `backing file:` field itself is correct — it is exactly what the
VHDX header records — and qemu-img would print the same field
unconditionally if it could open a differencing VHDX at all. Fixing the
"actual path" resolution is path normalisation, which belongs to phase
11 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/), not this
output path. Suppressing the field instead of fixing the resolution
would paper over the underlying issue rather than close it.
