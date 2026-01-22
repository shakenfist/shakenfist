# Clingwrap Documentation

Clingwrap is a debugging information collection tool that bundles system state
data into a zip file for later analysis. Originally built for the
[Shaken Fist](https://shakenfist.com) project, it is a general-purpose tool
useful for any system debugging scenario.

## Key Features

- **Rapid collection**: Quickly gather comprehensive system diagnostics
- **Single output**: All data bundled into one compressed ZIP file for easy
  distribution
- **Flexible configuration**: YAML-based configuration files define what to
  collect
- **Multiple command types**: Support for files, directories, shell commands,
  and dynamic job generation
- **CI/CD friendly**: Designed for use in automated testing pipelines to
  capture failure diagnostics

## Documentation Index

### Getting Started

- [Installation](/components/clingwrap/installation/) - How to install clingwrap
- [Configuration](/components/clingwrap/configuration/) - Configuration file format and structure
- [Examples](/components/clingwrap/examples/) - Example configurations and use cases

### Reference

- [Command Types](/components/clingwrap/command-types/) - Detailed documentation of all command
  types

## Quick Start

Install clingwrap:

```bash
pip install clingwrap
```

Run with a built-in example configuration:

```bash
clingwrap gather --target shakenfist-ci-failure --output debug.zip
```

Or use your own configuration file:

```bash
clingwrap gather --target /path/to/config.cwd --output debug.zip
```

## Command Line Interface

The main command is `clingwrap gather` with the following options:

| Option | Required | Description |
|--------|----------|-------------|
| `--target` | No | Configuration file path or built-in example name |
| `--output` | Yes | Output ZIP file path |

If `--target` is omitted, clingwrap reads configuration from stdin.

## Output Format

Clingwrap produces a single ZIP file containing:

- Collected files and directories (preserving original paths)
- Shell command outputs (stored in `_commands/` directory)
- `clingwrap.log` - Execution log with timestamps and job tracking

## Built-in Examples

Clingwrap ships with example configurations that can be used by name:

| Name | Description |
|------|-------------|
| `shakenfist-ci-failure` | Comprehensive collection for Shaken Fist CI failures |
| `openstack-kolla-ansible` | Collection for OpenStack Kolla-Ansible deployments |

## External Resources

- [GitHub Repository](https://github.com/shakenfist/clingwrap)
- [Bug Tracker](https://github.com/shakenfist/clingwrap/issues)
- [Shaken Fist Project](https://shakenfist.com)

## License

Clingwrap is licensed under the Apache 2.0 license.
