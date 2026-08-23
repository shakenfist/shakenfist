# Audit: Python version and type hints

## What we check

### Python version targeting

* `shakenfist`: target the newest Python version packaged by
  supported host operating systems (currently Debian 12 and
  Ubuntu 24.04).
* All other Python projects: target the oldest system Python from
  the supported client operating systems listed at
  https://images.shakenfist.com/README.

### Type hints

All projects should use mypy type hints. `shakenfist` has been
going through a staged rollout and should be excluded from strict
interpretation for now.

### Modern Python features

Features we prefer when available:

* The walrus operator (`:=`).
* f-strings.

## Template

No template -- these are code-level standards.

## Projects

| Project | Status | Issue |
|---------|--------|-------|
| agent-python | needs checking | |
| client-python | needs checking | |
| clingwrap | needs checking | |
| cloudgood | N/A | - |
| imago | N/A (Rust) | - |
| kerbside | needs checking | |
| kerbside-patches | N/A | - |
| library-utilities | needs checking | |
| occystrap | needs checking | |
| ryll | N/A (Rust) | - |
| shakenfist | partial (staged rollout) | |
