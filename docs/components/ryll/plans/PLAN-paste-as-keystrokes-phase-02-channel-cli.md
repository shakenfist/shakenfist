# Phase 2: Channel integration and CLI flags

## Overview

Wire the Phase 1 translator into the inputs channel as a
cooperative, non-blocking paste state machine. Add CLI flags
(`--enable-paste-as-keystrokes`, `--paste-text`,
`--paste-char-delay-ms`) and plumb them through the full call
chain from `config.rs` to `InputsChannel`. In headless mode,
trigger the paste once after the connection is established.
Emit `ChannelEvent::PasteCompleted` when the sequence
finishes.

## Parent plan

[PLAN-paste-as-keystrokes.md](/components/ryll/plans/PLAN-paste-as-keystrokes/)

## Design

### The cooperative paste problem

The inputs channel event loop (`inputs.rs:87-200`) uses a
two-arm `tokio::select!`: one arm reads from the SPICE
server, the other drains `InputEvent`s from the UI. If a
paste blocked this loop for the duration of the sequence
(4096 chars × 16 ms = ~65 seconds at maximum), the channel
could not process server messages or real-time mouse/keyboard
input.

The solution is a **timer-driven paste state machine** added
as a conditional third arm in the `select!` loop. When a
paste is active, a `tokio::time::sleep_until` future fires on
schedule, the handler sends one sub-step worth of key events,
advances the state, and yields back to the loop. Between
firings, the other two arms run normally.

### Per-character event sequence

For each character in the translated paste:

1. If `shift` is true: send `KeyDown(0x2A)` (Left Shift)
2. Send `KeyDown(press)`
3. **Sleep half the inter-character delay**
4. Send `KeyUp(release)`
5. If `shift` is true: send `KeyUp(0xAA)` (Left Shift)
6. **Sleep the remaining half of the delay**

The half-delay between press and release models realistic
key timing. A bootloader or BIOS that polls key state once
per frame could miss a press/release pair sent in the same
TCP segment.

This maps to a two-phase sub-step per character:

```
PressPhase:  send shift-down (if needed) + key-down → sleep half delay
ReleasePhase: send key-up + shift-up (if needed) → sleep half delay
```

### Modifier state save/restore

The master plan (Q4) requires releasing held modifiers before
the paste and restoring them afterward. The trigger gesture
(`Ctrl+Alt+V` in Phase 3, or the CLI flag) means Ctrl and
Alt are likely held when the paste starts. Typing characters
with Ctrl held would produce control sequences, not text.

The `InputsChannel` does not currently track modifier state.
Phase 2 adds modifier tracking by observing `KeyDown`/`KeyUp`
events as they flow through `handle_input_event`:

- Left Ctrl (`0x1D`): set/clear `ctrl_held`
- Left Shift (`0x2A`): set/clear `shift_held`
- Left Alt (`0x38`): set/clear `alt_held`

At paste start:
1. Save `(ctrl_held, shift_held, alt_held)` into `PasteState`.
2. Send `KeyUp` for each modifier that was held.
3. Clear the tracking flags.

At paste end (after all characters typed):
1. Re-send `KeyDown` for each modifier that was saved.
2. Restore the tracking flags.

### The 4096-character cap

A `PASTE_MAX_CHARS` constant (4096) in `inputs.rs` enforces
the hard cap from Q5. When `PasteText` is received, if the
text exceeds the cap, it is truncated and a `warn!` line is
emitted citing the requested vs actual character counts. The
truncated text is still typed — it is not an error.

### PasteText validation and error handling

When `PasteText` arrives in `handle_input_event`:

1. If `!self.enable_paste`: log a `warn!` and return (gate).
2. If a paste is already in progress: log a `warn!` and
   return (no queueing).
3. Truncate to `PASTE_MAX_CHARS` if needed (warn).
4. Call `translate_paste()`. On `PasteError::Unrepresentable`,
   log an `error!` with the count and sample, emit
   `ChannelEvent::PasteFailed` (see below), and return.
5. If translation succeeds, save/release modifiers and
   initialise the `PasteState`.

### PasteFailed event

In addition to `PasteCompleted`, add a `PasteFailed { reason:
String }` variant to `ChannelEvent`. This surfaces translation
errors to the GUI (Phase 3 will show an error dialog) and to
the headless event loop (which will log the error and — per
Q6 — should cause a non-zero exit for scripted use).

### CLI flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--enable-paste-as-keystrokes` | bool | false | Master gate for the feature |
| `--paste-text TEXT` | Option\<String\> | None | String to type in headless mode |
| `--paste-char-delay-ms N` | u32 | 16 | Inter-character delay in ms |

Passing `--paste-text` implies `--enable-paste-as-keystrokes`
(the effective enable flag is `args.enable_paste_as_keystrokes
|| args.paste_text.is_some()`).

### Headless paste trigger

In `app.rs::run_headless()`, if `paste_text` is `Some`, clone
`input_tx` before moving it into the cadence task, then spawn
a paste trigger task that:

1. Waits 1 second (for the inputs channel to connect).
2. Sends `InputEvent::PasteText { text, char_delay_ms }` via
   `input_tx.send().await` (blocking send — will succeed once
   the receiver starts consuming).

This follows the cadence-task precedent at `app.rs:2912`.

### Headless non-zero exit on PasteFailed

In the headless event loop, handle `PasteFailed` by setting
a flag and breaking out of the event loop. After the loop,
if the flag is set, return `Err(...)` so `main` exits with
a non-zero code. This satisfies Q6's requirement that
headless scripted use signals failure to the test harness.

## Current code state and exact locations

### Files and line numbers

| File | What | Lines |
|------|------|-------|
| `channels/mod.rs` | `InputEvent` enum | 196-216 |
| `channels/mod.rs` | `ChannelEvent` enum | 22-194 |
| `channels/inputs.rs` | `InputsChannel` struct | 30-48 |
| `channels/inputs.rs` | Constructor | 49-77 |
| `channels/inputs.rs` | `run()` event loop | 80-204 |
| `channels/inputs.rs` | `handle_input_event()` | 336-522 |
| `channels/inputs.rs` | `translate_paste()` (Phase 1) | 854-895 |
| `channels/inputs.rs` | `PASTE_MAX_CHARS` | (new) |
| `config.rs` | `Args` struct | 12-80 |
| `main.rs` | `run_headless()` | 163-186 |
| `main.rs` | `run_gui()` | 188-224 |
| `app.rs` | `RyllApp` struct | 194-332 |
| `app.rs` | `RyllApp::new()` | 382-517 |
| `app.rs` | `run_connection()` | 2589-2826 |
| `app.rs` | `run_headless()` | 2828-3010 |
| `app.rs` | `process_events()` | 582-899 |

### Modifier scancodes

| Modifier | Base | KeyDown | KeyUp |
|----------|------|---------|-------|
| Left Ctrl | 0x1D | 0x1D | 0x9D |
| Left Shift | 0x2A | 0x2A | 0xAA |
| Left Alt | 0x38 | 0x38 | 0xB8 |

## Implementation steps

### Step 1: Add new event variants to `channels/mod.rs`

Add `PasteText` to `InputEvent` (after `MouseUp`):

```rust
/// Paste a string as synthetic keystrokes (US-QWERTY).
PasteText { text: String, char_delay_ms: u32 },
```

Add `PasteCompleted` and `PasteFailed` to `ChannelEvent`
(after the `Latency` variant, around line 155):

```rust
/// Paste-as-keystrokes sequence completed.
PasteCompleted { chars: usize, elapsed_ms: u64 },

/// Paste-as-keystrokes failed (unrepresentable characters).
PasteFailed { reason: String },
```

### Step 2: Add CLI flags to `config.rs`

Add three fields to the `Args` struct (after the `pedantic_dir`
field, around line 78):

```rust
/// Enable paste-as-keystrokes fallback for guests without vdagent
#[arg(long)]
pub enable_paste_as_keystrokes: bool,

/// String to type as keystrokes in headless mode (implies --enable-paste-as-keystrokes)
#[arg(long = "paste-text")]
pub paste_text: Option<String>,

/// Inter-character delay for paste-as-keystrokes in milliseconds
#[arg(long = "paste-char-delay-ms", default_value_t = 16)]
pub paste_char_delay_ms: u32,
```

### Step 3: Add paste state machine to `channels/inputs.rs`

#### 3a: Add constants and types

After the existing `use` block, add:

```rust
/// Maximum characters for a single paste-as-keystrokes sequence.
const PASTE_MAX_CHARS: usize = 4096;
```

Before or after `PasteKey`/`PasteError`, add:

```rust
/// Sub-step within a single character of a paste sequence.
#[derive(Debug, Clone, Copy)]
enum PasteSubStep {
    /// Send shift-down (if needed) + key-down, then wait half the delay.
    Press,
    /// Send key-up + shift-up (if needed), then wait the remaining half.
    Release,
}

/// State for an in-progress paste-as-keystrokes sequence.
#[derive(Debug)]
struct PasteState {
    keys: Vec<PasteKey>,
    index: usize,
    sub_step: PasteSubStep,
    half_delay: Duration,
    start: Instant,
    next_fire: Instant,
    /// Modifier state saved at paste start, restored at paste end.
    saved_ctrl: bool,
    saved_shift: bool,
    saved_alt: bool,
}
```

#### 3b: Add fields to `InputsChannel` struct

```rust
enable_paste: bool,
paste_char_delay_ms: u32,
ctrl_held: bool,
shift_held: bool,
alt_held: bool,
paste_state: Option<PasteState>,
```

#### 3c: Update constructor

Add `enable_paste: bool` and `paste_char_delay_ms: u32`
parameters to `InputsChannel::new()`. Initialise the new
fields (`ctrl_held`, `shift_held`, `alt_held` all `false`;
`paste_state` is `None`).

#### 3d: Track modifier state in `handle_input_event`

In the `KeyDown` arm (around line 340), after recording the
event, add:

```rust
match scancode {
    0x1D => self.ctrl_held = true,
    0x2A => self.shift_held = true,
    0x38 => self.alt_held = true,
    _ => {}
}
```

In the `KeyUp` arm (around line 362), after recording the
event, add:

```rust
match scancode {
    0x9D => self.ctrl_held = false,
    0xAA => self.shift_held = false,
    0xB8 => self.alt_held = false,
    _ => {}
}
```

#### 3e: Add `PasteText` match arm

Add a new arm in `handle_input_event` for `PasteText`:

```rust
InputEvent::PasteText { text, char_delay_ms } => {
    if !self.enable_paste {
        warn!("inputs: paste-as-keystrokes not enabled, ignoring");
        return Ok(());
    }
    if self.paste_state.is_some() {
        warn!("inputs: paste already in progress, ignoring");
        return Ok(());
    }

    // Enforce character cap
    let mut text = text;
    if text.chars().count() > PASTE_MAX_CHARS {
        let original_len = text.chars().count();
        text = text.chars().take(PASTE_MAX_CHARS).collect();
        warn!(
            "inputs: paste truncated from {} to {} characters",
            original_len, PASTE_MAX_CHARS
        );
    }

    // Translate
    let keys = match translate_paste(&text) {
        Ok(k) => k,
        Err(PasteError::Unrepresentable { count, sample }) => {
            let sample_str: String = sample.iter()
                .map(|c| format!("U+{:04X}", *c as u32))
                .collect::<Vec<_>>()
                .join(", ");
            let reason = format!(
                "cannot paste: {} unrepresentable character(s): {}",
                count, sample_str
            );
            error!("inputs: {}", reason);
            self.event_tx
                .send(ChannelEvent::PasteFailed { reason })
                .await
                .ok();
            self.repaint_notify.notify_one();
            return Ok(());
        }
    };

    if keys.is_empty() {
        self.event_tx
            .send(ChannelEvent::PasteCompleted {
                chars: 0,
                elapsed_ms: 0,
            })
            .await
            .ok();
        self.repaint_notify.notify_one();
        return Ok(());
    }

    let delay = Duration::from_millis(char_delay_ms as u64);
    info!(
        "inputs: starting paste of {} characters (delay={}ms)",
        keys.len(),
        char_delay_ms
    );

    // Save and release held modifiers
    let saved_ctrl = self.ctrl_held;
    let saved_shift = self.shift_held;
    let saved_alt = self.alt_held;

    if self.ctrl_held {
        self.handle_input_event(InputEvent::KeyUp(0x9D)).await?;
    }
    if self.shift_held {
        self.handle_input_event(InputEvent::KeyUp(0xAA)).await?;
    }
    if self.alt_held {
        self.handle_input_event(InputEvent::KeyUp(0xB8)).await?;
    }

    let now = Instant::now();
    self.paste_state = Some(PasteState {
        keys,
        index: 0,
        sub_step: PasteSubStep::Press,
        half_delay: delay / 2,
        start: now,
        next_fire: now, // fire immediately
        saved_ctrl,
        saved_shift,
        saved_alt,
    });
}
```

Note: the modifier release calls `self.handle_input_event`
recursively — this reuses the existing `KeyUp` arm which
sends on the wire and updates the tracking flags. This is
safe because the `KeyUp` arm does not recurse.

#### 3f: Add `advance_paste` method

```rust
/// Advance the paste state machine by one sub-step.
async fn advance_paste(&mut self) -> Result<()> {
    let state = self.paste_state.as_mut().unwrap();
    let key = state.keys[state.index];

    match state.sub_step {
        PasteSubStep::Press => {
            if key.shift {
                self.send_key_down(0x2A).await?;
            }
            self.send_key_down(key.press).await?;
            let state = self.paste_state.as_mut().unwrap();
            state.sub_step = PasteSubStep::Release;
            state.next_fire = Instant::now() + state.half_delay;
        }
        PasteSubStep::Release => {
            self.send_key_up(key.release).await?;
            if key.shift {
                self.send_key_up(0xAA).await?;
            }

            let state = self.paste_state.as_mut().unwrap();
            state.index += 1;

            if state.index >= state.keys.len() {
                // Paste complete
                let chars = state.keys.len();
                let elapsed_ms = state.start.elapsed().as_millis() as u64;

                // Restore modifiers
                let saved_ctrl = state.saved_ctrl;
                let saved_shift = state.saved_shift;
                let saved_alt = state.saved_alt;

                self.paste_state = None;

                if saved_ctrl {
                    self.send_key_down(0x1D).await?;
                    self.ctrl_held = true;
                }
                if saved_shift {
                    self.send_key_down(0x2A).await?;
                    self.shift_held = true;
                }
                if saved_alt {
                    self.send_key_down(0x38).await?;
                    self.alt_held = true;
                }

                info!(
                    "inputs: paste complete: {} chars in {}ms",
                    chars, elapsed_ms
                );
                self.event_tx
                    .send(ChannelEvent::PasteCompleted {
                        chars,
                        elapsed_ms,
                    })
                    .await
                    .ok();
                self.repaint_notify.notify_one();
            } else {
                state.sub_step = PasteSubStep::Press;
                state.next_fire =
                    Instant::now() + state.half_delay;
            }
        }
    }

    Ok(())
}
```

The `advance_paste` method needs small helper methods to
send key events without going through `handle_input_event`
(to avoid triggering the event recording and modifier
tracking for synthetic paste events):

```rust
async fn send_key_down(&mut self, scancode: u32) -> Result<()> {
    let mut payload = Vec::new();
    KeyEvent { scancode }.write(&mut payload)?;
    let msg = make_message(inputs_client::KEY_DOWN, &payload);
    self.send_with_log(inputs_client::KEY_DOWN, &msg).await
}

async fn send_key_up(&mut self, scancode: u32) -> Result<()> {
    let mut payload = Vec::new();
    KeyEvent { scancode }.write(&mut payload)?;
    let msg = make_message(inputs_client::KEY_UP, &payload);
    self.send_with_log(inputs_client::KEY_UP, &msg).await
}
```

#### 3g: Add third arm to `select!` loop

In `run()`, add a conditional third arm to the `select!`
block at line 120. The arm must be placed **inside** the
existing `select!` block, as a peer to the read and input
arms:

```rust
// Paste state machine: fire when the next step is due.
_ = async {
    if let Some(ref state) = self.paste_state {
        tokio::time::sleep_until(
            tokio::time::Instant::from_std(state.next_fire)
        ).await;
    } else {
        // No active paste — park this future indefinitely.
        std::future::pending::<()>().await;
    }
} => {
    self.advance_paste().await?;
}
```

**Borrow-checker note**: The existing `select!` macro borrows
`self` fields into local variables before the macro call
(lines 89-94). The paste arm borrows `self.paste_state`
immutably in the future (for `next_fire`), then calls
`self.advance_paste()` mutably in the handler. This should
work because `select!` drops the losing futures before
executing the winning handler. If the borrow checker
complains, extract `next_fire` into a local `Option<Instant>`
before the `select!` and use that instead:

```rust
let paste_next = self.paste_state.as_ref().map(|s| s.next_fire);

tokio::select! {
    // ... existing arms ...

    _ = async {
        match paste_next {
            Some(t) => tokio::time::sleep_until(
                tokio::time::Instant::from_std(t)
            ).await,
            None => std::future::pending::<()>().await,
        }
    } => {
        self.advance_paste().await?;
    }
}
```

#### 3h: Remove `#[allow(dead_code)]` from Phase 1 items

Remove the `#[allow(dead_code)]` attributes from `PasteKey`,
`PasteError`, `char_to_scancode`, and `translate_paste` — they
are now used by the `PasteText` handler.

### Step 4: Plumb CLI flags through the call chain

This step threads the new config through every function
signature in the chain. The changes are mechanical but
numerous.

#### 4a: `main.rs::run_headless()`

Add parameters. Extract from `args`:

```rust
fn run_headless(
    config: Config,
    args: &Args,
    virtual_disks: Vec<VirtualDiskConfig>,
    share_dir: Option<ShareDirConfig>,
    capture: Option<Arc<CaptureSession>>,
    pedantic_config: Option<PedanticConfig>,
) -> Result<()> {
```

Pass to `app::run_headless()`:

```rust
app::run_headless(
    config,
    args.cadence,
    args.paste_text.clone(),
    args.paste_char_delay_ms,
    args.enable_paste_as_keystrokes || args.paste_text.is_some(),
    virtual_disks,
    share_dir,
    capture,
    args.monitors,
    pedantic_config,
)
```

Actually, a cleaner approach: since `run_headless` already
takes `args: &Args`, just pass the whole `&Args` reference
instead of extracting individual fields (cadence is already
extracted this way). But the async function in `app.rs` needs
owned values, and `Args` doesn't implement `Clone`. So
extract the three new fields alongside `cadence` and
`monitors`.

#### 4b: `main.rs::run_gui()`

Similarly, extract the paste fields and pass to
`RyllApp::new()`.

#### 4c: `app.rs::run_headless()`

Add parameters to the signature:

```rust
pub async fn run_headless(
    config: Config,
    cadence: bool,
    paste_text: Option<String>,
    paste_char_delay_ms: u32,
    enable_paste: bool,
    virtual_disks: Vec<VirtualDiskConfig>,
    share_dir: Option<ShareDirConfig>,
    capture: Option<Arc<CaptureSession>>,
    monitors: u8,
    pedantic_config: Option<PedanticConfig>,
) -> Result<()>
```

Pass `enable_paste` and `paste_char_delay_ms` through to
`run_connection()`.

Add paste trigger task after the cadence task block
(around line 2923). Clone `input_tx` before the cadence
block so both tasks can use it:

```rust
let paste_input_tx = input_tx.clone();
// ... existing cadence_handle block uses input_tx ...

let paste_handle = if let Some(text) = paste_text {
    let delay_ms = paste_char_delay_ms;
    Some(tokio::spawn(async move {
        // Wait for the inputs channel to be ready.
        tokio::time::sleep(Duration::from_secs(1)).await;
        let _ = paste_input_tx
            .send(InputEvent::PasteText {
                text,
                char_delay_ms: delay_ms,
            })
            .await;
    }))
} else {
    None
};
```

Add `PasteCompleted` and `PasteFailed` handling in the
headless event loop (inside the `match event` block):

```rust
ChannelEvent::PasteCompleted { chars, elapsed_ms } => {
    info!(
        "headless: paste complete: {} chars in {}ms",
        chars, elapsed_ms
    );
}
ChannelEvent::PasteFailed { reason } => {
    error!("headless: paste failed: {}", reason);
    paste_failed = true;
}
```

Add a `paste_failed` flag (initialised to `false` before
the event loop). After the loop, if `paste_failed` is true,
return an error:

```rust
if paste_failed {
    anyhow::bail!("paste-as-keystrokes failed");
}
```

Abort the paste handle in the cleanup section (alongside
cadence):

```rust
if let Some(handle) = paste_handle {
    handle.abort();
}
```

#### 4d: `app.rs::RyllApp::new()`

Add `enable_paste: bool` and `paste_char_delay_ms: u32`
parameters. Store them on the `RyllApp` struct (new fields).
Pass them through to `run_connection()`.

The stored fields will be used in Phase 3 for the GUI button
(gating visibility and providing the delay value).

#### 4e: `app.rs::run_connection()`

Add `enable_paste: bool` and `paste_char_delay_ms: u32`
parameters. Pass them to `InputsChannel::new()` at line
2726.

#### 4f: `app.rs::process_events()`

Add match arms for `PasteCompleted` and `PasteFailed` in the
GUI event processing. Use the `bug_status_message` pattern
for transient display:

```rust
ChannelEvent::PasteCompleted { chars, elapsed_ms } => {
    info!(
        "app: paste complete: {} chars in {}ms",
        chars, elapsed_ms
    );
    self.bug_status_message = Some((
        format!("Pasted {} chars ({}ms)", chars, elapsed_ms),
        Instant::now(),
    ));
}
ChannelEvent::PasteFailed { reason } => {
    error!("app: paste failed: {}", reason);
    self.bug_status_message = Some((
        format!("Paste failed: {}", reason),
        Instant::now(),
    ));
}
```

Note: reusing `bug_status_message` is expedient for Phase 2.
Phase 3 may introduce a separate `paste_status_message` or
the notifications plan may absorb both. For now, the
transient message slot serves both bug reports and paste
status.

### Step 5: Run validation

```bash
pre-commit run --all-files
make test    # cargo test --workspace via Docker
```

Fix any rustfmt, clippy, or test failures.

## Files to modify

| File | Changes |
|------|---------|
| `ryll/src/channels/mod.rs` | Add `PasteText` to `InputEvent`; add `PasteCompleted` and `PasteFailed` to `ChannelEvent` |
| `ryll/src/channels/inputs.rs` | Add paste state machine, modifier tracking, `PASTE_MAX_CHARS`, `PasteState`, `advance_paste`, `send_key_down/up` helpers, third `select!` arm, `PasteText` handler; remove `#[allow(dead_code)]` from Phase 1 items |
| `ryll/src/config.rs` | Add three CLI flags to `Args` |
| `ryll/src/main.rs` | Thread paste config through `run_headless` and `run_gui` |
| `ryll/src/app.rs` | Add params to `run_headless`, `RyllApp::new`, `run_connection`; add fields to `RyllApp` struct; add paste trigger task in headless; handle `PasteCompleted`/`PasteFailed` in `process_events` and headless event loop |

## Step-level guidance

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 1 | low | sonnet | none | Add three new enum variants to `channels/mod.rs` |
| 2 | low | sonnet | none | Add three CLI flags to `config.rs` |
| 3 | high | opus | none | Implement the paste state machine in `inputs.rs` — the state types, modifier tracking, `PasteText` handler, `advance_paste`, helper methods, and the third `select!` arm. This is the load-bearing step and requires careful attention to the borrow checker and the `select!` macro's borrowing rules. |
| 4 | medium | sonnet | none | Plumb CLI flags through the full call chain: `main.rs` → `app.rs` (headless + GUI) → `run_connection` → `InputsChannel::new`. Add paste trigger task in headless. Handle `PasteCompleted`/`PasteFailed` in both GUI and headless event loops. |
| 5 | low | haiku | none | Run `pre-commit run --all-files` and `make test`. Fix issues. |

**Recommended execution**: Steps 1-4 as a single sub-agent
invocation (opus, high effort) since the changes are
interdependent — step 3 depends on the types from step 1,
and step 4 depends on both. Running them as a single agent
avoids partial compilation states.

## Risks and mitigations

- **Borrow checker in `select!`**: The paste arm borrows
  `self.paste_state` immutably in the future, then calls
  `&mut self` in the handler. Extract `next_fire` into a
  local variable before the `select!` to avoid this.

- **Recursive `handle_input_event`**: The `PasteText` arm
  calls `handle_input_event(KeyUp(...))` to release
  modifiers. This is safe because `KeyUp` does not recurse.
  Alternatively, call `send_key_up` directly instead.

- **`advance_paste` re-borrows `self.paste_state`**: After
  calling `self.send_key_down()` (which borrows `&mut self`),
  re-borrow `self.paste_state.as_mut()` to update the state.
  This is fine because the send methods don't touch
  `paste_state`.

- **Headless exit code**: The `paste_failed` flag must be
  checked after the event loop exits for any reason (Ctrl-C,
  disconnect, or normal completion). If paste failed and then
  the connection disconnected, the error should still
  propagate.

- **Cadence + paste coexistence**: Both tasks need
  `input_tx`. Clone it before moving into the cadence task.
  In practice, `--cadence` and `--paste-text` are unlikely
  to be combined, but the code must handle it.

## Success criteria

- `--paste-text "hello"` in headless mode types "hello" into
  the guest as keystrokes, one per 16ms (default delay).
- `--paste-text "café"` in headless mode logs an error and
  exits non-zero (non-ASCII rejection).
- `--paste-text` with a >4096 character string truncates and
  warns.
- `--paste-char-delay-ms 50` increases the inter-character
  delay.
- `--paste-text` implies `--enable-paste-as-keystrokes` (no
  need to pass both).
- `PasteCompleted` is emitted and logged in both GUI and
  headless.
- `PasteFailed` is emitted, logged, and causes non-zero exit
  in headless.
- The inputs channel remains responsive to real-time
  mouse/keyboard input during a paste.
- Modifier state is saved and restored around the paste.
- `pre-commit run --all-files` passes.
- `cargo test --workspace` passes.

## Sub-agent brief

**Effort**: high | **Model**: opus | **Isolation**: none

Implement Steps 1-5 of this plan. The critical piece is
Step 3 — the cooperative paste state machine in `inputs.rs`.
Read this plan thoroughly before starting. Key files:
`channels/mod.rs`, `channels/inputs.rs`, `config.rs`,
`main.rs`, `app.rs`. The plan provides exact code snippets
for the new types, the `select!` arm, and the plumbing
chain. Adapt them to fit the actual code structure you find
when reading the files — line numbers are approximate.
