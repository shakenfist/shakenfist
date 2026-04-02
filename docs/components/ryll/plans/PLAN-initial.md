# Plan: Port Ryll SPICE Client to Rust

## Overview

Port the Python SPICE test client (ryll) to Rust using egui for immediate-mode GUI rendering. The new project will be at `shakenfist/ryll`.

## Architecture Comparison

| Python (current) | Rust (proposed) |
|------------------|-----------------|
| Multi-threaded with queues | Async with tokio + channels |
| tkinter GUI | egui/eframe immediate mode |
| Pillow for images | `image` crate |
| struct.pack/unpack | `byteorder` or `bytes` crate |
| kerbside.SpiceClient | Native Rust implementation |

## Project Structure

```
shakenfist/ryll/
├── Cargo.toml
├── src/
│   ├── main.rs              # CLI entry point, eframe setup
│   ├── app.rs               # egui App implementation, event loop
│   ├── config.rs            # VV file parsing, CLI args
│   ├── protocol/
│   │   ├── mod.rs
│   │   ├── constants.rs     # Channel types, message IDs, enums
│   │   ├── messages.rs      # Message struct definitions
│   │   ├── link.rs          # Handshake/auth protocol
│   │   └── client.rs        # SpiceClient equivalent
│   ├── channels/
│   │   ├── mod.rs
│   │   ├── main_channel.rs  # Main channel handler
│   │   ├── display.rs       # Display channel + surface management
│   │   ├── cursor.rs        # Cursor channel
│   │   └── inputs.rs        # Input channel + scancode mapping
│   ├── decompression/
│   │   ├── mod.rs
│   │   ├── glz.rs           # GLZ decompression
│   │   └── lz.rs            # LZ decompression
│   └── display/
│       ├── mod.rs
│       └── surface.rs       # Surface buffer management
└── README.md
```

## Dependencies (Cargo.toml)

```toml
[dependencies]
eframe = "0.29"              # egui framework (includes egui)
tokio = { version = "1", features = ["full"] }
tokio-rustls = "0.26"        # TLS support
clap = { version = "4", features = ["derive"] }  # CLI parsing
bytes = "1"                  # Binary protocol handling
byteorder = "1"              # Little/big endian parsing
rsa = "0.9"                  # RSA-OAEP for auth
sha1 = "0.10"                # SHA1 for RSA-OAEP
tracing = "0.1"              # Logging
tracing-subscriber = "0.3"
configparser = "3"           # INI file parsing for .vv files
```

## Implementation Steps

### Step 1: Project Scaffolding
- Create `Cargo.toml` with dependencies
- Create basic module structure
- Set up CLI argument parsing with clap
- Implement VV config file parsing

### Step 2: Protocol Foundation
- Define constants (channel types, message IDs, error codes)
- Create message structs with binary serialization
- Implement link handshake and authentication
- Create SpiceClient struct for connection management

### Step 3: Decompression Algorithms
- Port GLZ decompression from Python
- Port LZ decompression from Python
- Output RGBA pixel buffers

### Step 4: Channel Handlers
- Main channel: session init, ping/pong, channel list
- Display channel: surface create, draw_copy, mark
- Cursor channel: position, visibility
- Inputs channel: keyboard scancodes, mouse events

### Step 5: egui Display Integration
- Create App struct implementing eframe::App
- Manage surface as `Vec<u8>` RGBA buffer
- Update egui TextureHandle on each draw_copy
- Handle keyboard/mouse input and route to inputs channel

### Step 6: Async Architecture
- Use tokio for async socket I/O
- Use tokio::sync::mpsc channels for inter-task communication
- Main egui thread receives display updates via channel
- Input events sent from egui to inputs task

## Key Design Decisions

### 1. Immediate Mode Rendering
The egui surface will be a simple pixel buffer:
```rust
struct DisplaySurface {
    width: u32,
    height: u32,
    pixels: Vec<u8>,  // RGBA
    texture: Option<egui::TextureHandle>,
}
```

On each `draw_copy`, blit decompressed pixels directly into `pixels`, then update texture.

### 2. GLZ Dictionary
Maintain HashMap for previous images (needed for GLZ cross-frame references):
```rust
previous_images: HashMap<u64, Vec<u8>>,  // img_id -> RGBA pixels
```

### 3. Async vs Threads
Use tokio async tasks instead of Python threads:
- One task per channel (main, display, cursor, inputs)
- mpsc channels for communication
- egui runs on main thread, receives updates via channel

## Files to Create

1. `Cargo.toml` - Project manifest with dependencies
2. `src/main.rs` - Entry point, CLI, eframe launch
3. `src/app.rs` - egui App, event loop, display rendering
4. `src/config.rs` - VV file and CLI argument parsing
5. `src/protocol/mod.rs` - Protocol module exports
6. `src/protocol/constants.rs` - SPICE constants and enums
7. `src/protocol/messages.rs` - Message struct definitions
8. `src/protocol/link.rs` - Handshake and auth
9. `src/protocol/client.rs` - SpiceClient implementation
10. `src/channels/mod.rs` - Channel module exports
11. `src/channels/main_channel.rs` - Main channel logic
12. `src/channels/display.rs` - Display channel logic
13. `src/channels/cursor.rs` - Cursor channel logic
14. `src/channels/inputs.rs` - Input handling + scancodes
15. `src/decompression/mod.rs` - Decompression exports
16. `src/decompression/glz.rs` - GLZ algorithm
17. `src/decompression/lz.rs` - LZ algorithm
18. `src/display/mod.rs` - Display module exports
19. `src/display/surface.rs` - Surface buffer management

## Verification

1. **Build**: `cargo build` should complete without errors
2. **Run**: `cargo run -- --help` should show CLI options
3. **Connect**: Test against a real SPICE server with a .vv file
4. **Display**: Verify window appears and shows remote desktop
5. **Input**: Verify keyboard/mouse events are sent correctly

## Implementation Scope: Feature Complete

Full port of all ryll functionality:
- Complete SPICE protocol implementation (link, auth, all channels)
- GLZ and LZ decompression algorithms
- Display rendering with egui immediate mode
- Headless mode (`--headless`) for automated testing without GUI
- Keyboard input with AT-84 scancode mapping
- Mouse input (motion, buttons)
- Cursor channel handling
- Latency tracking (key event to display update)
- Statistics collection and display
- CLI with all current options (--url, --file, --direct, --cadence, --headless, etc.)

### Headless Mode Design

When `--headless` is specified:
- Skip eframe/egui window creation entirely
- Run pure tokio async runtime
- Still connect all channels and process data
- Decompress images (for benchmarking) but don't render
- Output statistics to stdout/file
- Useful for: automated testing, latency benchmarks, CI pipelines

## Implementation Order

1. **Project setup** - Cargo.toml, module structure
2. **Config/CLI** - Argument parsing, VV file loading
3. **Protocol constants** - All enums and message IDs
4. **Protocol messages** - Binary serialization/deserialization
5. **Connection** - TLS sockets, handshake, authentication
6. **Main channel** - Session init, channel list, ping/pong
7. **Decompression** - GLZ and LZ algorithms
8. **Display channel** - Surface management, draw_copy handling
9. **egui rendering** - Immediate mode surface display
10. **Input channel** - Keyboard scancodes, mouse events
11. **Cursor channel** - Position and visibility
12. **Statistics** - Bytes in/out, latency tracking
13. **Cadence mode** - Automated keystroke injection for testing
