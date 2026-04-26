# Phase 1: Paste-as-keystrokes translator

## Overview

Build a pure-function translator that converts a `&str` into a
sequence of AT-keyboard scancode triples suitable for typing
that string on a US-QWERTY guest. No channel integration, no
GUI surface, no CLI flags — just the translator function and
comprehensive unit tests. This is the foundation that Phases
2-4 build on.

## Parent plan

[PLAN-paste-as-keystrokes.md](/components/ryll/plans/PLAN-paste-as-keystrokes/)

## Current state of the code

### Existing scancode infrastructure

The inputs channel at `ryll/src/channels/inputs.rs` already
has the load-bearing pieces:

**`make_scancode(base, release)` at line 573**: Encodes a base
scancode into the SPICE wire format. Normal keys use a single
byte; extended keys (base >= 0x100) get an E0 prefix. Release
codes have bit 7 set.

```rust
fn make_scancode(base: u32, release: bool) -> u32 {
    let code = if release { base | 0x80 } else { base };
    if base >= 0x100 {
        let sc = code & 0xFF;
        (sc << 8) | 0xE0
    } else {
        code
    }
}
```

**`key_to_scancode(key)` at line 596**: Maps `egui::Key` enum
variants to `(press_code, release_code)` pairs. Covers
letters A-Z, digits 0-9, function keys F1-F12, navigation
keys, arrows, and punctuation keys. Uses a `LazyLock<HashMap>`
for the mapping.

Key observation: `key_to_scancode` is keyed by **physical
key** (`egui::Key`), not by **character**. It has no concept
of "shifted" — the app's modifier tracking code
(`app.rs:1181-1209`) handles shift/ctrl/alt state changes
separately. The paste translator needs a different mapping:
from `char` to `(base_scancode, needs_shift)`.

### Base scancodes from the existing map

All scancodes needed for the translator already appear in
`SCANCODE_MAP`:

| Key | Base | Unshifted char | Shifted char |
|-----|------|----------------|--------------|
| Num1 | 0x02 | `1` | `!` |
| Num2 | 0x03 | `2` | `@` |
| Num3 | 0x04 | `3` | `#` |
| Num4 | 0x05 | `4` | `$` |
| Num5 | 0x06 | `5` | `%` |
| Num6 | 0x07 | `6` | `^` |
| Num7 | 0x08 | `7` | `&` |
| Num8 | 0x09 | `8` | `*` |
| Num9 | 0x0A | `9` | `(` |
| Num0 | 0x0B | `0` | `)` |
| Minus | 0x0C | `-` | `_` |
| Equals | 0x0D | `=` | `+` |
| Tab | 0x0F | `\t` | — |
| Q | 0x10 | `q` | `Q` |
| W | 0x11 | `w` | `W` |
| E | 0x12 | `e` | `E` |
| R | 0x13 | `r` | `R` |
| T | 0x14 | `t` | `T` |
| Y | 0x15 | `y` | `Y` |
| U | 0x16 | `u` | `U` |
| I | 0x17 | `i` | `I` |
| O | 0x18 | `o` | `O` |
| P | 0x19 | `p` | `P` |
| OpenBracket | 0x1A | `[` | `{` |
| CloseBracket | 0x1B | `]` | `}` |
| Enter | 0x1C | `\n` | — |
| A | 0x1E | `a` | `A` |
| S | 0x1F | `s` | `S` |
| D | 0x20 | `d` | `D` |
| F | 0x21 | `f` | `F` |
| G | 0x22 | `g` | `G` |
| H | 0x23 | `h` | `H` |
| J | 0x24 | `j` | `J` |
| K | 0x25 | `k` | `K` |
| L | 0x26 | `l` | `L` |
| Semicolon | 0x27 | `;` | `:` |
| Quote | 0x28 | `'` | `"` |
| Backtick | 0x29 | `` ` `` | `~` |
| Backslash | 0x2B | `\` | `|` |
| Z | 0x2C | `z` | `Z` |
| X | 0x2D | `x` | `X` |
| C | 0x2E | `c` | `C` |
| V | 0x2F | `v` | `V` |
| B | 0x30 | `b` | `B` |
| N | 0x31 | `n` | `N` |
| M | 0x32 | `m` | `M` |
| Comma | 0x33 | `,` | `<` |
| Period | 0x34 | `.` | `>` |
| Slash | 0x35 | `/` | `?` |
| Space | 0x39 | ` ` | — |

### Modifier scancodes (for reference — used in Phase 2)

From the modifier tracking at `app.rs:1181-1209`:

- Left Shift: base `0x2A`, press `0x2A`, release `0xAA`
- Left Ctrl: base `0x1D`, press `0x1D`, release `0x9D`
- Left Alt: base `0x38`, press `0x38`, release `0xB8`

### Existing test patterns

Tests in `inputs.rs:709-776` use `#[cfg(test)] mod tests`
with `use super::key_to_scancode` and `use eframe::egui`.
Each test is a small focused function asserting
`key_to_scancode(egui::Key::X) == Some((press, release))`.
The new translator tests should follow this same style.

## Design

### Public API

```rust
/// A single key event in a paste sequence.
///
/// `press` and `release` are SPICE wire-format scancodes
/// (output of `make_scancode`). `shift` indicates whether
/// Left Shift must be held for this character.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PasteKey {
    pub press: u32,
    pub release: u32,
    pub shift: bool,
}

/// Errors from paste text translation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PasteError {
    /// The input contains characters that cannot be
    /// represented as US-QWERTY scancodes.
    Unrepresentable {
        /// Number of unrepresentable characters.
        count: usize,
        /// Up to three sample characters for diagnostics.
        sample: Vec<char>,
    },
}

/// Translate a string into a sequence of scancode triples
/// for typing on a US-QWERTY keyboard.
///
/// Returns one `PasteKey` per typeable character. CRLF
/// sequences are collapsed to a single Enter. The input
/// is pre-validated: if any character cannot be mapped,
/// nothing is returned and the error reports which
/// characters failed.
///
/// This function does not enforce the 4096-character cap
/// (that is the caller's responsibility in Phase 2).
pub fn translate_paste(text: &str)
    -> Result<Vec<PasteKey>, PasteError>
```

### Internal mapping

A `char_to_scancode(c: char) -> Option<(u32, bool)>` helper
that returns `(base_scancode, needs_shift)` for a single
character. Implemented as a `match` statement (not a HashMap
— the character set is fixed and small, and `match` is
faster for this kind of dispatch). The base scancode values
are the same constants already in `SCANCODE_MAP`.

The `translate_paste` function:

1. Iterates over `text.chars()`, collapsing `\r\n` to a
   single `\n` (peek at the next char when `\r` is seen).
2. For each char, calls `char_to_scancode`. If `None`,
   records it in a set of unrepresentable characters.
3. If any unrepresentable characters were found, returns
   `PasteError::Unrepresentable` with the count and up to
   three samples.
4. Otherwise, maps each `(base, shift)` through
   `make_scancode` to produce `PasteKey { press, release,
   shift }`.

### Placement

Both `PasteKey`, `PasteError`, `translate_paste`, and
`char_to_scancode` go in `ryll/src/channels/inputs.rs`, next
to the existing `key_to_scancode` function. `PasteKey` and
`PasteError` are `pub`; `char_to_scancode` is private.

`make_scancode` is currently `fn` (private). It stays
private — `translate_paste` calls it internally.

## Implementation steps

### Step 1: Add `char_to_scancode` helper

Add a private function below `key_to_scancode` (after
line 696) that maps a `char` to `Option<(u32, bool)>` where
the tuple is `(base_scancode, needs_shift)`.

```rust
/// Map a character to its US-QWERTY AT scancode.
///
/// Returns `(base_scancode, needs_shift)` or `None` if the
/// character has no US-QWERTY representation.
fn char_to_scancode(c: char) -> Option<(u32, bool)> {
    match c {
        // Lowercase letters (unshifted)
        'a' => Some((0x1E, false)),
        'b' => Some((0x30, false)),
        'c' => Some((0x2E, false)),
        'd' => Some((0x20, false)),
        'e' => Some((0x12, false)),
        'f' => Some((0x21, false)),
        'g' => Some((0x22, false)),
        'h' => Some((0x23, false)),
        'i' => Some((0x17, false)),
        'j' => Some((0x24, false)),
        'k' => Some((0x25, false)),
        'l' => Some((0x26, false)),
        'm' => Some((0x32, false)),
        'n' => Some((0x31, false)),
        'o' => Some((0x18, false)),
        'p' => Some((0x19, false)),
        'q' => Some((0x10, false)),
        'r' => Some((0x13, false)),
        's' => Some((0x1F, false)),
        't' => Some((0x14, false)),
        'u' => Some((0x16, false)),
        'v' => Some((0x2F, false)),
        'w' => Some((0x11, false)),
        'x' => Some((0x2D, false)),
        'y' => Some((0x15, false)),
        'z' => Some((0x2C, false)),

        // Uppercase letters (shifted)
        'A' => Some((0x1E, true)),
        'B' => Some((0x30, true)),
        'C' => Some((0x2E, true)),
        'D' => Some((0x20, true)),
        'E' => Some((0x12, true)),
        'F' => Some((0x21, true)),
        'G' => Some((0x22, true)),
        'H' => Some((0x23, true)),
        'I' => Some((0x17, true)),
        'J' => Some((0x24, true)),
        'K' => Some((0x25, true)),
        'L' => Some((0x26, true)),
        'M' => Some((0x32, true)),
        'N' => Some((0x31, true)),
        'O' => Some((0x18, true)),
        'P' => Some((0x19, true)),
        'Q' => Some((0x10, true)),
        'R' => Some((0x13, true)),
        'S' => Some((0x1F, true)),
        'T' => Some((0x14, true)),
        'U' => Some((0x16, true)),
        'V' => Some((0x2F, true)),
        'W' => Some((0x11, true)),
        'X' => Some((0x2D, true)),
        'Y' => Some((0x15, true)),
        'Z' => Some((0x2C, true)),

        // Digits (unshifted)
        '0' => Some((0x0B, false)),
        '1' => Some((0x02, false)),
        '2' => Some((0x03, false)),
        '3' => Some((0x04, false)),
        '4' => Some((0x05, false)),
        '5' => Some((0x06, false)),
        '6' => Some((0x07, false)),
        '7' => Some((0x08, false)),
        '8' => Some((0x09, false)),
        '9' => Some((0x0A, false)),

        // Shifted digit-row symbols
        '!' => Some((0x02, true)),
        '@' => Some((0x03, true)),
        '#' => Some((0x04, true)),
        '$' => Some((0x05, true)),
        '%' => Some((0x06, true)),
        '^' => Some((0x07, true)),
        '&' => Some((0x08, true)),
        '*' => Some((0x09, true)),
        '(' => Some((0x0A, true)),
        ')' => Some((0x0B, true)),

        // Unshifted punctuation
        '-' => Some((0x0C, false)),
        '=' => Some((0x0D, false)),
        '[' => Some((0x1A, false)),
        ']' => Some((0x1B, false)),
        '\\' => Some((0x2B, false)),
        ';' => Some((0x27, false)),
        '\'' => Some((0x28, false)),
        '`' => Some((0x29, false)),
        ',' => Some((0x33, false)),
        '.' => Some((0x34, false)),
        '/' => Some((0x35, false)),

        // Shifted punctuation
        '_' => Some((0x0C, true)),
        '+' => Some((0x0D, true)),
        '{' => Some((0x1A, true)),
        '}' => Some((0x1B, true)),
        '|' => Some((0x2B, true)),
        ':' => Some((0x27, true)),
        '"' => Some((0x28, true)),
        '~' => Some((0x29, true)),
        '<' => Some((0x33, true)),
        '>' => Some((0x34, true)),
        '?' => Some((0x35, true)),

        // Whitespace
        ' ' => Some((0x39, false)),
        '\t' => Some((0x0F, false)),
        '\n' => Some((0x1C, false)),

        _ => None,
    }
}
```

### Step 2: Add `PasteKey`, `PasteError`, and `translate_paste`

Add the struct, enum, and function immediately after
`char_to_scancode`.

`translate_paste` must handle CRLF collapsing: when it sees
`\r` followed by `\n`, emit one Enter; when it sees a bare
`\r`, also emit Enter (treat `\r` as a line ending). Use
`text.chars().peekable()` to look ahead.

The pre-validation pass collects all unrepresentable
characters first (skipping `\r` — it's handled by the CRLF
logic, not by `char_to_scancode`). If any are found, return
the error without emitting anything.

### Step 3: Add unit tests

Add tests to the existing `#[cfg(test)] mod tests` block
at the end of `inputs.rs`. Required test cases:

1. **`paste_empty_string`**: `translate_paste("")` returns
   `Ok(vec![])`.

2. **`paste_lowercase_letters`**: `translate_paste("abc")`
   returns three `PasteKey`s with `shift: false` and the
   correct scancodes for a, b, c.

3. **`paste_uppercase_letters`**: `translate_paste("ABC")`
   returns three `PasteKey`s with `shift: true` and the
   same base scancodes as lowercase.

4. **`paste_mixed_case`**: `translate_paste("Hello")` — H
   shifted, e/l/l/o unshifted.

5. **`paste_digits`**: `translate_paste("0123456789")` —
   ten keys, all unshifted.

6. **`paste_shifted_digit_symbols`**: `translate_paste(
   "!@#$%^&*()")` — ten keys, all shifted, same base
   scancodes as digits 1-9, 0.

7. **`paste_unshifted_punctuation`**: `translate_paste(
   "-=[]\\;',./")` — correct base scancodes, all
   unshifted. (Note: the backtick is tested separately
   to avoid quoting issues.)

8. **`paste_shifted_punctuation`**: `translate_paste(
   "_+{}|:\"~<>?")` — same base scancodes as their
   unshifted counterparts, all shifted.

9. **`paste_backtick_and_tilde`**: test `` ` `` (unshifted)
   and `~` (shifted) separately.

10. **`paste_whitespace`**: `translate_paste("a b")` — a,
    space, b. Space is unshifted, base 0x39.

11. **`paste_tab`**: `translate_paste("a\tb")` — a, tab, b.
    Tab is unshifted, base 0x0F.

12. **`paste_newline`**: `translate_paste("a\nb")` — a,
    enter, b. Enter is unshifted, base 0x1C.

13. **`paste_crlf_collapsed`**: `translate_paste("a\r\nb")`
    returns three keys (a, enter, b), not four. The `\r\n`
    produces a single Enter.

14. **`paste_bare_cr`**: `translate_paste("a\rb")` — bare
    `\r` also produces Enter.

15. **`paste_non_ascii_rejected`**: `translate_paste("café")`
    returns `Err(PasteError::Unrepresentable { count: 1,
    sample: vec!['é'] })`.

16. **`paste_multiple_non_ascii`**: `translate_paste(
    "αβγδ hello")` returns error with count 4 and sample
    containing the first three Greek letters.

17. **`paste_all_printable_ascii`**: Iterate over every
    ASCII char from 0x20 (`' '`) through 0x7E (`'~'`) and
    assert `translate_paste` of that single character
    returns `Ok` with exactly one `PasteKey`. This is the
    comprehensive coverage test.

18. **`paste_scancode_values_match_key_to_scancode`**:
    For a representative set of characters (e.g. `'a'`,
    `'1'`, `' '`, `'\t'`, `'\n'`), verify that the
    `press`/`release` values from `translate_paste` match
    what `key_to_scancode` returns for the corresponding
    `egui::Key`. This cross-checks that the two mapping
    tables are consistent.

### Step 4: Run validation

```bash
pre-commit run --all-files
make test    # cargo test --workspace via Docker
```

Fix any rustfmt, clippy, or test failures.

## Files to modify

| File | Changes |
|------|---------|
| `ryll/src/channels/inputs.rs` | Add `char_to_scancode`, `PasteKey`, `PasteError`, `translate_paste`, and 18 test functions |

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1-3 | medium | sonnet | none | Implement `char_to_scancode`, `PasteKey`, `PasteError`, `translate_paste`, and all 18 unit tests in `ryll/src/channels/inputs.rs`. The complete character mapping table and test specifications are in this plan — follow them verbatim. Place new code after `key_to_scancode` (line 696) and new tests in the existing `mod tests` block (before the closing `}`). |
| 4 | low | haiku | none | Run `pre-commit run --all-files` and `make test`. Fix any issues. |

## Risks and mitigations

- **`make_scancode` is private**: `translate_paste` lives in
  the same module, so it can call `make_scancode` directly.
  No visibility change needed.

- **CRLF edge cases**: A string ending in `\r` (no following
  `\n`) should still produce Enter, not be silently dropped.
  Test 14 (`paste_bare_cr`) covers this.

- **Match exhaustiveness**: The `char_to_scancode` match
  uses a wildcard `_ => None` arm. Clippy won't complain.
  The comprehensive test (test 17) ensures every printable
  ASCII character is covered.

## Success criteria

- `translate_paste` converts every printable ASCII character
  (0x20-0x7E) plus `\t`, `\n`, `\r` to the correct scancode
  sequence.
- Shifted characters (uppercase, symbols) have `shift: true`.
- CRLF is collapsed to a single Enter.
- Non-ASCII input is rejected with `PasteError::Unrepresentable`
  carrying count and sample.
- All 18 unit tests pass.
- `pre-commit run --all-files` passes.
- `cargo test --workspace` passes.
- No other files are modified.

## Sub-agent brief

**Effort**: medium | **Model**: sonnet | **Isolation**: none

Add the paste-as-keystrokes translator to
`ryll/src/channels/inputs.rs`. Implementation details:

1. Add `char_to_scancode(c: char) -> Option<(u32, bool)>`
   after line 696 with the full US-QWERTY character-to-
   scancode mapping table from Step 1 of this plan.

2. Add `PasteKey` struct, `PasteError` enum, and
   `translate_paste(text: &str) -> Result<Vec<PasteKey>,
   PasteError>` function per the API in this plan's Design
   section. Use `text.chars().peekable()` for CRLF
   collapsing.

3. Add all 18 unit tests specified in Step 3 to the
   existing `mod tests` block.

4. Run `pre-commit run --all-files` and `make test` (Docker).
   Fix any issues.
