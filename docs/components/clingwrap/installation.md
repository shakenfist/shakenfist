# Installation

## Requirements

- Python 3.7 or later
- pip (Python package installer)

## Installing from PyPI

The simplest way to install clingwrap is from PyPI:

```bash
pip install clingwrap
```

## Installing from Source

To install from source, clone the repository and install using pip:

```bash
git clone https://github.com/shakenfist/clingwrap.git
cd clingwrap
pip install .
```

For development installations with editable mode:

```bash
pip install -e .
```

## Dependencies

Clingwrap has the following runtime dependencies (installed automatically):

| Package | License | Purpose |
|---------|---------|---------|
| `oslo.concurrency` | Apache 2.0 | Process execution with timeouts |
| `click` (>=7.1.1) | BSD | Command-line interface framework |
| `pyyaml` | MIT | YAML configuration file parsing |
| `shakenfist-utilities` | Apache 2.0 | Shared utility functions |

## Development Dependencies

For running tests and development work, install the test dependencies:

```bash
pip install clingwrap[test]
```

This installs additional packages:

| Package | Purpose |
|---------|---------|
| `coverage` | Code coverage analysis |
| `testtools` | Test utilities |
| `mock` | Mocking library |
| `stestr` | Test runner |
| `tox` | Test automation |
| `flake8` | Code linting |

## Verifying Installation

After installation, verify clingwrap is available:

```bash
clingwrap --help
```

You should see output showing the available commands and options.

## Permissions

Clingwrap collects system information, which may require elevated privileges
depending on what you want to collect. Common scenarios requiring root or sudo:

- Reading system logs (`/var/log/syslog`)
- Accessing container information (`docker` commands)
- Reading protected configuration files
- Executing privileged commands (e.g., `iptables`, `etcdctl`)

Run with appropriate permissions based on your collection requirements:

```bash
sudo clingwrap gather --target myconfig.cwd --output debug.zip
```
