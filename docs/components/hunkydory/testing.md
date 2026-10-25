# Testing

Five layers, each catching what the one before cannot.

| Command | What it runs | Where |
|---|---|---|
| `npm test` | Unit tests, and a fixed slice of the edit fuzzer over `test/fixtures/` | pre-commit and CI |
| `npm run corpus` | Every fixture and every patch in a corpus must round trip byte identically | by hand |
| `npm run fuzz` | The edit fuzzer, open ended, over the fixtures and the corpus | by hand |
| `tools/editor-test.sh` | Undo, redo, and the editor's recount, in a real VS Code | CI, through `tools/editor-test-docker.sh` |
| `tools/check-package-contents.sh` | What the `.vsix` would hold: every compiled module, and nothing else | CI |

## The corpus

```bash
npm run corpus                                    # the default corpus
HUNKYDORY_CORPUS=/path/to/_patches npm run corpus # another one
```

The corpus is a directory of real patches: `HUNKYDORY_CORPUS`, or by default
`../kerbside-patches/_patches` beside this checkout. That OpenStack set, from
`shakenfist/kerbside-patches`, is what the recounter was developed against,
and most of the edge cases in `src/diff.ts` were found by running over it
rather than by reasoning: file creations, deletions, format-patch signatures,
missing final newlines and blank context lines.

`test/fixtures/` is the committed half of the check, and always runs, corpus
or not. It holds what a real patch set does not supply, such as CRLF line
endings and the empty sides of a `-U0` patch. Each fixture is checked twice:
it must round trip, and it must also round trip after every count in it has
been scrambled. The second check is the one with teeth, since a recounter that
does not recognise a file at all passes the first perfectly. It also limits
what can be a fixture: one that turns on either of
[the two ambiguities](/components/hunkydory/ambiguity/)
would fail for a reason that is not a bug, because scrambling destroys the
declared counts they are resolved from. `test/corpus.ts` has the detail.

## The edit fuzzer

The unit tests and the corpus only see patches whose headers are already
right, or whose counts have all been scrambled at once. What the extension
meets is a correct patch someone has just edited, and that is where the bugs
have been. So the fuzzer (`test/fuzz.ts` and `test/fuzz/`) takes correct
patches, makes the edits a person might -- adding, deleting and retyping body
lines, mostly in the last hunk and in file creations and deletions; blank lines
after a hunk; a signature added or removed; the final newline toggled; CRLF --
and recounts. Some of the lines it adds look like something else: deleted,
`-- note` reads as the start of a file header, and `- ` as git's signature;
added, `++ note` reads as the rest of a file header.

It is judged from outside the rules under test wherever it can be:

- The edited bodies are known exactly, because each seed is read the way git
  reads it, by consuming as many lines as each header declares, and the edits
  are made to that structure rather than to text. A recounted header must
  match its body, give or take blank lines at its end where
  [the ambiguity](/components/hunkydory/ambiguity/)
  allows, or be refused if no valid header exists. A hunk made with `-U0`
  must leave its trailing blanks out. That one is held by rule: the old file
  the next checks build has no other blank for git to find, so git would
  place such a hunk right however it was counted.
- `git apply --numstat` must parse the whole recounted patch and agree on how
  many lines it adds and deletes.
- For each file, an old file is built from the recounted hunks, and
  `git apply` must apply them exactly where their headers say -- with no
  offset -- and produce the new file they describe. This is what checks the
  starts in a header without restating the rule for computing them.
- Recounting the result again changes nothing.

Where git has nothing true to say -- a header that was refused, a hunk that
changes nothing, which git calls corrupt however it is counted -- the git
checks stand aside, and `npm run fuzz` reports how often that happened. A
patch is set aside entirely, as ambiguous, where a hunk in a bare `diff -u`
file ends in what reads as a file header, which is read as one by design.

### Running it

```bash
npm run fuzz                                          # 2000 cases, fresh seed
HUNKYDORY_FUZZ_CASES=100000 npm run fuzz              # a soak
HUNKYDORY_FUZZ_SEED=7 HUNKYDORY_FUZZ_CASE=4242 npm run fuzz   # replay one case
```

A failure prints the command that replays it, the seed patch, the shortest
list of edits that still fails, and the edited patch. Turn it into a unit test
in `test/recount.test.ts` along with the fix.

`npm test` runs 400 cases with a fixed seed over the fixtures alone, so it
never depends on a corpus and a failure is always reproducible. That slice is
all CI runs; the corpus, and the variety of real patches it brings, is only in
`npm run fuzz` by hand.

## The editor tests

`test/editor/` holds what needs a real editor: the undo stack, and the
editor's own reading of a document. The second runs the fuzzer's edited
patches through the Recount command and requires exactly what `recountText`
makes of the same text. It is what would have caught
[#80](https://github.com/shakenfist/hunkydory/issues/80), where the editor
saw a line after the final newline that no other test could.

Locally, `tools/editor-test.sh` runs them against an installed VS Code
(`VSCODE_BIN`, default `/usr/share/code/code`) and needs a display. CI runs
`tools/editor-test-docker.sh` instead, which builds a container with a virtual
display and a VS Code release pinned by version and sha256. Renovate does not
update that pin; the script says how to bump it.

## The packaging check

`.vscodeignore` is an allow-list, so a module it fails to name is left out of
the `.vsix` silently: the build and the tests pass, and the extension fails at
`require()` time on a user's machine. What ships is decided by vsce's matcher,
which no unit test can reach, so `tools/check-package-contents.sh` asks vsce.
It checks both directions -- everything compiled from `src/` ships, including
a nested probe module it creates, and nothing else does. CI runs it on every
pull request; it needs `npm ci` first.
