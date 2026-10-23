# Running ryll as a Kasm workspace

[Kasm Workspaces](https://kasm.com/) streams containerised desktop
applications to a browser. ryll can be packaged as one of those
applications, a *workspace*, so that a SPICE console is one click away
in the Kasm dashboard instead of running a different SPICE client
(such as Kasm's stock Remmina workspace) inside Kasm.

The image is a single-application desktop: one maximised ryll window,
restarted if it exits, with no panel. Kasm's own audio, clipboard and
resize plumbing carry ryll's audio, clipboard and window size to the
browser.

## What this does and does not buy you

A Kasm workspace is a ryll GUI running inside a container, and Kasm
re-encodes that container's X display for the browser. The SPICE
server's video is therefore decoded by ryll and encoded a second time
by KasmVNC, exactly as it is with Remmina in the same seat. The
workspace changes which client does the decoding, not how many codecs
the picture passes through.

For a browser path with one encode, use [`ryll --web`](/components/ryll/web-frontend/),
which serves the session to the browser over WebRTC directly. That
needs the browser to reach ryll's WebRTC media ports, which a remote
Kasm user often cannot; the workspace needs nothing but the Kasm
session itself.

## Building the image

```bash
make kasm-image
```

This builds the release binary and `.deb` in the devcontainer (it
depends on `make deb`), then builds a docker image tagged
`ryll-kasm:latest` from `tools/kasm/Dockerfile`. Override the tag with
`make kasm-image KASM_IMAGE=registry.example.com/ryll-kasm:dev`.

The image is based on Kasm's Debian core image, pinned by the
Dockerfile's `KASM_BASE` argument. Its Debian release must match the
devcontainer's, because the `.deb`'s dependencies name that release's
library packages. When the devcontainer moves to a new Debian release,
move `KASM_BASE` with it.

Kasm's base already carries everything ryll needs at runtime:
PulseAudio and its ALSA plugin (ryll's ALSA output reaches the browser
through Kasm's audio stream), Mesa for software OpenGL (containers
usually have no GPU, so egui renders on the CPU), and the X keyboard
libraries.

## Registering it with Kasm

On a single-server Kasm install the image built above is already in
the agent's local docker image store. For a multi-server install, push
it to a registry the agents can pull from.

!!! warning

    A Kasm agent whose *Auto Prune Images* setting is *Aggressive*
    deletes every image that no workspace uses, about every thirty
    seconds, and a freshly built `ryll-kasm` image is one of those
    until a workspace names it. Register the workspace before building
    the image (or rebuild once it is registered), or set the agent's
    prune mode to *Normal*.

In the Kasm admin UI, add a workspace:

- **Workspace type**: Container.
- **Docker image**: the tag you built, for example `ryll-kasm:latest`.
- **Cores and memory**: ryll itself is modest, but software OpenGL at
  a large browser size is CPU-bound; 2 cores and 2 GB is a reasonable
  start.
- **Docker Run Config Override**: the connection to make, as below.

## Choosing the connection

The startup script (`tools/kasm/custom_startup.sh`) builds ryll's
command line from these, in order:

| Source | Passed to ryll as | Use |
|--------|-------------------|-----|
| A URL Kasm hands the session (`KASM_URL` or `LAUNCH_URL` at startup, or `--url` on an exec into a running session) | `--url <URL>` | A link to a `.vv` file, so one workspace can open any console |
| The launch form's `connection` answer | `--url <URL>` for an `http(s)://` link, otherwise `--direct <HOST:PORT>` | Asking the user which server to open each time the workspace starts |
| `RYLL_KASM_DIRECT` | `--direct <HOST:PORT>` | A workspace pinned to one SPICE server |
| The launch form's `password` answer | `--password-file <PATH>`, via a private file in the container | With a `--direct` connection, the server's password, asked for at launch |
| `RYLL_KASM_PASSWORD_FILE` | `--password-file <PATH>` | With a `--direct` connection and no password typed into the form, a file inside the container holding the server's password |
| `APP_ARGS` | appended, split on whitespace (quotes are not interpreted, so no argument can contain a space) | Extra ryll flags, for example `--no-h264` or `-v` |

With no URL, no launch form answer and no `RYLL_KASM_DIRECT`, the
script logs an error and starts nothing. When the launch form is the
only possible source, the script waits up to thirty seconds for Kasm
to deliver its answers first. A workspace that has a launch form *and*
`RYLL_KASM_DIRECT` (a default server, say, with a password field)
should also set `RYLL_KASM_LAUNCH_FORM` to `1`; otherwise the script
does not wait, and answers that arrive late are ignored. If the
answers cannot be parsed as JSON, the script logs an error and starts
nothing.

### Asking at launch

Kasm can show a form before a workspace starts and hand the answers to
the container. Paste the contents of `tools/kasm/launch_config.json`
into the workspace's *Launch Config* field to ask for:

- **SPICE server**: `HOST:PORT`, `HOST:PORT:TLS_PORT`, or an
  `http(s)://` link to a `.vv` file. Kasm offers to remember it.
- **Password**: optional and masked, for a `HOST:PORT` server. It is
  never remembered: Kasm keeps remembered answers in the browser's
  local storage, which is no place for a password. Leave it empty to
  fall back to `RYLL_KASM_PASSWORD_FILE`, so a workspace whose server
  password is mounted (as below) never needs it typed.

Kasm writes the answers as a JSON object to `/tmp/launch_selections.json`
in the container, the destination `launch_config.json` names. If you
change that destination, set `RYLL_KASM_LAUNCH_SELECTIONS` to match.

That file holds a typed password in plain text for the life of the
session, and so does the private file the script writes it to for
ryll (in `/dev/shm`, or `XDG_RUNTIME_DIR` if that is set). Both are
outside the home directory, so Kasm's persistent profiles do not save
them, and both go when the container is destroyed.

For example, a workspace pinned to a SPICE server that listens on the
Kasm docker bridge at `172.18.0.1:5900`:

```json
{
  "environment": {
    "RYLL_KASM_DIRECT": "172.18.0.1:5900"
  }
}
```

If the server wants a password, keep it out of the environment (Kasm
shows the override in its admin UI, and `docker inspect` shows it to
anyone on the host). Instead, map the file holding it into the
container with the workspace's *Volume Mappings*, read-only, and
point `RYLL_KASM_PASSWORD_FILE` at it. For andris, whose ticket lives
in `~/.config/andris/ticket` on the host:

```json
{
  "/home/USER/.config/andris/ticket": {
    "bind": "/run/ryll/ticket",
    "mode": "ro",
    "uid": 1000,
    "gid": 1000,
    "required": true,
    "skip_check": false
  }
}
```

```json
{
  "environment": {
    "RYLL_KASM_DIRECT": "172.18.0.1:5900",
    "RYLL_KASM_PASSWORD_FILE": "/run/ryll/ticket"
  }
}
```

The ticket file is private to its owner, so it is readable in the
container only if the owner's uid matches the container's
`kasm-user` (1000).

The SPICE server must be reachable from the workspace container's
network. A server that listens only on the host's loopback is not;
relay it onto the docker bridge (for example with a
`systemd-socket-proxyd` socket unit bound to the bridge address), or
bind it there directly.

## Known gaps

This packaging is new and has not been exercised much. Things worth
checking when you first use it:

- **Resize.** The chain is browser size, then KasmVNC's display size,
  then the maximised ryll window, then the SPICE monitors-config sent
  to the guest. ryll leaves a maximised window's size alone (see
  [window sizing](/components/ryll/rendering-pipeline/#window-sizing)), so the guest
  follows the browser only if the SPICE server honours the resize.
- **Keyboard.** KasmVNC turns the browser's key events into synthetic
  X key codes inside the container, and ryll maps X key codes to PC
  scancodes by physical position. Layouts other than US, and modifier
  combinations, are where a double translation like that goes wrong.
- **Clipboard.** Browser clipboard to KasmVNC to the container's X
  clipboard to ryll to the guest's vdagent; each hop has its own
  rules about when it syncs.
- **USB redirection and folder sharing** only see devices and
  directories inside the container, so they are of little use here.
