# Phase 6: Windows Packaging

Parent plan: [PLAN-packaging.md](/components/ryll/plans/PLAN-packaging/)

## Goal

Produce a `.zip` archive containing `ryll.exe` from the
Windows CI build and upload it as an artifact. Update
installation documentation.

## Current state

- The Windows CI job already builds the release binary
  (with `--no-default-features` to skip capture) and runs
  tests.
- No artifact is produced from the Windows build.
- `docs/installation.md` has a Windows placeholder.

## Design decisions

### .zip, not .msi

Per the master plan, MSI installer is a future work item
requiring WiX toolset. A `.zip` with the `.exe` inside is
the simplest viable distribution — users extract and run.

### No capture on Windows

Per the master plan decision (question 3), `--capture` mode
is disabled on Windows. The installation docs should note
this.

### PowerShell for packaging

The Windows CI runner uses PowerShell by default. We use
`Compress-Archive` (built into PowerShell 5.1+, available
on all `windows-latest` runners) to create the zip.

## Changes

### Step 1: Add Windows zip creation to CI

Add steps to the Windows build job to package the binary
and upload it as an artifact:

```yaml
- name: Create Windows zip
  if: runner.os == 'Windows'
  shell: pwsh
  run: |
    Compress-Archive `
      -Path target\release\ryll.exe `
      -DestinationPath target\release\ryll-${{ matrix.target }}.zip

- name: Upload Windows zip
  if: runner.os == 'Windows'
  uses: actions/upload-artifact@v4
  with:
    name: windows-zip
    path: target/release/ryll-*.zip
    retention-days: 30
```

**Files changed:**
- `.github/workflows/ci.yml` — add two steps gated on
  `runner.os == 'Windows'`

**Commit:** standalone.

### Step 2: Update documentation

- Update `docs/installation.md` Windows section with real
  instructions.

**Files changed:**
- `docs/installation.md`

**Commit:** standalone.

## Step summary

| Step | Description | Files | Commit |
|------|-------------|-------|--------|
| 1 | Create Windows zip in CI | `.github/workflows/ci.yml` | Yes |
| 2 | Update documentation | `docs/installation.md` | Yes |

## Risks and mitigations

- **SmartScreen warning:** Unsigned `.exe` files trigger a
  Windows SmartScreen "unrecognized app" warning. Users must
  click "More info" then "Run anyway". Code signing is
  listed as future work in the master plan.

- **Missing Visual C++ Redistributable:** The MSVC-built
  binary may depend on `vcruntime140.dll`. This is included
  in the Visual C++ Redistributable, which is installed on
  most Windows machines. If not, users need to install it.
  In practice, Rust statically links the CRT by default on
  MSVC targets (the `+crt-static` target feature), so this
  is unlikely to be an issue.

## Open questions

None.
