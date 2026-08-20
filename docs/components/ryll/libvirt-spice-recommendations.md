# Libvirt / QEMU Settings for Best SPICE Performance with Ryll

Ryll is built to tolerate any reasonable SPICE server configuration, but the
guest configuration has a large effect on what the user perceives as
"display responsiveness." This document captures the settings we recommend
for QEMU guests fronted by SPICE, based on measurements taken against
real guests with ryll's own bug-report instrumentation, and on reading
the upstream `spice-server` and `qemu` source.

The recommendations below are written against a **libvirt domain XML**
because that is the most common deployment path; the equivalent direct
`qemu` command-line flags are noted in parentheses where helpful.

!!! tip "Prefer virtio-vga over qxl"

    The virtio display path is the actively-maintained one, and it
    does not produce the pathological full-frame updates QXL guests
    fall into.

!!! warning "QXL video streaming stops working above roughly 1280 pixels wide"

    Expect stable streams at 1024×768 and 1280×832, and static
    fallback from 1600×1200 up. Increasing VRAM does not fix it.

!!! tip "Three server-side settings do most of the work"

    Set `image-compression=auto_lz`, drop any
    `*-wan-compression=always` overrides, and use
    `streaming-video=filter` rather than `all` or `off`.

!!! info "Two packages are prerequisites"

    Install `spice-vdagent` in the guest for clipboard, absolute
    pointer, and dynamic monitor configuration. Install
    `gstreamer1.0-plugins-bad` and `gstreamer1.0-vaapi` on the
    hypervisor if you want H.264 streams to be available at all.

The rest of this document explains why.

## Video device

### Recommended: virtio-vga (or virtio-gpu-pci)

```xml
<video>
  <model type='virtio' heads='1' primary='yes'>
    <acceleration accel3d='no'/>
  </model>
</video>
```

`virtio-gpu` is the actively-maintained guest-side display path in modern
Linux kernels. It uses a normal virtio queue to push dirty rectangles
into the host, which gives the SPICE server clean small-region updates
to encode — exactly what the static-UI (terminal, text) path needs.

Trade-offs vs `qxl`:

- **No hardware-accelerated streaming video.** The SPICE server's
  "stream this region as MJPEG/H.264" path is tightly coupled to the
  QXL command ring. With `virtio-gpu`, video playback falls back to
  bitmap blits and is bandwidth-heavier. This follows from the
  server source; we have not yet measured a virtio guest against a
  QXL one for sustained video, so treat the size of the difference
  as unquantified. Predictable bitmap blits may well feel better
  than QXL's stream-then-fall-back behaviour above the resolution
  cliff described below.
- **3D acceleration is opt-in.** Set `accel3d='yes'` if you want
  virgl 3D forwarding; leave off for plain VDI workloads.

### Not recommended: qxl

```xml
<!-- avoid for new VMs -->
<video>
  <model type='qxl' ram='65536' vram='65536' vgamem='16384' heads='1' primary='yes'/>
</video>
```

The QXL display driver is in maintenance-only mode upstream — it still
works but is no longer where SPICE engineering investment is going.
We have seen QXL guests fall into pathological encoding patterns
(full-screen `ZlibGlzRgb` blasts for terminal cursor blinks) that
virtio-gpu does not exhibit.

**Resolution cliff.** Measured on Debian 11 with QXL across four
guest resolutions and three VRAM allocations, video streaming
degrades sharply between roughly 1280 and 1600 pixels wide. The
raw evidence behind this section is in the streaming
intermittency investigation, linked from
[`PLAN-stream-caps-and-flap.md`](/components/ryll/plans/PLAN-stream-caps-and-flap/).

| Guest resolution | Video streaming behaviour |
|---|---|
| 1024×768 | Stable MJPEG/H.264 stream for the whole clip. |
| 1280×832 | Stable stream. |
| 1600×1200 | Stream lives ~1.7 s, then is destroyed. |
| 1920×1440 | Stream is created within seconds of video start, torn down ~8 s later, and never recreated. |

Two distinct mechanisms are at work above the cliff.

*The server's stream-detection heuristic stops matching.* Per
`spice/server/display-channel.cpp:1057-1078`, the QXL guest driver
issues qualitatively different draw ops at higher resolutions
(tiled copies, alpha-blends, pre-compressed images) that fail the
heuristic's `QXL_DRAW_COPY + QXL_EFFECT_OPAQUE + SPICE_ROPD_OP_PUT
+ SPICE_IMAGE_TYPE_BITMAP` filter. Frames that fail the filter fall
back to full-frame ZlibGlzRgb updates — heavy on both bandwidth and
client-side decode.

*The guest QXL driver runs out of command-ring memory.* At
1920×1440 the qemu log shows `display_channel_debug_oom` firing
~1.7 times per second throughout playback. That is qemu's QXL
device emulation telling spice-server the guest driver's command
ring is exhausted; spice-server responds by dropping pending
drawables via `display_channel_free_some` and flushing, which
appears to evict the stream-tracking state and stop the server
re-engaging streaming for the rest of the session. This is why the
1920×1440 stream never comes back after its first teardown.

**VRAM is not the lever it looks like.** Running the same
1920×1440 workload at 64 MiB, 128 MiB, and 256 MiB VRAM does not
restore stable streaming at any of the three sizes:

- **VRAM does not unlock streaming.** None of the three sizes
  produced stable video, so a `vram` bump on its own does not
  restore video performance. The three-size comparison predates
  the logging fix described below, so it cannot distinguish
  "no stream was created" from "the stream was invisible to the
  instrumentation"; the create-then-teardown-then-never-recreate
  cycle in the table above comes from the one 1920×1440 run with
  working server-side debug logging.
- **VRAM plausibly affects the OOM rate**, and therefore stream
  *survival* and *re-engagement*, indirectly. Whether a larger
  `vram` reduces OOM frequency enough to keep a stream alive is
  an open question we have not yet measured; it is tracked in
  [`PLAN-stream-caps-and-flap.md`](/components/ryll/plans/PLAN-stream-caps-and-flap/).

Net: don't undersize `vram` on QXL guests intended for
video workloads, but don't expect a `vram` bump alone to be
a cure either. The static-UI cache-hit benefit of generous
VRAM is real and unchanged.

!!! note "Reproducing these measurements needs server-side debug logging"

    The stream-create, teardown, and OOM evidence above is only
    visible in spice-server's own log output, and libvirt scrubs
    most environment variables before forking qemu — see
    [Enabling server-side debug logging](#enabling-server-side-debug-logging)
    below for the setup that actually works. Without it the server
    looks like it never creates a stream at 1920×1440 at all, which
    is a measurement artefact rather than the real behaviour.

For 1024×768 or 1280-class desktops on QXL, streaming works and
the trade-off is fine. For 1600+ desktops on QXL, expect static
fallback and use both client-side caps — `--image-cache-cap-mib`
for the renderer's decoded-RGBA cache and `--glz-dictionary-cap-mib`
for the GLZ decompression dictionary — to keep the resulting
`CACHE_ME` pressure bounded. The GLZ dictionary in particular was
the dominant RSS contributor in this failure mode before it was
capped;
see [Glz dictionary pressure](/components/ryll/troubleshooting/#glz-dictionary-pressure)
in the troubleshooting guide.

### Cirrus, vmware-svga, bochs

Don't. Cirrus is 1990s hardware emulation; vmware-svga and bochs are
fallbacks for guests that lack better drivers. None of them offer the
SPICE-specific paths (dirty-rect queue, image cache, command ring) that
make the protocol worth using.

## SPICE channel settings

### Recommended graphics block

```xml
<graphics type='spice' port='-1' tls-port='-1' autoport='yes'
          listen='0.0.0.0'>
  <listen type='address' address='0.0.0.0'/>
  <image compression='auto_lz'/>
  <jpeg compression='auto'/>
  <zlib compression='auto'/>
  <playback compression='on'/>
  <streaming mode='filter'/>
</graphics>
```

Each of these maps to a `-spice` flag on the QEMU command line. The
defaults vary by distro, so spell them out explicitly.

### Why `image compression='auto_lz'`?

Default in many libvirt configurations is `auto_glz`. `glz` is a
dictionary-based zlib variant designed for static-UI surfaces — fine
in theory, but it produces large compressed payloads (~50% of raw
RGBA) and is slow to decode (~80 ms for a 2048×1152 frame on Apple
Silicon).

`auto_lz` switches the server's default to plain LZ, which is much
faster to decode (~10–20 ms for the same payload) at the cost of a
slightly worse compression ratio. Combined with ryll's advertised
`LZ4_COMPRESSION` capability, the server will pick LZ4 — even faster — for any frames that benefit
from it.

If you actually need glz (e.g. you are bandwidth-constrained over a
WAN link), keep `auto_glz` but at least drop the
`zlib-glz-wan-compression=always` override mentioned below.

### Why drop `*-wan-compression=always`?

Older libvirt templates often include:

```xml
<image compression='auto_glz'/>
<jpeg compression='always'/>
<zlib compression='always'/>
```

The `always` setting forces the server's "wan" code path on every
image, regardless of what the client advertises. This is what causes
ryll's `LZ4_COMPRESSION` and `PREF_COMPRESSION` capabilities, and
the `PREFERRED_COMPRESSION` message it sends on the strength of the
latter, to be silently ignored — the server has been told "I don't
care what the client wants, always use this."

Use `auto` instead so the server picks dynamically based on the
client's capabilities and the actual image characteristics.

### Why `streaming mode='filter'`?

The QEMU defaults are usually `streaming=off` or `streaming=all`. Both
extremes are wrong for typical desktop workloads:

- `off` — server never streams MJPEG/H.264, so video playback
  decompresses every frame as a static image. Murders bandwidth.
- `all` — server eagerly creates a stream for any region that changes
  more than a few times per second, including terminal cursor blinks
  and window-drag rubber-banding. Then tears the stream down 0.7 s
  later when the region stops moving, leaving the client to redraw
  the affected area as a static image. We have observed
  ten-streams-in-a-minute flap counts on `all`.
- `filter` — server uses a heuristic to decide which regions are
  actually video-like (high frame rate, large continuous area,
  smooth motion). Stable streams for real video, dirty-rect updates
  for everything else. This is the right answer for almost every
  workload.

Ryll decodes both MJPEG and H.264 streams client-side.
For sustained video playback with `streaming-video=filter`, the server will
prefer H.264 when available, which is typically more bandwidth-efficient than
MJPEG and results in cheaper sustained-video transmission.

**H.264 needs GStreamer plugins on the hypervisor.** spice-server's H.264
encoder is implemented through GStreamer
(`spice/server/gstreamer-encoder.c:1135`); without the GStreamer H.264
plugin packages installed, the server can only encode MJPEG even when
the client advertises `CODEC_H264`. On Debian/Ubuntu:

```bash
sudo apt-get install gstreamer1.0-plugins-bad gstreamer1.0-vaapi
```

On Fedora/RHEL, the corresponding `gstreamer1-plugins-bad-free-extras`
and `gstreamer1-vaapi` packages. This is a *necessary* condition for
H.264, not a sufficient one — the streaming heuristic still has to fire
in the first place (see the QXL resolution-cliff discussion above).
If `streaming-video=filter` chooses not to stream a region at all, no
codec — H.264 or MJPEG — gets exercised.

The server's `CACHE_ME` flag on every video frame drives client-side cache
pressure on both the renderer's image cache and the GLZ decompression
dictionary — tuning `streaming-video` to `filter` (or away from `all`)
reduces how often video frames hit either cache. See
[Display image cache pressure](/components/ryll/troubleshooting/#display-image-cache-pressure)
(`--image-cache-cap-mib`) and
[Glz dictionary pressure](/components/ryll/troubleshooting/#glz-dictionary-pressure)
(`--glz-dictionary-cap-mib`) in the troubleshooting guide for the
client-side cache-cap tuning that complements these server-side settings.

### TLS channels

```xml
<channel name='main' mode='secure'/>
<channel name='display' mode='secure'/>
<channel name='inputs' mode='secure'/>
<channel name='cursor' mode='secure'/>
<channel name='playback' mode='secure'/>
<channel name='record' mode='secure'/>
<channel name='smartcard' mode='secure'/>
<channel name='usbredir' mode='secure'/>
```

Force-secure all SPICE channels. The 2010s-vintage `mode='any'`
default lets the client pick plaintext for "non-sensitive" channels
(display, inputs, etc.) which is the wrong threat model — anyone who
can sniff the display can shoulder-surf the session.

Ryll has no preference for plaintext: TLS is a fixed cost at link
time and a negligible per-frame cost after. Always use TLS unless
you are doing protocol bring-up against a debug server.

## VD agent (clipboard, mouse mode, monitors config)

```xml
<channel type='spicevmc'>
  <target type='virtio' name='com.redhat.spice.0'/>
</channel>
```

`spicevmc` over a `virtserialport` named `com.redhat.spice.0` is the
channel that carries `VD_AGENT_*` messages. Install `spice-vdagent`
in the guest (`apt install spice-vdagent` on Debian/Ubuntu;
`dnf install spice-vdagent` on Fedora). Without it:

- The mouse pointer is stuck in server-mode (relative coordinates),
  which breaks tablet-style absolute positioning that the SPICE
  client expects.
- Clipboard sync between client and guest is broken.
- Dynamic monitor reconfiguration (e.g. window resize triggers a
  guest-side resolution change) does not work.

Ryll's bug-report `MainSnapshot::agent_request_count` and related
fields report whether the agent is responding to probes. A `0` agent reply count
in a bug report usually means `spice-vdagent` is not installed or
not running.

## USB redirection (optional)

```xml
<redirdev bus='usb' type='spicevmc'/>
<redirdev bus='usb' type='spicevmc'/>
<redirdev bus='usb' type='spicevmc'/>
<controller type='usb' index='0' model='ich9-ehci1'/>
<controller type='usb' index='0' model='ich9-uhci1'>
  <master startport='0'/>
</controller>
<controller type='usb' index='0' model='nec-xhci'/>
```

USB redirection works fine with ryll (we ship a usbredir channel
handler), but each `<redirdev>` adds a `usbredir` channel that has
to be set up at link time even if no device is attached. Three is the
historical libvirt default and is fine for most desktops. Set to one
if you only ever attach a single device; remove entirely if your
deployment policy forbids USB passthrough.

The xHCI (`nec-xhci`) controller is required for USB 3 devices. EHCI
(`ich9-ehci1`) plus the three UHCI companions cover USB 2.

## Audio (optional)

```xml
<audio id='1' type='spice'/>
<sound model='ich9'/>
```

`audio type='spice'` routes guest audio over the SPICE playback /
record channels. We ship handlers for both. If audio isn't needed in
your benchmark, omit both elements — they add channel-setup time and
playback decode load (~1 MiB/s on a typical media stream).

## CPU and memory sizing

These are not SPICE-specific, but they affect what the user perceives
through SPICE:

- **CPUs**: 4 vCPUs minimum for a desktop workload that includes a
  browser. 2 is enough for terminal-only testing. The guest's
  display server (X11 / Wayland) is single-threaded so adding cores
  beyond 8 rarely improves responsiveness.
- **RAM**: 4 GiB for terminal-only, 8 GiB for a desktop with a
  browser. The QXL display driver allocates from guest RAM for
  its image cache; under-provisioning it leads to thrashing that
  shows up as choppy redraws.
- **Disk**: virtio-blk with `cache='none'` and `io='native'` for
  benchmark VMs (predictable IO, no host page-cache effects).
  `cache='writeback'` is fine for daily-driver VMs where you care
  more about throughput than reproducibility.

## What the `-spice` flag looks like on the QEMU command line

If you are testing without libvirt, the equivalent QEMU flags for the
recommended block above:

```
-spice port=5930,tls-port=5931,addr=0.0.0.0,\
       disable-ticketing=on,\
       x509-dir=/etc/pki/libvirt-spice,\
       tls-channel=default,\
       image-compression=auto_lz,\
       jpeg-wan-compression=auto,\
       zlib-glz-wan-compression=auto,\
       playback-compression=on,\
       streaming-video=filter,\
       seamless-migration=on
```

(Substitute appropriate ports, x509 path, and ticketing policy for
your deployment.)

## Side-by-side testing recipe

To compare configurations cleanly:

1. Clone the VM (e.g. `virt-clone --original src --name dst --auto-clone`).
2. Apply one change to the cloned VM's XML (e.g. swap `qxl` for
   `virtio`, or change `image compression`).
3. Restart both VMs.
4. Connect with ryll to each in turn, run an identical workload
   (e.g. open a terminal, type for 60 seconds, then drag a window for
   60 seconds), and file a Display bug report at the end of each.
5. Compare the two bug reports' `recent_decodes`,
   `mjpeg_decode_recent_mean_us`, `decode_recent_mean_us`, and
   `streams_created_total` fields.

Useful single-change A/B pairs:

| A | B | What it tells you |
|---|---|---|
| `qxl` | `virtio` | Whether the QXL command ring is producing pathological full-frame updates. |
| `image compression='auto_glz'` | `image compression='auto_lz'` | Whether `ZlibGlzRgb` decode time is the dominant per-frame cost. |
| `streaming mode='all'` | `streaming mode='filter'` | Whether stream flapping is the dominant lag source. |
| `<jpeg compression='always'/>` | `<jpeg compression='auto'/>` | Whether the server is over-eagerly switching to MJPEG for non-video regions. |

The ryll bug-report fields make these comparisons cheap; the
`Display` report type in particular includes everything needed to
characterise an A/B difference.

**Alternative: Auto-snapshot mode for long-running tests** — Instead of
manually filing reports at specific points, use `ryll --auto-snapshot-interval 30`
to fire a bug-report zip every 30 seconds into a rolling cap throughout the test.
This eliminates the risk of missing a transient lag spike or flap event that
happens between manual report points. Compare metrics across snapshots before,
during, and after the test to see the full timeline of changes.

## Enabling server-side debug logging

When something looks server-driven (no streams created, mystery
fallback to `ZlibGlzRgb`, etc.) the spice-server's own
`spice_debug` / `spice_info` lines in the qemu log are the
canonical data source. Getting them to actually appear is
trickier than it looks:

- **`SPICE_DEBUG_LEVEL` is not a real env var.** Grepping
  spice-server, spice-common, and spice-gtk turns up no
  `g_getenv("SPICE_DEBUG_LEVEL")` anywhere. The spice log
  macros at
  `/srv/src-reference/spice/spice-common/common/log.h:62-72`
  just call `g_log` with `G_LOG_DOMAIN="Spice"`. The actual
  threshold knob is GLib's `G_MESSAGES_DEBUG`.

- **Setting `G_MESSAGES_DEBUG` in libvirtd's systemd unit
  doesn't reach qemu.** `virCommandAddEnvPassCommon` at
  `/srv/src-reference/libvirt/libvirt/src/util/vircommand.c:1446`
  whitelists exactly nine env vars (LC_ALL, LD_*, PATH, HOME,
  USER, LOGNAME, TMPDIR). Anything else set in libvirtd's
  environment is scrubbed before the qemu child fork.

The canonical libvirt path: add the env var per-domain via
`<qemu:env>` in the libvirt namespaced commandline extension:

```xml
<domain xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0' ...>
  ...
  <qemu:commandline>
    <qemu:env name='G_MESSAGES_DEBUG' value='Spice'/>
  </qemu:commandline>
</domain>
```

After `virsh edit` and a VM restart,
`/var/log/libvirt/qemu/<domain>.log` will gain `Spice-DEBUG:`
and `Spice-INFO:` lines from spice-server (rather than only
the `spice_warning` output that comes through under the
default).

Useful grep targets once the lines are flowing:

- `stream_create` — when the server decides to start a stream.
- `is_next_stream_frame` — frame-rate detector input/output.
- `red_drawable` — the per-draw filtering decisions the QXL
  resolution-cliff discussion above hinges on.
- `mjpeg_encoder` / `gstreamer_encoder` — codec path selection
  and per-frame encode behaviour.

If you don't want to edit every domain's XML (e.g. a test
harness that recreates VMs frequently), an alternative is to
replace `/usr/bin/kvm` with a small wrapper that sets the env
before exec'ing the real binary. dpkg-divert protects the
rename from being clobbered by package updates. This is
heavier-handed but applies to every VM the host runs.

## What ryll cannot fix from the client side

The server makes the encoding decisions. Ryll can:

- Advertise capabilities (`LZ4_COMPRESSION`, `STREAM_REPORT`,
  `PREF_COMPRESSION`, `PREF_VIDEO_CODEC_TYPE`, etc.) so the server
  *can* use efficient paths.
- Send the preference messages those last two capabilities unlock
  (`PREFERRED_COMPRESSION`, `PREFERRED_VIDEO_CODEC_TYPE`) at
  link-up, to bias server choices.
- Decode whatever arrives as fast as the host hardware allows.

But ryll cannot override a server config that says
`zlib-glz-wan-compression=always`. If the server is hard-configured
to use a slow encoding, the client just has to decode it. The
right fix in that case is server-side: update the libvirt XML per
the recommendations above.

## Shaken Fist enhancement requests

Ryll's primary deployment target is Shaken Fist, but a few of the
libvirt knobs recommended above are not currently exposed by the
Shaken Fist API. The gaps below are tracked here so anyone reading
the recommendations knows where the orchestrator-side ceiling is,
and what they would have to hand-roll if they wanted to bypass it.

### virgl / `accel3d` is not exposed through `--videospec`

The Shaken Fist videospec accepts `model`, `memory`, and `vdi`
fields, which the libvirt template
(`shakenfist/deploy/templates/libvirt.tmpl:199`) substitutes as:

```xml
<model type='{{video_model}}' vram='{{video_memory}}' heads='1' primary='yes'/>
```

There is no `<acceleration accel3d='...'/>` child element in the
template and no field in the API to populate one. That means
`--videospec model=virtio,...` always builds a guest with
`accel3d='no'`, and there is no API path to ask for virgl 3D
forwarding.

Whether this matters depends on the workload. For plain VDI
(terminal, browser scrolling without WebGL, office apps) virgl
adds nothing observable. For compositor-heavy desktops, WebGL,
or any guest-side OpenGL workload it would noticeably improve
*guest-side* responsiveness — but it would not change anything
ryll sees on the wire, because SPICE's GL passthrough mode
(`gl=on`, DMA-BUF) only works for a *local* SPICE client. For a
remote client like ryll the GL framebuffer is read back to a
normal framebuffer and shipped as ordinary bitmap blits.

**Workaround today:** create the instance through Shaken Fist as
normal, then edit the resulting libvirt domain XML by hand to
add `<acceleration accel3d='yes'/>` and restart the domain. The
hypervisor needs `virglrenderer` installed and ideally a real
GPU on the host; software rasterisation works but defeats the
purpose.

**To close the gap properly** would take an API field on the
videospec (e.g. `accel3d: bool`), a template change to emit the
`<acceleration>` element when set, and a hypervisor-side
prerequisite that operators have `virglrenderer` available. Not
on any ryll roadmap; filed here as a note for future Shaken Fist
work.
