# The hunk header format

```
@@ -86,11 +86,12 @@ jpeg_compression = {{ nova_spice_jpeg_compression }}
    │   │    │  │  └── function hint: decorative, never verified
    │   │    │  └───── lines this hunk occupies in the new file
    │   │    └──────── first line number in the new file
    │   └───────────── lines this hunk occupies in the old file
    └───────────────── first line number in the old file
```

The old count is context lines plus `-` lines. The new count is context lines
plus `+` lines. The text after the closing `@@` is a hint git adds for
readability and is not checked by anything.

## The rules that bite

Every one of these broke a real patch during development.

**A count of exactly 1 is elided.** `-1,1` is written `-1`. A recounter that
always emits the long form produces diffs that are correct but not byte
identical to git's, which makes round-trip testing useless.

**An empty range names the line before it.** A side with lines starts at its
first line, but an empty side starts at the line it comes after. So file
creation is `-0,0 +1,N` and deletion is its mirror image, `-1,N +0,0`. With
context lines that only happens at the ends of a file, but a patch made with
`git diff -U0` has empty sides anywhere: inserting two lines after line 3 is
`-3,0 +4,2`, and deleting line 8 is `-8 +7,0`. Recounting moves the new-side
start by one for each empty side; computing `oldStart + delta` for every hunk
silently shifts such a hunk by a line, and git applies it there without
complaint.

The offset assumes hunks come in order, as git writes them. In a patch
assembled by hand they may not, and the start it gives can then fall before
the first line; Hunky Dory reports such a header rather than writing it, and
the same goes for a hunk left with no lines on either side.

**A range that starts at 0 must be empty.** Line 0 only exists as "before the
first line", so `-0` with any count but 0 is invalid -- `-0 +1,6` is a corrupt
patch to git. Trailing blank lines after a creation are therefore never counted
into it (see [the ambiguous cases](/components/hunkydory/ambiguity/)). If the body still implies old
lines, Hunky Dory leaves the header alone and reports it rather than writing
it: the body has stopped being a creation, and the right start is not
something a recounter can guess.

**git's signature is not a deletion.** `git format-patch` ends its output with:

```
-- 
2.47.3
```

That `-- ` starts with a `-`, and a naive counter reads it as a deleted line.
It is only the signature when a version follows it, which no body line can.

**A deleted `-- ` line is not a file header.** A deleted SQL, Lua, Haskell or
Ada comment is a body line starting `--- `, which is how a file header starts
too. Ending the hunk there dropped the deletion and everything after it, and
git applied what was left without complaint. Where each file starts with a
line of its own -- `diff --git` from git, a `diff` line from `diff -r`,
`Index:` from svn and quilt -- the next file will too, and a `--- ` inside a hunk is
always body. A bare `diff -u` patch has nothing between files but `--- ` and
`+++ `, so there a lone `--- ` is body and the pair is a file header, unless a
body line follows it. That misreads a bare hunk ending in a deleted `-- ` line
and an added `++ ` one (see [the ambiguous cases](/components/hunkydory/ambiguity/)).

**A bare empty line is a context line.** git writes a blank context line as a
single space, but editors and mail paths strip trailing whitespace, so patches
in the wild contain genuinely empty lines in the middle of a hunk body. They
count on both sides.

**`\ No newline at end of file` counts on neither side.** It annotates the
line above it.

**Offsets accumulate within a file and reset between files.** The new-side
start of each hunk is its old-side start plus the running total of
`(additions - deletions)` from earlier hunks in the same file, adjusted by one
for an empty side as above. That total must reset at each file header or the
second file in a patch comes out shifted.

## Why the direction of an error matters

A header whose counts are **too large** makes git refuse the patch:

```
error: corrupt patch at line 25
```

Loud, immediate, easy to fix.

A header whose counts are **too small** is the dangerous one. git reads exactly
as many lines as the header declares and ignores the rest, so the hunk is
quietly truncated and the patch applies with the wrong content. Nothing warns
you.

This asymmetry is why `resolveCounts` falls back to counting *every* line when
it cannot tell: over-counting fails safe.
