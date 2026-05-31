# Ryll Documentation

## What is Ryll?

Ryll is a purpose-built SPICE VDI test client, written in Rust. It exists to
support performance testing of the **Kerbside** SPICE proxy.

## Background

[Kerbside](https://github.com/shakenfist/kerbside) is a SPICE protocol native
proxy that sits between SPICE clients and servers. Unlike layer 4 proxies
that simply pass through unparsed traffic, Kerbside understands the SPICE
protocol itself, allowing it to make intelligent routing and optimization
decisions.

Getting Kerbside integrated into OpenStack has been a long process (see
[kerbside-patches](https://github.com/shakenfist/kerbside-patches)), and now
the focus has started to shift to performance validation.

## What's With The Name?

Honestly, I am not super into Star Wars or anything, but "ryll" was the first
good spice pun I came across. To quote the excellently named Wookieepedia:

> Ryll was a precious ore harvested from the mines of Ryloth, and could also
> be found in the Krost Mountains on Aaloth. It had military and scientific
> applications, but could also be used as a drug in the form of refined spice,
> which was less effective than glitterstim.
> 
> https://starwars.fandom.com/wiki/Ryll

I guess I should think of a project to name "glitterstim" now too.

## Why Build a Custom Client?

Existing SPICE clients like `remote-viewer` (spice-gtk) are designed for
end-user use. They work well for connecting to VMs, but they're not designed
to be instrumented for performance testing.

Ryll was built to:

1. **Generate controlled traffic** - Predictable workloads for benchmarking
2. **Measure latency precisely** - Track time from keystroke to display update
3. **Run headless** - Automated testing without GUI overhead
4. **Be fully instrumented** - Every metric needed for proxy performance analysis

## The Testing Setup

```
┌─────────┐         ┌───────────┐         ┌─────────────┐
│  ryll   │────────▶│ kerbside  │────────▶│ SPICE server│
│ (client)│         │  (proxy)  │         │   (QEMU)    │
└─────────┘         └───────────┘         └─────────────┘
     │                    │                      │
     │                    │                      │
     ▼                    ▼                      ▼
  Metrics:            Metrics:              Metrics:
  - Latency           - Throughput          - Server-side
  - Frame rate        - Connection time       processing
  - Bytes in/out      - Protocol overhead
```

With ryll, we can:

- Measure end-to-end latency through the proxy
- Compare performance with and without the proxy
- Identify bottlenecks in the proxy implementation
- Validate that the proxy doesn't degrade user experience

## Future Direction

It's likely that a **custom SPICE server** will also be needed, to control
both ends of the traffic through the proxy. This would allow:

- Generating specific display patterns (gradients, text, video-like content)
- Precise timing of server-side events
- Controlled latency injection for testing
- Complete instrumentation of the entire path

## Documentation Index

- [Binary Portability](/components/ryll/portability/) - How to share built binaries
- [Channel Diagnostics Audit](/components/ryll/channel-diagnostics-audit/) - Per-channel observability checklist
- [Configuration](/components/ryll/configuration/) - CLI options and .vv file format
- [Libvirt / SPICE Server Recommendations](/components/ryll/libvirt-spice-recommendations/) - Guest XML settings for best display responsiveness
- [macOS Development](/components/ryll/development-macos/) - Build and test locally on macOS
- [Troubleshooting](/components/ryll/troubleshooting/) - Common issues and debugging

## Project Files

- [README](/components/ryll/../README/) - Quick start and usage
- [ARCHITECTURE](/components/ryll/../ARCHITECTURE/) - Technical design details
- [AGENTS](/components/ryll/../AGENTS/) - Guide for AI coding assistants
