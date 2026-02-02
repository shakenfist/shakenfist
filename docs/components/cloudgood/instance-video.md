---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/instance-video.md
---

# Instance video

Sometimes users want a graphical console for their instance. libvirt supports this
by exposing either a VNC or a SPICE console on one or more TCP ports on the
hypervisor (the libvirt documentation says it also supports RDP, but that is only
when using virtualbox as the hypervisor). This arrangement is desirable because
unlike running a VNC or RDP server inside the instance, the arrangement is largely
invisible to the instance and hard for the user of the instance to accidentally break.

While these console types can use qemu's standard VGA graphics driver, there are
also two virt graphics drivers available which make things more efficient. The
older of the two is QXL, which was implemented as part of the work to implement
SPICE, and the second is the newer virtio-gpu.

## But why?

Why would users want a graphical console? Certainly its more convenient for
Windows instances, but additionally there are a variety of reasons you might want
virtual desktops -- many products offer such things such as Citrix, AWS Workspaces,
and so on. Sometimes this is about controlling the data stored within the system
(such as confidential source code), sometimes its about providing access to
exotic hardware the end user might not have available, and sometimes its just
about reducing assumptions about the end user's environment -- for example, I
have seen multiple implementations which use virtual desktops to provide access
to training environments for students with widely varying personal hardware.

## VNC vs SPICE

Naively VNC and SPICE are approximately equivalent virtual desktop protocols.
However, there are some differences -- SPICE offers a richer experience (cut
and paste, high resolution desktops, optomised video playback, USB device
passthrough, etc) in return for being a less commonly deployed protocol. However,
for the purposes of this discussion they are identical.

## Security considerations

Exposing TCP ports on your hypervisor is a bad idea. Don't do that. There are
however proxies and web frontends you can place in front of them to make this
safer. That's a topic for another day however.

## How hardware video cards worked back in the day

Early PC graphics (think 286-era) were relatively simple devices, though not
quite as simple as "just a frame buffer". The video card's memory was mapped
into the CPU's address space, allowing programs to write directly to it, but
the details varied by mode:

**Text mode**: Programs didn't write pixels at all. Instead, they wrote
character codes and attributes (foreground/background color, blink) to video
memory. The video card contained a character ROM with font glyphs and handled
the conversion to pixels in hardware. This was efficient for terminal-style
applications and remained common well into the VGA era.

??? example "Text mode graphics demo"

    There is an example of a text mode application written in C in this
    repository [here](https://github.com/shakenfist/cloudgood/tree/main/examples/textmode/).
    I'd just look at the C code itself unless you're super motivated, as the
    process of getting the image to boot is a bit involved.

    You can run the example by running the `make run-sdl` command, but note
    you need to install some dependencies as documented in the README.md
    first.

**Graphics modes**: In graphics modes, programs did write to a frame buffer,
but the memory layout was often complex:

- **CGA** used interleaved memory -- even scan lines lived at one address
  range, odd lines at another. This made scrolling and sprite rendering
  awkward. Sadly there's no demo code for this graphics mode because its
  so old that qemu doesn't actually support it.

- **EGA/VGA planar modes** split the image into bit planes. To set a pixel's
  color, you manipulated I/O ports to select which planes to write, then
  performed the memory write. This allowed more colors with limited memory
  bandwidth but was notoriously painful to program.

- **VGA Mode 13h** (320x200, 256 colors) was the famous "linear" mode -- one
  byte per pixel, laid out sequentially in memory. This matched the simple
  frame buffer mental model, which is why so many DOS games used it despite
  the low resolution.

??? example "VGA mode 13h graphics demo"

    There is an example of a VGA mode 13h application written in C in this
    repository [here](https://github.com/shakenfist/cloudgood/tree/main/examples/mode13h/).
    Again I'd just look at the C code itself unless you're super motivated,
    as the process of getting the image to boot is a bit involved and
    largely identical to the one used in the textmode demo.

    You can run the example by running the `make run-sdl` command, but note
    you need to install some dependencies as documented in the README.md
    first.

**I/O ports were essential**: Video cards weren't purely memory-mapped. I/O
ports controlled video mode selection, CRT timing parameters, palette/DAC
registers, and plane selection. Programs constantly poked these ports.

**No acceleration**: The CPU performed all graphics operations -- calculating
pixels, copying sprites, filling polygons, applying textures. The video card's
only job was to scan out whatever was in VRAM at the monitor's refresh rate.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | VGA video signalling | [The world's worst video card?](https://www.youtube.com/watch?v=l7rce6IQDWs) (YouTube) is not 100% relevant, but is an interesting introduction to how VGA uses timing signals on the wire. |
    | An example of the shared framebuffer memory used by many older video cards | [The world's worst video card? The exciting conclustion](https://www.youtube.com/watch?v=uqY3FMuMuRo) (YouTube) is also not hugely relevant but still kind of fun. |

## How hardware video cards work now

The journey from VGA to modern GPUs spans roughly 35 years and represents one of
the most dramatic architectural shifts in computing history. Understanding this
evolution helps explain why virtualizing modern graphics hardware is
fundamentally harder than emulating a VGA framebuffer.

## The 2D acceleration era (1987-1995)

Even as VGA was being introduced, IBM simultaneously released the **8514/A** in
1987 -- one of the first fixed-function 2D accelerators for PC compatibles. The
8514/A could offload common drawing operations from the CPU:

- Line drawing (Bresenham's algorithm in hardware)
- Rectangle and color fills
- BitBLT (Block Image Transfer -- copying rectangular pixel regions)

This mattered because early ISA buses were painfully slow. Writing pixels
directly to VGA memory over an 8 MHz bus was often slower than maintaining a
local buffer and copying it on vsync. Hardware acceleration bypassed this
bottleneck.

The GUI boom of the early 1990s (Windows 3.0 and its successors) drove rapid
development of 2D accelerators. S3's **86C911** (1991, named after the Porsche)
sparked a wave of competition from Tseng Labs, Cirrus Logic, Trident, ATI, and
Matrox. By 1995, virtually all PC graphics chips included 2D acceleration.

Interestingly, DOS games rarely used these accelerators despite their
availability. The market was completely fragmented -- every vendor had unique,
incompatible hardware. Cards were only compatible at the basic VGA level.
Acceleration only became practical once Windows abstracted the hardware via GDI
and (later) DirectDraw.

## The rise of 3D acceleration (1994-1998)

Early 3D attempts were mixed. The **S3 ViRGE** (1995) was one of the first
consumer 2D/3D combo cards, but it was often called a "3D decelerator" because
CPU software rendering was sometimes faster. NVIDIA's **NV1** (1995) was
technically impressive but used quadratic texture mapping instead of polygons,
making it incompatible with emerging standards.

The breakthrough came with **3dfx's Voodoo Graphics** in late 1996. Founded by
ex-Silicon Graphics engineers, 3dfx made an audacious design choice: the Voodoo
did NOT support 2D at all. It had two VGA ports -- input and output. In 2D mode,
it passed through the signal from your existing VGA card. When a 3D game
launched, the Voodoo took over (some boards had an audible relay click when
switching).

This specialization paid off. The Voodoo delivered:

- Perspective-correct texture mapping (critical for visual quality -- earlier
  hardware had warping artifacts)
- Bilinear and trilinear texture filtering
- Hardware Z-buffering
- Alpha blending for transparency effects
- 50 megapixels/second fill rate

Before 3D accelerators, games like Quake performed everything on the CPU:
transforming 3D vertices through matrices, determining which pixels each
triangle covered, looking up texture colors, handling hidden surface removal.
The Voodoo offloaded rasterization, texturing, and Z-buffering to dedicated
hardware, but the CPU still handled vertex transformation. This division --
CPU for geometry, GPU for pixels -- defined the era.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | The 3dfx founders retell the story of their company | [3dfx Oral History Panel with Ross Smith, Scott Sellers, Gary Tarolli, and Gordon Campbell](https://www.youtube.com/watch?v=3MghYhf-GhU) (YouTube) a very interesting, if a bit long, interview with the founder sof 3dfx. |

## From "video card" to "GPU" (1999)

The term "GPU" was popularized by **NVIDIA with the GeForce 256** in August
1999. NVIDIA defined a GPU as:

> A single-chip processor with integrated transform, lighting, triangle
> setup/clipping, and rendering engines that is capable of processing a
> minimum of 10 million polygons per second.

The key innovation was **Hardware Transform & Lighting (T&L)**. Before the
GeForce 256, the CPU performed all vertex transformation (rotation, translation,
projection) and lighting calculations. The "3D accelerator" only handled the
final rasterization stage.

With hardware T&L, the graphics chip took over vertex processing entirely. This
freed the CPU for game logic, physics, and AI. It also meant the graphics
hardware was now doing *computation*, not just pixel painting -- hence "Graphics
Processing Unit" rather than "video card" or "3D accelerator."

The T&L engine was still **fixed-function** -- it implemented the OpenGL
fixed-function pipeline in hardware. You could enable or disable features
(lighting, fog, texture coordinate generation), but you couldn't program custom
behavior.

## Programmable shaders (2001-2002)

The fixed-function limitation frustrated developers. If the hardware didn't
support a specific effect, you couldn't do it. **DirectX 8** (late 2000)
introduced programmable shaders, with the **GeForce 3** (February 2001) being
the first hardware to fully support them.

**Vertex shaders** were small programs run on every vertex. Instead of the
fixed T&L pipeline, developers could write custom transformation code --
enabling effects like skeletal animation, vertex displacement, and procedural
geometry.

**Pixel shaders** (also called fragment shaders) ran on every pixel. Custom
texturing, per-pixel lighting, and effects like bump mapping became possible
without dedicated fixed-function hardware.

Early shaders were extremely limited: ~128 instructions maximum, no loops or
conditionals, hardware-specific dialects. But the principle was established --
GPUs were becoming programmable processors, not just configurable pipelines.

**DirectX 9** and the **ATI Radeon 9700** (August 2002) brought mature
programmability: pixel shaders up to 160 instructions with floating-point
precision, vertex shaders up to 1024 instructions with flow control. The
transition from "configure the pipeline" to "program the pipeline" was complete.

### The software stack: from API to hardware

Understanding how applications actually communicated with programmable GPUs
helps explain both why GPU virtualization is difficult and why CUDA was
revolutionary.

#### Graphics APIs

Applications didn't talk to GPUs directly. They used graphics APIs:

- **DirectX** (specifically Direct3D) on Windows
- **OpenGL** across platforms

These APIs provided a standardized interface for submitting geometry, loading
textures, binding shaders, and issuing draw calls. The application never
touched hardware directly.

#### Shader languages

Shaders went through their own language evolution:

**Assembly era (2001-2003)**: Early shaders were written in assembly-like
languages. DirectX used shader assembly with version-specific profiles
(`vs_1_1`, `ps_1_1` through `ps_1_4`). OpenGL used ARB assembly language
(`ARBvp1.0`, `ARBfp1.0`), standardized by the OpenGL Architecture Review Board
in 2002. These were tedious to write -- a simple textured, lit surface might
require 20-30 instructions.

**High-level era (2002-2004)**: As shader complexity grew, assembly became
unmanageable. Higher-level languages emerged:

- **HLSL** (High Level Shader Language) -- Microsoft, for DirectX 9+
- **Cg** (C for Graphics) -- NVIDIA, cross-platform, nearly identical to HLSL
- **GLSL** (OpenGL Shading Language) -- standardized in OpenGL 2.0 (2004)

These looked like C with graphics-specific types (vectors, matrices, samplers)
and built-in functions for common operations.

#### The driver: the critical translation layer

The driver bridged the gap between standardized APIs and proprietary hardware.
It consisted of two components:

**User-mode driver (UMD)**: Running in the application's process, this
component compiled shaders from HLSL/GLSL to vendor-specific binary code,
validated API calls, and assembled **command buffers** -- sequences of
hardware-specific commands.

**Kernel-mode driver (KMD)**: Running with kernel privileges, this component
validated command buffers, managed GPU memory, and submitted work to the
hardware.

#### Ring buffer communication

The CPU and GPU communicated through a **ring buffer** in system memory:

```
┌─────────────────────────────────────────────┐
│                Ring Buffer                  │
│  ┌─────┬─────┬─────┬─────┬─────┬─────┐     │
│  │cmd 1│cmd 2│cmd 3│     │     │     │     │
│  └─────┴─────┴─────┴─────┴─────┴─────┘     │
│     ↑                       ↑               │
│   (tail)                  (head)            │
│   GPU reads               CPU writes        │
└─────────────────────────────────────────────┘
```

The driver wrote commands to the buffer and advanced the head pointer. The GPU
read commands and advanced the tail pointer. When the driver updated the head,
it signaled the GPU (via a register write or interrupt) to wake the **command
processor** -- dedicated hardware that parsed and dispatched commands.

#### Why this architecture matters

This layered design had important implications:

**Proprietary command formats**: Each GPU vendor had their own binary command
format, understood only by their driver. There was no standard way to submit
work directly to the hardware.

**Massive drivers**: GPU drivers contained complete shader compilers for every
supported hardware generation. A modern NVIDIA driver is hundreds of megabytes,
much of it compiler code.

**Virtualization difficulty**: You couldn't simply intercept API calls and
replay them -- the actual hardware interface was the proprietary command stream.
This is why GPU virtualization required either full software emulation (slow)
or vendor cooperation (SR-IOV, vGPU).

**CUDA's innovation**: When NVIDIA introduced CUDA in 2007, they documented a
way to submit compute workloads that bypassed the graphics API entirely. For
the first time, developers had a supported path to GPU compute without
pretending their data was textures and their algorithms were shaders.

## Unified shaders (2005-2007)

Earlier GPUs had separate vertex shader units and pixel shader units with
different capabilities. This created inefficiencies -- vertex-heavy scenes left
pixel shaders idle, and vice versa.

The **Xbox 360's Xenos GPU** (2005, designed by ATI) introduced unified shaders:
all shader units were identical and could process vertices, pixels, or geometry
interchangeably. Hardware utilization improved dramatically.

NVIDIA brought unified shaders to PC with the **GeForce 8800 GTX** (November
2006): 128 stream processors that could be dynamically allocated to any shader
stage. This architecture also enabled a new pipeline stage -- geometry shaders
-- and laid the groundwork for general-purpose GPU computing.

## GPGPU: the GPU becomes a general-purpose processor (2007-present)

Before dedicated compute APIs, researchers performed general-purpose computation
by awkwardly encoding problems as graphics operations -- storing data in
textures and using pixel shaders for calculation. It worked, but was limited
and painful.

**CUDA** (June 2007) changed everything. NVIDIA released a C-like programming
language for their GPUs, exposing them as massively parallel processors rather
than graphics-specific hardware. **OpenCL** (2008) followed as a vendor-neutral
alternative.

Modern GPUs have evolved into genuinely general-purpose parallel processors:

| Characteristic | CPU | GPU |
|---------------|-----|-----|
| Design philosophy | Minimize latency (fast single-thread) | Maximize throughput (aggregate parallel) |
| Core count | 8-64 sophisticated cores | 10,000+ simple cores |
| Control logic | Branch prediction, out-of-order execution, speculation | Minimal per-core |
| Cache strategy | Large caches to hide memory latency | Hide latency by running thousands of threads |
| Memory bandwidth | ~100 GB/s | 500 GB/s to 2+ TB/s |

The transistor counts tell the story of this complexity explosion:

| Year | GPU | Transistors |
|------|-----|-------------|
| 1999 | GeForce 256 | 17 million |
| 2006 | GeForce 8800 | 681 million |
| 2020 | NVIDIA A100 | 54 billion |
| 2024 | NVIDIA B200 | 208 billion |

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Comprehensive GPU history | [The History of the Modern Graphics Processor](https://www.techspot.com/article/650-history-of-the-gpu/) (TechSpot) |
    | 3dfx Voodoo deep dive | [The story of the 3dfx Voodoo1](https://fabiensanglard.net/3dfx_sst1/) (Fabien Sanglard) |
    | GeForce 256 and the birth of the GPU | [Famous Graphics Chips: Nvidia's GeForce 256](https://www.computer.org/publications/tech-news/chasing-pixels/nvidias-geforce-256) (IEEE) |
    | Historical 2D/3D hardware | [DOS Days Graphics Technology](https://www.dosdays.co.uk/topics/graphics_features_pt2.php) |
    | IBM's pioneering 8514/A | [Famous Graphics Chips: IBM's PGC and 8514/A](https://www.computer.org/publications/tech-news/chasing-pixels/Famous-Graphics-Chips-IBMs-professional-graphics-the-PGC-and-8514A) (IEEE) |

## Why modern GPUs are hard to virtualize

This history explains why virtualizing modern graphics hardware is fundamentally
different from emulating a VGA framebuffer.

### VGA virtualization is straightforward

A VGA framebuffer is conceptually simple:

- Memory-mapped pixel buffer
- A handful of control registers (resolution, color depth, timing)
- Minimal internal state
- Well-documented, stable interfaces

The hypervisor can easily emulate registers, provide virtual framebuffer memory,
and translate memory writes to host display operations. This is exactly what
QEMU's VGA emulation does.

### Modern GPUs resist virtualization

Modern GPUs have characteristics that make virtualization dramatically harder:

**Massive internal state**: A modern GPU has thousands of execution contexts,
complex command queues and ring buffers, shader programs loaded into GPU memory,
texture caches, memory management units with page tables, and performance
counters. Capturing all this state for live migration is far more complex than
copying a framebuffer.

**Proprietary, undocumented architecture**: Unlike CPUs with published ISA
specifications, GPU internal architectures are closely guarded trade secrets.
Each generation changes significantly. It's generally not feasible to fully
emulate new GPU generations -- only older, simpler hardware can be accurately
emulated.

**Performance-critical hardware access**: GPUs require direct memory access
(DMA) for command submission, low-latency interrupt handling, and high-bandwidth
memory access. Virtualizing these at the hypervisor level adds unacceptable
latency. Software rendering achieves roughly 3% of hardware-accelerated native
performance.

**No standardization**: Each vendor has proprietary virtualization solutions:

- NVIDIA: vGPU (licensed proprietary software)
- AMD: MxGPU (hardware-based partitioning)
- Intel: GVT-g, SR-IOV (varies by generation)

This is why the virtual graphics options we'll discuss below -- QXL and
virtio-gpu -- take fundamentally different approaches than hardware emulation.
QXL uses a high-level 2D command protocol. virtio-gpu either accepts
pre-rendered framebuffers or forwards API calls to the host GPU. Neither
attempts to emulate actual GPU hardware.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | GPU virtualization overview | [GPU virtualization](https://en.wikipedia.org/wiki/GPU_virtualization) (Wikipedia) |
    | Vendor approaches compared | [NVIDIA, AMD, and Intel: How they do their GPU virtualization](https://www.techtarget.com/searchvirtualdesktop/opinion/NVIDIA-AMD-and-Intel-How-they-do-their-GPU-virtualization) (TechTarget) |

## QXL

QXL is a paravirtualized 2D graphics device developed by Red Hat as part of the
SPICE (Simple Protocol for Independent Computing Environments) project. Unlike
emulated VGA hardware which pretends to be a physical video card from the 1990s,
QXL is designed from the ground up for virtualization -- the guest operating
system knows it's running on a virtual device and uses a specialized driver to
communicate efficiently with the host.

### How QXL differs from VGA emulation

Traditional VGA emulation is expensive. The hypervisor must intercept every I/O
port access and memory-mapped write to the emulated VGA registers, translate
those operations into something meaningful, and update the display. This
creates a constant stream of VM exits (transitions from guest to hypervisor
context), each of which has significant overhead.

QXL takes a different approach. Instead of emulating hardware registers, it
exposes a set of memory regions that the guest driver can write commands into
directly. The guest batches up drawing operations -- "draw a rectangle here",
"copy this region there", "render this cursor" -- into a command ring. The
host periodically processes these commands in bulk, dramatically reducing the
number of VM exits.

Additionally, QXL integrates tightly with the SPICE protocol for remote display.
Rather than simply sending raw framebuffer updates over the network (as VNC
does), SPICE can transmit the high-level QXL commands to a remote client. The
client then renders those commands locally using its own GPU. This means 2D
acceleration effectively happens on the viewer's machine rather than the server,
making remote desktop performance surprisingly good even over moderate bandwidth
connections.

### Why a bare-metal QXL example is impractical

Earlier in this document we provided bare-metal examples for text mode and VGA
mode 13h graphics. These work because VGA has **fixed, well-known memory
addresses** -- write to `0xB8000` and characters appear on screen, write to
`0xA0000` and pixels appear. The simplicity is striking: no device discovery,
no protocol negotiation, just memory writes.

QXL is fundamentally different, and creating an equivalent bare-metal example
would require substantially more infrastructure code than actual graphics code.

**PCI bus enumeration**: QXL is a PCI device. Unlike VGA's fixed addresses, a
bare-metal program must scan PCI configuration space (via I/O ports 0xCF8 and
0xCFC), locate the QXL device by its vendor and device IDs (0x1b36 and 0x0100
respectively), and read the Base Address Registers to discover where memory
regions have been mapped. These addresses change between boots.

**Multiple memory regions**: QXL exposes several BARs -- primary RAM containing
the framebuffer and command rings, VRAM for off-screen surfaces, and I/O ports
for device control. Each must be discovered and mapped correctly before any
graphics operations can occur.

**The command ring protocol**: QXL doesn't accept simple framebuffer writes.
Instead, you must allocate command structures in QXL memory, populate them with
drawing operations (DRAW_OPAQUE, DRAW_COPY, UPDATE_AREA, etc.), submit them to
a ring buffer, update ring pointers, and notify the device via I/O port writes.
The device processes commands asynchronously and updates completion pointers.

**Device initialization**: Before any of this, you need to parse the QXL ROM
for device capabilities, configure video modes through QXL-specific mechanisms,
and potentially handle interrupts for completion notifications.

The complexity difference is stark:

| Aspect | VGA Mode 13h | QXL bare-metal |
|--------|--------------|----------------|
| Device discovery | Not needed (fixed address) | PCI enumeration required |
| Memory addresses | Hardcoded `0xA0000` | Dynamic BAR discovery |
| Draw a pixel | `mem[offset] = color` | Allocate command, fill structure, submit to ring, notify device |
| Setup code | ~20 lines | ~300-500 lines |

In a bare-metal QXL example, roughly 90% of the code would be PCI infrastructure
rather than anything QXL-specific. The educational value of demonstrating
paravirtualized I/O would be buried under boilerplate. Additionally, given QXL's
declining relevance (discussed later in this document), investing effort in such
an example seems unwarranted.

For those interested in exploring QXL internals, a more practical approach is
examining QXL debug logs from a running system, or reading the Linux kernel's
QXL driver source code (`drivers/gpu/drm/qxl/`). The QEMU source code
(`hw/display/qxl.c`) also provides insight into the device-side implementation.

### Debugging QXL

If you want to understand what QXL is doing at runtime, there are debugging
options on both the guest (Linux DRM driver) and host (QEMU) sides.

#### Guest-side: Linux DRM debug logging

The DRM subsystem provides a bitmask debug parameter that controls verbose
logging. You can enable it at boot via kernel command line:

```
drm.debug=0x1f
```

Or at runtime:

```bash
echo 0x1f > /sys/module/drm/parameters/debug
```

The bit values control different categories of debug output:

| Bit | Value | Category |
|-----|-------|----------|
| 0 | 0x01 | DRM core |
| 1 | 0x02 | Driver-specific |
| 2 | 0x04 | KMS (mode setting) |
| 3 | 0x08 | Prime (buffer sharing) |
| 4 | 0x10 | Atomic modesetting |
| 5 | 0x20 | VBlank events |

Output appears in the kernel log, viewable with `dmesg`. For QXL-specific
messages:

```bash
dmesg | grep -i qxl
```

When `CONFIG_DEBUG_FS` is enabled in the kernel, the QXL driver also exposes
information via debugfs at `/sys/kernel/debug/dri/0/`, including GEM object
state, framebuffer information, and VRAM usage.

#### Host-side: QEMU trace events

QEMU has extensive tracing infrastructure, including many QXL-specific trace
events. Enable them with the `-d` or `--trace` options:

```bash
# Enable all QXL trace events
qemu-system-x86_64 ... -d trace:qxl_*

# Or specific categories
qemu-system-x86_64 ... \
  --trace "qxl_ring_command_*" \
  --trace "qxl_spice_*"
```

Available QXL trace events include:

| Category | Events | What they show |
|----------|--------|----------------|
| Ring operations | `qxl_ring_command_get`, `qxl_ring_cursor_get` | Commands submitted by guest |
| Mode changes | `qxl_enter_vga_mode`, `qxl_exit_vga_mode`, `qxl_set_mode` | Display mode transitions |
| Surface management | `qxl_create_guest_primary`, `qxl_destroy_primary` | Framebuffer allocation |
| SPICE integration | `qxl_spice_update_area`, `qxl_spice_monitors_config` | Display updates sent to SPICE |
| Rendering | `qxl_render_blit`, `qxl_render_update_area_done` | Actual rendering operations |

QEMU also includes a dedicated command logger (`hw/display/qxl-logger.c`) that
can decode QXL commands into human-readable form.

#### Practical debugging example

To observe QXL behaviour from both sides simultaneously:

**On the host**, start QEMU with tracing enabled:

```bash
qemu-system-x86_64 \
  -device qxl-vga \
  -spice port=5900,disable-ticketing=on \
  -d trace:qxl_* \
  ...
```

**In the guest**, enable DRM debugging and watch the log:

```bash
echo 0x1f > /sys/module/drm/parameters/debug
dmesg -w | grep -E '(qxl|drm)'
```

This lets you correlate guest driver actions with host device responses --
useful for understanding the command flow or diagnosing display issues.

### QXL memory architecture

QXL exposes multiple PCI memory regions (BARs -- Base Address Registers) to the
guest. Understanding these is essential for configuring QXL properly,
particularly for high-resolution or multi-monitor setups.

#### The four memory parameters

| Parameter | PCI BAR | Default | Purpose |
|-----------|---------|---------|---------|
| **vgamem** | (within BAR 0) | 16 MB | The VGA framebuffer -- must be sized for your maximum resolution |
| **ram** | BAR 0 | 64 MB | Contains vgamem plus command rings and rendering data |
| **vram** | BAR 1 (32-bit) | 64 MB | Off-screen surface storage |
| **vram64** | BAR 4+5 (64-bit) | 0 | Extended surface storage for 64-bit guests |

**BAR 0 (RAM)** is the primary memory region. It contains:

- The **VGA framebuffer** (the `vgamem` portion) at the start -- this is where
  the actual displayed pixels live
- **Command rings** at the end -- circular buffers where the guest submits
  drawing commands and the host returns completion notifications
- **Working space** in the middle for QXL rendering commands, image data, clip
  regions, and cursor data

The critical relationship here is that `vgamem` lives *inside* `ram` -- they're
not additive. QEMU enforces that `ram` must be at least twice the size of
`vgamem`.

**BAR 1 (VRAM)** provides storage for off-screen surfaces. X11 and Windows both
use off-screen pixmaps extensively -- for window backing stores, cached rendered
content, double-buffering, and compositing. This memory is addressed with 32-bit
physical addresses, limiting it to the first 4GB of guest address space.

**BAR 4+5 (VRAM64)** exists because of 32-bit addressing limitations. When
running multiple video cards or needing large amounts of surface memory (for 4K
multi-monitor setups, for instance), the 32-bit address space becomes cramped.
VRAM64 provides a 64-bit addressable region that 64-bit guest drivers can use.
The first portion of VRAM64 overlaps with the 32-bit VRAM region -- they're
different windows into the same underlying memory.

#### Sizing for different resolutions

The fundamental calculation for `vgamem` is straightforward:

```
vgamem = width × height × 4 bytes (for 32-bit color) × number_of_heads
```

For a single 4K (3840×2160) display:

```
3840 × 2160 × 4 = 33,177,600 bytes ≈ 32 MB
```

The general recommendations are:

- `ram` should be at least 4× `vgamem` (QEMU enforces a minimum of 2×)
- `vram` should be at least 2× `vgamem`
- For 64-bit guests with high resolutions, set `vram64` larger than `vram`

| Resolution | vgamem | ram | vram | Notes |
|------------|--------|-----|------|-------|
| 1920×1080 (1080p) | 16 MB | 64 MB | 32 MB | Comfortable defaults |
| 2560×1440 (QHD) | 16 MB | 64 MB | 64 MB | |
| 3840×2160 (4K) | 32 MB | 128 MB | 64 MB | Consider vram64=128 MB |
| Dual 4K | 64 MB | 128 MB | 128 MB | Use vram64=256 MB |

#### Platform differences

**Linux guests** support multiple monitors on a single QXL device (up to 4
heads). The driver supports page-flipping for smooth display updates, so having
room for 3-4 framebuffers is beneficial.

**Windows guests** require one QXL device per monitor. The primary display uses
`qxl-vga` (which includes VGA compatibility for boot), while secondary displays
use plain `qxl` devices.

#### Example configurations

QEMU command line for a single 4K display:

```bash
-device qxl-vga,vgamem_mb=32,ram_size_mb=128,vram_size_mb=64
```

libvirt XML (note that values are in KB):

```xml
<video>
  <model type='qxl' ram='131072' vram='65536' vgamem='32768' heads='1'/>
</video>
```

#### QEMU defaults and parameter independence

An important detail: QEMU's QXL parameters are **largely independent** with
their own defaults. If you only specify some parameters, the others use defaults
rather than being derived from the values you set.

From the QEMU source (`hw/display/qxl.c`), the defaults are:

| Parameter | QEMU property | Default |
|-----------|---------------|---------|
| vgamem | `vgamem_mb` | 16 MB |
| ram (BAR 0) | `ram_size` | 64 MB |
| vram (BAR 1) | `vram_size` | 64 MB |
| vram64 (BAR 4+5) | `vram64_size_mb` | 0 (disabled) |

The only constraints enforced are:

- `ram` must be at least 2× `vgamem` (if you set vgamem higher, ram is
  automatically increased)
- `vram64` must be at least as large as `vram` (if set)
- All values are rounded up to the nearest power of two

This means if you only configure `vram`, the critical `vgamem` parameter stays
at 16 MB -- which limits your maximum resolution to approximately 1920×1080.

#### OpenStack's limitation

OpenStack Nova only exposes one QXL parameter: `hw_video_ram` (or the flavor
extra spec `hw_video:ram_max_mb`). This maps to libvirt's `vram` attribute,
which becomes QEMU's `vram_size` (BAR 1, surface storage).

**OpenStack does not expose `vgamem`** -- the parameter that actually controls
maximum framebuffer resolution.

The practical consequence:

| What you configure in OpenStack | What QEMU uses |
|--------------------------------|----------------|
| `hw_video_ram=128` | vram = 128 MB |
| (nothing) | vgamem = **16 MB (default)** |
| (nothing) | ram = **64 MB (default)** |

With the default 16 MB vgamem, you're limited to resolutions around 1920×1080
regardless of how much vram you allocate:

```
1920 × 1080 × 4 bytes = 8.3 MB   ✓ fits in 16 MB
2560 × 1440 × 4 bytes = 14.7 MB  ✓ barely fits
3840 × 2160 × 4 bytes = 33.2 MB  ✗ needs 32 MB vgamem
```

If you need higher resolutions in OpenStack, you'll need to either modify Nova
to expose additional QXL parameters or switch to virtio-gpu, which doesn't have
this configuration complexity.

### QXL's declining relevance

QXL was designed in an era when 2D acceleration mattered. Graphics cards had
dedicated silicon for operations like rectangle fills, BitBlt copies, and
hardware cursors. By offloading these operations to the SPICE client, QXL could
deliver a responsive remote desktop experience.

Modern GPUs have largely abandoned dedicated 2D acceleration hardware. Everything
goes through the 3D pipeline now -- even drawing a simple rectangle is done by
setting up vertices and running them through shaders. This architectural shift
has made QXL's 2D acceleration model increasingly anachronistic.

As Gerd Hoffmann (one of the QXL kernel maintainers and a Red Hat engineer)
noted in 2019:

> QXL is a slightly dated display device... It has support for 2D acceleration.
> This becomes more and more useless though as modern display devices don't have
> dedicated 2D acceleration support any more and use the 3D engine for
> everything.

## virtio-gpu

virtio-gpu represents the modern approach to virtual graphics. Rather than
trying to accelerate 2D operations like QXL, virtio-gpu focuses on providing
either efficient framebuffer transport (in 2D mode) or actual 3D acceleration
via the host GPU (using virgl or Venus).

### Architecture overview

virtio-gpu is built on the VirtIO standard -- the same framework used for
virtio-net, virtio-blk, and other paravirtualized devices. It uses virtqueues
(ring buffers in shared memory) for communication between guest and host.

The device has two queues:

- **controlq**: All primary operations -- resource creation, display
  configuration, 3D command submission
- **cursorq**: Dedicated queue for cursor updates, keeping pointer movement
  responsive even when the main queue is busy

Unlike QXL with its multiple memory BARs, virtio-gpu has a fundamentally
different memory model: **it uses guest main memory for almost everything**.
There's no dedicated VRAM allocation. The guest allocates memory for
framebuffers and surfaces using standard DRM/GEM mechanisms, then tells the
host where that memory is. This simplifies configuration significantly -- you
don't need to worry about sizing RAM vs VRAM vs VRAM64.

### Operating modes

#### 2D mode (basic)

In basic 2D mode, virtio-gpu is essentially a dumb framebuffer transport:

1. Guest allocates a framebuffer in system memory
2. Guest renders into it using software rendering (Mesa's llvmpipe, for example)
3. Guest tells host to copy the framebuffer to the display
4. Host blits the pixels to the screen

This is simple and works everywhere, but there's no acceleration -- all
rendering happens on the guest CPU.

### 3D mode with virgl (OpenGL)

virgl (Virgil 3D) provides OpenGL acceleration by forwarding rendering commands
to the host GPU. The architecture is:

1. Guest application issues OpenGL calls
2. Guest Mesa driver (the virgl Gallium driver) captures these calls
3. Commands are serialized along with shaders (in TGSI intermediate format)
4. Commands sent to host via `VIRTIO_GPU_CMD_SUBMIT_3D`
5. Host virglrenderer library validates and translates commands
6. TGSI shaders are converted back to GLSL
7. Host GPU executes the actual rendering

This works, but has significant overhead:

- **Double compilation**: Shaders are compiled in the guest to TGSI, then on
  the host TGSI is translated back to GLSL and compiled again by the host
  driver
- **Serialization**: All guest OpenGL applications share a single command
  stream to the host. Running multiple OpenGL programs simultaneously causes
  contention.
- **OpenGL abstraction overhead**: The OpenGL state machine doesn't map
  efficiently to modern GPU programming models

virgl achieves roughly 60-100% of native performance for synthetic GPU tests,
but can drop to 5-20% of native for texture-heavy workloads like games.

### 3D mode with Venus (Vulkan)

Venus is a newer Vulkan driver that provides much better performance than virgl
because Vulkan's lower-level design maps more efficiently to virtualization:

- **SPIR-V passthrough**: Shaders are passed as SPIR-V binary directly to the
  host without recompilation
- **Minimal translation**: Vulkan calls require very little modification to
  forward
- **Better parallelism**: Venus doesn't have virgl's serialization bottleneck

Venus achieves near-native performance for most workloads. The main overhead
is host-guest synchronization rather than command translation.

Requirements for Venus:

- Host: Linux kernel 6.13+, QEMU 9.2.0+, virglrenderer 1.0.0+
- Guest: Linux kernel 5.16+, Mesa with Venus driver
- Host GPU driver must support `VK_EXT_image_drm_format_modifier`

#### Zink: OpenGL via Vulkan

An interesting option is running Zink (Mesa's OpenGL-to-Vulkan translation
layer) on top of Venus. This provides OpenGL support while avoiding virgl's
limitations -- multiple OpenGL applications can run concurrently without severe
performance degradation.

### Device variants

QEMU provides several virtio-gpu variants:

| Device | VGA Compatible | 3D Support | Notes |
|--------|---------------|------------|-------|
| `virtio-gpu-pci` | No | Optional | Modern guests, reduced attack surface |
| `virtio-vga` | Yes | No | x86 systems needing VGA boot |
| `virtio-vga-gl` | Yes | VirGL | 3D acceleration with VGA fallback |
| `virtio-gpu-gl-pci` | No | VirGL | 3D without VGA overhead |
| `virtio-gpu-rutabaga` | No | gfxstream | Google's Android-focused backend |

### Configuration

Unlike QXL, virtio-gpu has few memory-related parameters to tune. The main
settings are:

```bash
# Basic 2D
-device virtio-vga -display gtk

# 3D with virgl
-device virtio-vga-gl -display gtk,gl=on

# 3D with Venus (Vulkan)
-device virtio-vga-gl,hostmem=4G,blob=true,venus=true -display gtk,gl=on

# Multiple monitors
-device virtio-gpu-pci,max_outputs=2
```

The `hostmem` parameter is only needed for advanced features like blob resources
(used by Venus). For basic virgl usage, no memory configuration is required.

### Guest driver requirements

**Linux**: Built into the kernel since 4.4 (`CONFIG_DRM_VIRTIO_GPU`). Works
out of the box on most modern distributions. For 3D acceleration, you need
Mesa with the virgl driver (standard in distributions) or Venus driver (newer).

**Windows**: More limited support. The virtio-win package includes a basic
display driver (`viogpudo`). There's an experimental 3D driver (`viogpu3d`)
under development that supports OpenGL and Direct3D 10, but it's not yet
production-ready.

## Comparing QXL and virtio-gpu

### Architectural differences

| Aspect | QXL | virtio-gpu |
|--------|-----|------------|
| **Memory model** | Dedicated VRAM BARs | Uses guest system memory |
| **Acceleration** | 2D (rendered on SPICE client) | 3D (rendered on host GPU) |
| **Display protocol** | Tightly coupled with SPICE | Protocol-agnostic |
| **Configuration complexity** | Must size RAM/VRAM/VRAM64 | Minimal configuration |
| **Multi-monitor** | Linux: 4 heads/device; Windows: 1 device/monitor | Flexible via max_outputs |

### When to use each

**Use QXL when:**

- You need SPICE-specific features (USB redirection, clipboard sharing, audio)
- Running older Windows guests without virtio drivers
- Using SPICE for remote access and want efficient bandwidth usage

**Use virtio-gpu when:**

- You need 3D acceleration
- Running modern Linux guests
- You want simpler configuration
- You're not using SPICE, or are using SPICE but don't need 2D acceleration

### The shift away from QXL

The Linux kernel community and Red Hat have been moving away from QXL:

**Red Hat's deprecation timeline:**

- **RHEL 8.3 (2020)**: SPICE and QXL officially deprecated
- **RHEL 9 (2022)**: SPICE and QXL support completely removed. VMs configured
  to use SPICE or QXL fail to start. QEMU is compiled without SPICE support,
  and the kernel doesn't include QXL drivers.

**Kernel maintenance status:**

While QXL is nominally still "Maintained" in the kernel MAINTAINERS file, it
receives minimal attention. Both listed maintainers (Dave Airlie and Gerd
Hoffmann) work for Red Hat, which has abandoned QXL in their enterprise
distribution.

A significant regression was introduced in kernel 6.8 (commit `5a838e5d5825`
"drm/qxl: simplify qxl_fence_wait") that causes "[TTM] Buffer eviction failed"
errors and display crashes. The fix history has been troubled -- a revert was
applied in 6.8.7, then the revert was itself reverted in 6.8.10, leaving many
users with a broken driver. As of early 2025, the issue remains unresolved in
many kernel versions.

**The recommended path forward** is migration to virtio-gpu. For SPICE users
who need remote access features, virtio-gpu works with SPICE for display --
you lose the 2D acceleration optimization but gain 3D support and a
better-maintained codebase.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | QXL memory architecture | [oVirt Video RAM Documentation](https://www.ovirt.org/develop/internal/video-ram.html) |
    | QEMU display devices overview | [VGA and display devices in QEMU](https://www.kraxel.org/blog/2019/09/display-devices-in-qemu/) (Gerd Hoffmann) |
    | virgl architecture | [Virgil 3D: A virtual GPU](https://lwn.net/Articles/611212/) (LWN) |
    | Venus documentation | [Mesa Venus Driver](https://docs.mesa3d.org/drivers/venus.html) |
    | State of virtualized graphics | [The state of GFX virtualization using virglrenderer](https://www.collabora.com/news-and-blog/blog/2025/01/15/the-state-of-gfx-virtualization-using-virglrenderer/) (Collabora, 2025) |
    | RHEL 9 changes | [Virtualization considerations in adopting RHEL 9](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/considerations_in_adopting_rhel_9/assembly_virtualization_considerations-in-adopting-rhel-9) |

--8<-- "docs-include/abbreviations.md"
