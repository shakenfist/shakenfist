# Plans index

This page summarises every planning document in chronological order. Master
plans decompose work into numbered phases, each with its own detailed plan
file. Standalone plans track issues, follow-ups, or design decisions that
do not require phased execution.

## Master plans

| Date | Plan | Intent | Status | Phases |
|------|------|--------|--------|--------|
| 2026-04-01 | [Initial porting plan](/components/ryll/plans/PLAN-initial/) | Port the ryll SPICE client from Python to Rust with egui | Complete | (design document) |
| 2026-04-01 | [Capture mode](/components/ryll/plans/PLAN-capture/) | Protocol traffic pcap and display frame video capture for debugging | Complete | [1. Infrastructure](/components/ryll/plans/PLAN-capture-phase-01-infra/), [2. Pcap](/components/ryll/plans/PLAN-capture-phase-02-pcap/), [3. Video](/components/ryll/plans/PLAN-capture-phase-03-video/) |
| 2026-04-01 | [Packaging](/components/ryll/plans/PLAN-packaging/) | Cross-platform packaging for Debian, RPM, macOS, and Windows | Complete | [1. Portability](/components/ryll/plans/PLAN-packaging-phase-01-portability/), [2. CI](/components/ryll/plans/PLAN-packaging-phase-02-ci/), [3. Debian](/components/ryll/plans/PLAN-packaging-phase-03-debian/), [4. RPM](/components/ryll/plans/PLAN-packaging-phase-04-rpm/), [5. macOS](/components/ryll/plans/PLAN-packaging-phase-05-macos/), [6. Windows](/components/ryll/plans/PLAN-packaging-phase-06-windows/), [7. Release](/components/ryll/plans/PLAN-packaging-phase-07-release/) |
| 2026-04-02 | [USB redirection](/components/ryll/plans/PLAN-usb-redir/) | USB device redirection via the SPICE usbredir channel | Complete | [1. VMC channel](/components/ryll/plans/PLAN-usb-redir-phase-01-vmc-channel/), [2. Parser](/components/ryll/plans/PLAN-usb-redir-phase-02-usbredir-parser/), [3. Backend trait](/components/ryll/plans/PLAN-usb-redir-phase-03-device-backend/), [4. Real devices](/components/ryll/plans/PLAN-usb-redir-phase-04-real-devices/), [5. Connect](/components/ryll/plans/PLAN-usb-redir-phase-05-device-connect/), [6. Transfers](/components/ryll/plans/PLAN-usb-redir-phase-06-transfers/), [7. Virtual MSC](/components/ryll/plans/PLAN-usb-redir-phase-07-virtual-msc/), [8. UI](/components/ryll/plans/PLAN-usb-redir-phase-08-ui/), [9. Interrupt](/components/ryll/plans/PLAN-usb-redir-phase-09-interrupt/), [10. Testing](/components/ryll/plans/PLAN-usb-redir-phase-10-testing/) |
| 2026-04-03 | [Cursor rendering](/components/ryll/plans/PLAN-cursor-rendering/) | Render SPICE server-provided cursor as an egui overlay | Complete | [1. Parse](/components/ryll/plans/PLAN-cursor-rendering-phase-01-parse/), [2. Render](/components/ryll/plans/PLAN-cursor-rendering-phase-02-render/) |
| 2026-04-04 | [Bug reports](/components/ryll/plans/PLAN-bug-reports/) | Interactive bug reporting with protocol ring buffers and display region selection | Complete | [1. Ring buffer](/components/ryll/plans/PLAN-bug-reports-phase-01-ring-buffer/), [2. Channel state](/components/ryll/plans/PLAN-bug-reports-phase-02-channel-state/), [3. Zip output](/components/ryll/plans/PLAN-bug-reports-phase-03-zip-output/), [4. GUI button](/components/ryll/plans/PLAN-bug-reports-phase-04-gui-button/), [5. Region select](/components/ryll/plans/PLAN-bug-reports-phase-05-region-select/), [6. Traffic viewer](/components/ryll/plans/PLAN-bug-reports-phase-06-traffic-viewer/), [7. Docs](/components/ryll/plans/PLAN-bug-reports-phase-07-docs/) |
| 2026-04-05 | [USB UI](/components/ryll/plans/PLAN-usb-ui/) | Interactive USB device management panel on the status bar | Complete | [1. Bus fix](/components/ryll/plans/PLAN-usb-ui-phase-01-bus-fix/), [2. Wire tx](/components/ryll/plans/PLAN-usb-ui-phase-02-wire-tx/), [3. Panel](/components/ryll/plans/PLAN-usb-ui-phase-03-panel/), [4. Enumerate](/components/ryll/plans/PLAN-usb-ui-phase-04-enumerate/), [5. Connect](/components/ryll/plans/PLAN-usb-ui-phase-05-connect/), [6. Add disk](/components/ryll/plans/PLAN-usb-ui-phase-06-add-disk/), [7. Polish](/components/ryll/plans/PLAN-usb-ui-phase-07-polish/), [8. Docs](/components/ryll/plans/PLAN-usb-ui-phase-08-docs/) |
| 2026-04-06 | [WebDAV](/components/ryll/plans/PLAN-webdav/) | WebDAV folder sharing via the SPICE port channel | Complete | [1. Port channel](/components/ryll/plans/PLAN-webdav-phase-01-port-channel/), [2. Mux protocol](/components/ryll/plans/PLAN-webdav-phase-02-mux-protocol/), [3. WebDAV server](/components/ryll/plans/PLAN-webdav-phase-03-webdav-server/), [4. Integration](/components/ryll/plans/PLAN-webdav-phase-04-integration/), [5. UI](/components/ryll/plans/PLAN-webdav-phase-05-ui/), [6. Testing](/components/ryll/plans/PLAN-webdav-phase-06-testing/) |
| 2026-04-08 | [Crate extraction](/components/ryll/plans/PLAN-crate-extraction/) | Extract compression, protocol, and usbredir crates for reuse | Complete | [1. Workspace](/components/ryll/plans/PLAN-crate-extraction-phase-01-workspace/), [2. Reserve names](/components/ryll/plans/PLAN-crate-extraction-phase-02-reserve-names/), [3. Compression](/components/ryll/plans/PLAN-crate-extraction-phase-03-compression/), [4. Protocol](/components/ryll/plans/PLAN-crate-extraction-phase-04-protocol/), [5. Usbredir](/components/ryll/plans/PLAN-crate-extraction-phase-05-usbredir/), [6. Client](/components/ryll/plans/PLAN-crate-extraction-phase-06-client/) |

## Standalone plans

These plans track issues, follow-ups, or deferred work without phased
execution.

| Date | Plan | Intent |
|------|------|--------|
| 2026-04-01 | [Remaining issues](/components/ryll/plans/PLAN-remaining-issues/) | Outstanding issues after the initial Rust port bring-up |
| 2026-04-08 | [Display iteration follow-ups](/components/ryll/plans/PLAN-display-iteration-followups/) | Deferred work from display rendering, QUIC decode, and multi-monitor PRs |
| 2026-04-11 | [PR #20 follow-up](/components/ryll/plans/PLAN-pr20-followup/) | Follow-up fixes from clipboard, MJPEG, and disconnect handling |
| 2026-04-11 | [PR #23 follow-up](/components/ryll/plans/PLAN-pr23-followup/) | Follow-up fixes from audio playback channel integration |
