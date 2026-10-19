# The ambiguous cases

Consider the end of a hunk:

```diff
@@ -52,5 +52,6 @@ LABEL maintainer="{{ maintainer }}"
 {% endif %}

 {% set ironic_conductor_pip_packages = [
+    '/ironic[ansible,networking_generic_switch]',
     'ironic-staging-drivers',
 ] %}
 
diff --git a/releasenotes/notes/whatever.yaml b/releasenotes/notes/whatever.yaml
```

Count the body and you get 6 old lines and 7 new. The header says 5 and 6.
Neither is a typo: there is a blank line just before `diff --git`, and whether
it belongs to this hunk or is merely separating the two files cannot be decided
from the text.

git resolves it by construction. It reads a hunk by consuming exactly as many
lines as the header declares, then stops and ignores whatever follows. So the
header is not a *description* of the body that can be recomputed from it — it
is an instruction about how much of the body to read.

That is a problem for any tool recounting after the fact. patchutils'
`recountdiff` guesses wrong here, on several real patches.

## What we do

`resolveCounts` breaks the tie using the header it was handed. It computes the
counts for each possible number of ignored trailing blank lines, and if one of
those readings matches the declared counts exactly on **both** sides, that was
the author's intent and it is preserved.

Once the body has been edited no reading matches any more, and it falls back to
counting every line. That is deliberate: over-counting makes git reject the
patch loudly, while under-counting makes it silently apply the wrong content.
Failing safe means erring large.

Except for a hunk that cannot own its trailing blanks. git and diff write
context above a hunk wherever there is a line to write, so a hunk with no
context at all above its trailing blanks, starting below the first line, was
made with `git diff -U0`, which writes none below it either. A header such as
`-1,0 +2` starts below it too, since an empty side names the line before it,
and is the one place the header has to say so: its body can be the same as
that of an insertion before line 1 whose context is three blank lines. Its
trailing blanks are the gap before whatever follows, and they are left out
whatever the header says. Counted in, they would be all git had to place the
hunk by: an edited `-U0` insertion recounted that way was applied by
`git apply --unidiff-zero` three lines from where its header said, without a
word. Not even a header that already counts them in is kept, because an edit
can make that reading match the old header by chance.

A hunk whose only context is blank lines, or one at the top of the file, may
own its trailing blanks, and the usual rule stands. Leaving them out would be
the silent mistake there instead: with context above a hunk and none below,
git and GNU patch insist that it match at the end of the file, and apply it
there if the end of the file happens to fit.

The exception is a hunk whose old side starts at 0, as a file creation's does.
That side must be empty, so a reading that counts a trailing blank into it is
not a reading at all, and the fallback takes the first one that leaves it
empty. Over-counting is only the safe direction while the result is still a
header git can parse.

Nor is there any ambiguity right before another hunk of the same file. git
reads every line up to the next `@@` as part of the hunk before it, and stops
at nothing in between, so a blank there is in the hunk whatever the old header
said. Without this, a blank typed at the end of such a hunk was left out by the
tie-break -- the old header still matched without it -- and git rejected the
patch with `patch fragment without header`.

That holds only for a hunk with context of its own. In a hunk with none, as in
a patch made with `git diff -U0`, or whose only context is itself blank, the
counted blank would be all the context git had, and an insertion would go
silently at the first run of blank lines it came to. A deleted line pins a
hunk too, but the rule asks for context and goes no further than it must:
there the old reading stands, and git rejects the patch, which is the better
way to be wrong.

A hunk with no context at all, which cannot own the blank as above, is
reported instead of recounted. The blank belongs to neither hunk: counted
in, git places the hunk by it alone, and left out, git rejects the patch while
GNU patch skips every later hunk of the file without a word. The thing to fix
is the blank line.

Even with context, counting the blank in means the hunk only fits where the
file has a blank line after it, so git searches for somewhere that does. That
is safe exactly as far as the rest of the hunk's context is distinctive, which
is why a stray blank is still best deleted rather than left to a recount.

In practice this is why trailing blanks never get a patch git accepts
rewritten, unless it was made with `-U0`: the tie-break reproduces whatever the
file already said. An edited one gets a header that is, at worst,
conservative, wherever the hunk could own its blanks at all.

## Lines that read as a file header

A rarer case, in rarer patches. A deleted `-- ` line, which is how SQL, Lua,
Haskell and Ada comments start, is a body line beginning `--- `; an added
`++ ` line begins `+++ `. Together they are what a file header looks like.

Most patches settle it by structure. git writes `diff --git` before every
file, `diff -r` writes a `diff` line, svn, quilt and CVS write `Index:`, bzr
`===` and Perforce `====`, so in a patch whose files start that way the next
file will too, and that line ends the hunk before it. A `--- ` inside a hunk
there is always body. Only lines straight before a file's `--- ` count, so a
commit message that happens to start a line with `Index:` does not.

A bare `diff -u` patch has nothing between its files but the header itself.
There a lone `--- ` is body, and the pair is a file header unless a body line
follows it after any blank lines -- so a file whose hunks were all deleted, or
a header still being typed, is not read into the hunk above.

That gets one case wrong: a bare hunk that ends in a deleted `-- ` line and an
added `++ ` one, at the end of the file or right before the next hunk, which
takes `-U0` or a change to a file's last line:

```diff
@@ -2 +2 @@
--- old
+++ new
@@ -5 +5 @@
```

git reads those two as body, because it goes by the counts. Hunky Dory reads
them as a header and recounts the hunk without them, or reports it if that
leaves the hunk empty. git then takes them for a file header too. With
another hunk after them, it applies that hunk to the `+++ ` file if there is
one and the hunk fits there, and fails if not; with none, it applies the rest
of the patch without them. Both can be silent, and both need an added line
that starts `++ `, which is rare outside a hand-made test.

The declared counts could settle it, as they do for blanks, but they are
deliberately not asked. In live mode they can be counts Hunky Dory wrote a
moment ago: while a file header is being retyped, a lone `--- ` is a deleted
line, the hunk above is recounted to include it, and once the `+++ ` is back
the counts would agree that it belongs there. A deleted line that one edit to
a hunk left one short on each side would agree the same way. Either would read
the next file's header into the hunk above, which is a much commoner way to be
wrong than the one above.

A patch that mixes the two, with a `diff --git` file followed by a bare one,
reads the bare file's header as the last lines of the hunk before it; git then
rejects the patch.

## What cannot be fixed this way

If you delete a *context* line from a hunk, nothing in the resulting text
records that it was ever there. Any recounter will happily produce a
self-consistent header for a patch that no longer matches the source.

The only defence is a reference copy from before the edit, which is what
patchutils' `rediff` uses. An editor extension has one available in principle —
the undo history — but does not use it today.
