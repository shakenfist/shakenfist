# Configuration

Clingwrap uses YAML configuration files to define what information to collect.
These files have the `.cwd` extension by convention.

## Configuration File Format

A configuration file is a YAML document containing a list of commands:

```yaml
---
- name: Descriptive name for the command
  type: shell
  command: uname -a
  destination: _commands/uname
```

Each command in the list is a dictionary with the following common fields:

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Human-readable description of what this command collects |
| `type` | Yes | Command type: `file`, `directory`, `shell`, or `shell_emitter` |
| `destination` | Varies | Path within the output ZIP file (not required for `shell_emitter`) |

Additional fields depend on the command type. See
[Command Types](/components/clingwrap/command-types/) for details.

## Destination Paths

The `destination` field specifies where collected data is stored in the output
ZIP file:

- **Shell command outputs**: By convention, store in `_commands/` directory
- **Files and directories**: Mirror the source path (e.g., `/etc/hosts` becomes
  `etc/hosts`)

Examples:

```yaml
# Shell output goes to _commands/
- name: Kernel version
  type: shell
  command: uname -a
  destination: _commands/uname

# File mirrors source path structure
- name: System hosts file
  type: file
  source: /etc/hosts
  destination: etc/hosts

# Directory mirrors source structure
- name: Apache configuration
  type: directory
  source: /etc/apache2
  destination: etc/apache2
```

## Loading Configuration

Clingwrap can load configuration from three sources:

### 1. File Path

Specify the full path to a configuration file:

```bash
clingwrap gather --target /path/to/config.cwd --output debug.zip
```

### 2. Built-in Example Name

Use the name of a shipped example (without path or extension):

```bash
clingwrap gather --target shakenfist-ci-failure --output debug.zip
```

Available built-in examples:

- `shakenfist-ci-failure` - Shaken Fist CI failure diagnostics
- `openstack-kolla-ansible` - OpenStack Kolla-Ansible diagnostics

### 3. Standard Input

Pipe configuration via stdin (omit `--target`):

```bash
cat config.cwd | clingwrap gather --output debug.zip
```

Or use a heredoc:

```bash
clingwrap gather --output debug.zip << 'EOF'
---
- name: Kernel version
  type: shell
  command: uname -a
  destination: _commands/uname
EOF
```

## Best Practices

### Organizing Commands

Group related commands together and use descriptive names:

```yaml
# System information
- name: Kernel version and architecture
  type: shell
  command: uname -a
  destination: _commands/uname

- name: OS release information
  type: file
  source: /etc/os-release
  destination: etc/os-release

# Network diagnostics
- name: Network interfaces
  type: shell
  command: ip link
  destination: _commands/ip-link
```

### Handling Missing Files

Clingwrap gracefully handles missing files - it logs the absence and continues.
This means you can include commands for files that may not exist on all systems:

```yaml
# These will be skipped if not present
- name: syslog
  type: file
  source: /var/log/syslog
  destination: var/log/syslog

- name: messages (RedHat systems)
  type: file
  source: /var/log/messages
  destination: var/log/messages
```

### Using Exclusion Patterns

For directory commands, use `exclude` to skip certain files:

```yaml
- name: Instance data (excluding disk images)
  type: directory
  source: /srv/instances
  destination: srv/instances
  exclude: "([hsv]d[ac-z]|nvme[0-9])"
```
