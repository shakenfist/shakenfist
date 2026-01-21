# Keyboard Scancodes

This document provides a reference for the keyboard scancodes used by the SPICE
protocol's inputs channel. SPICE uses IBM PC XT-compatible scancodes (Set 1).

## Scancode Format

SPICE supports two scancode formats:

### Simple Scancodes (key_down/key_up messages)

The `key_down` (ID 101) and `key_up` (ID 102) messages use a 32-bit scancode
field. For standard keys, only the low byte is used.

### Raw Scancodes (key_scancode message)

The `key_scancode` (ID 104) message sends raw XT scancodes as a byte array.
This format is more efficient for extended keys and supports:

- **Key press**: Scancode byte as-is
- **Key release**: Scancode byte OR'd with 0x80 (high bit set)
- **Extended keys**: Prefixed with 0xE0 byte

For example:
- Press 'A': `[0x1E]`
- Release 'A': `[0x9E]` (0x1E | 0x80)
- Press Right Ctrl: `[0xE0, 0x1D]`
- Release Right Ctrl: `[0xE0, 0x9D]`

## Standard Scancodes

### Main Keyboard - Row 1 (Function Keys)

| Key | Scancode | Hex |
|-----|----------|-----|
| Escape | 1 | 0x01 |
| F1 | 59 | 0x3B |
| F2 | 60 | 0x3C |
| F3 | 61 | 0x3D |
| F4 | 62 | 0x3E |
| F5 | 63 | 0x3F |
| F6 | 64 | 0x40 |
| F7 | 65 | 0x41 |
| F8 | 66 | 0x42 |
| F9 | 67 | 0x43 |
| F10 | 68 | 0x44 |
| F11 | 87 | 0x57 |
| F12 | 88 | 0x58 |

### Main Keyboard - Row 2 (Number Row)

| Key | Scancode | Hex |
|-----|----------|-----|
| ` (Backquote) | 41 | 0x29 |
| 1 | 2 | 0x02 |
| 2 | 3 | 0x03 |
| 3 | 4 | 0x04 |
| 4 | 5 | 0x05 |
| 5 | 6 | 0x06 |
| 6 | 7 | 0x07 |
| 7 | 8 | 0x08 |
| 8 | 9 | 0x09 |
| 9 | 10 | 0x0A |
| 0 | 11 | 0x0B |
| - (Minus) | 12 | 0x0C |
| = (Equal) | 13 | 0x0D |
| Backspace | 14 | 0x0E |

### Main Keyboard - Row 3 (QWERTY)

| Key | Scancode | Hex |
|-----|----------|-----|
| Tab | 15 | 0x0F |
| Q | 16 | 0x10 |
| W | 17 | 0x11 |
| E | 18 | 0x12 |
| R | 19 | 0x13 |
| T | 20 | 0x14 |
| Y | 21 | 0x15 |
| U | 22 | 0x16 |
| I | 23 | 0x17 |
| O | 24 | 0x18 |
| P | 25 | 0x19 |
| [ (BracketLeft) | 26 | 0x1A |
| ] (BracketRight) | 27 | 0x1B |
| \\ (Backslash) | 43 | 0x2B |

### Main Keyboard - Row 4 (ASDF)

| Key | Scancode | Hex |
|-----|----------|-----|
| Caps Lock | 58 | 0x3A |
| A | 30 | 0x1E |
| S | 31 | 0x1F |
| D | 32 | 0x20 |
| F | 33 | 0x21 |
| G | 34 | 0x22 |
| H | 35 | 0x23 |
| J | 36 | 0x24 |
| K | 37 | 0x25 |
| L | 38 | 0x26 |
| ; (Semicolon) | 39 | 0x27 |
| ' (Quote) | 40 | 0x28 |
| Enter | 28 | 0x1C |

### Main Keyboard - Row 5 (ZXCV)

| Key | Scancode | Hex |
|-----|----------|-----|
| Left Shift | 42 | 0x2A |
| Z | 44 | 0x2C |
| X | 45 | 0x2D |
| C | 46 | 0x2E |
| V | 47 | 0x2F |
| B | 48 | 0x30 |
| N | 49 | 0x31 |
| M | 50 | 0x32 |
| , (Comma) | 51 | 0x33 |
| . (Period) | 52 | 0x34 |
| / (Slash) | 53 | 0x35 |
| Right Shift | 54 | 0x36 |

### Main Keyboard - Row 6 (Bottom)

| Key | Scancode | Hex |
|-----|----------|-----|
| Left Ctrl | 29 | 0x1D |
| Left Alt | 56 | 0x38 |
| Space | 57 | 0x39 |

### Numeric Keypad

| Key | Scancode | Hex |
|-----|----------|-----|
| Numpad 7 (Home) | 71 | 0x47 |
| Numpad 8 (Up) | 72 | 0x48 |
| Numpad 9 (PgUp) | 73 | 0x49 |
| Numpad - | 74 | 0x4A |
| Numpad 4 (Left) | 75 | 0x4B |
| Numpad 5 | 76 | 0x4C |
| Numpad 6 (Right) | 77 | 0x4D |
| Numpad + | 78 | 0x4E |
| Numpad 1 (End) | 79 | 0x4F |
| Numpad 2 (Down) | 80 | 0x50 |
| Numpad 3 (PgDn) | 81 | 0x51 |
| Numpad 0 (Ins) | 82 | 0x52 |
| Numpad . (Del) | 83 | 0x53 |
| Numpad * | 55 | 0x37 |
| Numpad = | 89 | 0x59 |
| Numpad , | 126 | 0x7E |

### Lock Keys

| Key | Scancode | Hex |
|-----|----------|-----|
| Caps Lock | 58 | 0x3A |
| Scroll Lock | 70 | 0x46 |
| Pause | 69 | 0x45 |

### Extended Function Keys (F13-F24)

| Key | Scancode | Hex |
|-----|----------|-----|
| F13 | 100 | 0x64 |
| F14 | 101 | 0x65 |
| F15 | 102 | 0x66 |
| F16 | 103 | 0x67 |
| F17 | 104 | 0x68 |
| F18 | 105 | 0x69 |
| F19 | 106 | 0x6A |
| F20 | 107 | 0x6B |
| F21 | 108 | 0x6C |
| F22 | 109 | 0x6D |
| F23 | 110 | 0x6E |
| F24 | 118 | 0x76 |

### International Keys

| Key | Scancode | Hex |
|-----|----------|-----|
| IntlBackslash | 86 | 0x56 |
| IntlRo | 115 | 0x73 |
| IntlYen | 125 | 0x7D |
| KanaMode | 112 | 0x70 |
| Convert | 121 | 0x79 |
| NonConvert | 123 | 0x7B |

## Extended Scancodes (E0 Prefix)

Extended keys require a 0xE0 prefix byte. When using the `key_scancode` message,
send `[0xE0, scancode]` for press and `[0xE0, scancode | 0x80]` for release.

When using `key_down`/`key_up` messages, the scancode is encoded as:
`0xE0 | (scancode << 8)`

### Navigation Keys

| Key | Extended Code | Bytes (Press) |
|-----|---------------|---------------|
| Insert | 0x52 | E0 52 |
| Delete | 0x53 | E0 53 |
| Home | 0x47 | E0 47 |
| End | 0x4F | E0 4F |
| Page Up | 0x49 | E0 49 |
| Page Down | 0x51 | E0 51 |

### Arrow Keys

| Key | Extended Code | Bytes (Press) |
|-----|---------------|---------------|
| Up Arrow | 0x48 | E0 48 |
| Down Arrow | 0x50 | E0 50 |
| Left Arrow | 0x4B | E0 4B |
| Right Arrow | 0x4D | E0 4D |

### Right-Side Modifier Keys

| Key | Extended Code | Bytes (Press) |
|-----|---------------|---------------|
| Right Ctrl | 0x1D | E0 1D |
| Right Alt | 0x38 | E0 38 |
| Left Meta (Windows) | 0x5B | E0 5B |
| Right Meta (Windows) | 0x5C | E0 5C |
| Context Menu | 0x5D | E0 5D |

### Numpad Extended Keys

| Key | Extended Code | Bytes (Press) |
|-----|---------------|---------------|
| Numpad Enter | 0x1C | E0 1C |
| Numpad / | 0x35 | E0 35 |
| Num Lock | 0x45 | E0 45 |
| Pause | 0x46 | E0 46 |
| Print Screen | 0x37 | E0 37 |

### Media Keys

| Key | Extended Code | Bytes (Press) |
|-----|---------------|---------------|
| Volume Mute | 0x20 | E0 20 |
| Volume Down | 0x2E | E0 2E |
| Volume Up | 0x30 | E0 30 |
| Media Play/Pause | 0x22 | E0 22 |
| Media Stop | 0x24 | E0 24 |
| Media Previous | 0x10 | E0 10 |
| Media Next | 0x19 | E0 19 |
| Media Select | 0x6D | E0 6D |

### Browser Keys

| Key | Extended Code | Bytes (Press) |
|-----|---------------|---------------|
| Browser Home | 0x32 | E0 32 |
| Browser Back | 0x6A | E0 6A |
| Browser Forward | 0x69 | E0 69 |
| Browser Refresh | 0x67 | E0 67 |
| Browser Stop | 0x68 | E0 68 |
| Browser Search | 0x65 | E0 65 |
| Browser Favorites | 0x66 | E0 66 |

### Application Launch Keys

| Key | Extended Code | Bytes (Press) |
|-----|---------------|---------------|
| Launch App 1 (My Computer) | 0x6B | E0 6B |
| Launch App 2 (Calculator) | 0x21 | E0 21 |
| Launch Mail | 0x6C | E0 6C |
| Power | 0x5E | E0 5E |

## Example: Typing "Hello"

Using `key_scancode` messages:

```
H press:   [0x2A, 0x23]     (Shift down, H down)
H release: [0xA3, 0xAA]     (H up, Shift up)
e press:   [0x12]           (E down)
e release: [0x92]           (E up)
l press:   [0x26]           (L down)
l release: [0xA6]           (L up)
l press:   [0x26]           (L down)
l release: [0xA6]           (L up)
o press:   [0x18]           (O down)
o release: [0x98]           (O up)
```

## References

- [MDN Keyboard Event Code Values](https://developer.mozilla.org/en-US/docs/Web/API/UI_Events/Keyboard_event_code_values)
- [spice-html5 scancode mapping](https://gitlab.freedesktop.org/nicholasbishop/nicholasbishop-spice-html5/-/blob/main/src/code_to_scancode.js)
- [Inputs Channel Protocol](/components/kerbside/channel-protocols.md#inputs-channel-type-3)

## Related Documentation

- [Channel Protocols](/components/kerbside/channel-protocols.md) - Inputs channel message formats
- [Capabilities](/components/kerbside/capabilities.md) - KeyScancode capability negotiation
- [Protocol Overview](/components/kerbside/protocol-overview.md) - High-level SPICE protocol
