# Command Types

Clingwrap supports four command types for collecting different kinds of
information.

## File Commands

Copy individual files to the output ZIP.

### Syntax

```yaml
- name: Description of the file
  type: file
  source: /path/to/source/file
  destination: path/in/zip
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Human-readable description |
| `type` | Yes | Must be `file` |
| `source` | Yes | Absolute path to the source file |
| `destination` | Yes | Path within the output ZIP |

### Behavior

- If the source file exists, it is copied to the ZIP using streaming (memory
  efficient)
- If the source file is missing, a placeholder is written:
  `--- file /path/to/file was absent ---`
- If an error occurs reading the file, the error is captured:
  `--- file /path/to/file exception: <error> ---`

### Example

```yaml
- name: System hosts file
  type: file
  source: /etc/hosts
  destination: etc/hosts

- name: Syslog
  type: file
  source: /var/log/syslog
  destination: var/log/syslog
```

## Directory Commands

Recursively copy all files in a directory hierarchy.

### Syntax

```yaml
- name: Description of the directory
  type: directory
  source: /path/to/source/directory
  destination: path/in/zip
  exclude: "optional-regex-pattern"
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Human-readable description |
| `type` | Yes | Must be `directory` |
| `source` | Yes | Absolute path to the source directory |
| `destination` | Yes | Base path within the output ZIP |
| `exclude` | No | Regular expression pattern for files to skip |

### Behavior

- Walks the directory tree recursively
- Creates a file job for each file found
- Files matching the `exclude` pattern are skipped
- Processes files depth-first for memory efficiency

### Exclude Pattern

The `exclude` field accepts a Python regular expression. Files whose full path
matches the pattern are skipped.

Examples:

```yaml
# Skip disk device files
exclude: "([hsv]d[ac-z]|nvme[0-9])"

# Skip lock files
exclude: ".*\\.lock"

# Skip backup files
exclude: ".*~$"
```

### Example

```yaml
- name: Apache configuration
  type: directory
  source: /etc/apache2
  destination: etc/apache2

- name: Instance data (excluding disk images)
  type: directory
  source: /srv/shakenfist/instances
  destination: srv/shakenfist/instances
  exclude: "([hsv]d[ac-z]|nvme[0-9]|sc-.*)"
```

## Shell Commands

Execute shell commands and capture their output.

### Syntax

```yaml
- name: Description of the command
  type: shell
  command: shell command to execute
  destination: _commands/output-name
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Human-readable description |
| `type` | Yes | Must be `shell` |
| `command` | Yes | Shell command or script to execute |
| `destination` | Yes | Path within the output ZIP |

### Behavior

- Commands are executed in a shell with a 30-second timeout
- Both stdout and stderr are captured
- Output format includes the command and separated stdout/stderr sections
- Exceptions are captured and included in the output

### Output Format

```
# <command>

----- stdout -----
<stdout content>

----- stderr -----
<stderr content>
```

If an exception occurs:

```
# <command>

----- stdout -----

----- stderr -----

----- exception -----
<exception details>
```

### Multi-line Scripts

The `command` field supports multi-line scripts using YAML block scalars:

```yaml
- name: Complex diagnostics
  type: shell
  command: |
    echo "System Info"
    uname -a
    echo ""
    echo "Memory Info"
    free -h
  destination: _commands/system-info
```

### Example

```yaml
- name: Kernel version
  type: shell
  command: uname -a
  destination: _commands/uname

- name: Network interfaces
  type: shell
  command: ip link
  destination: _commands/ip-link

- name: Installed packages
  type: shell
  command: dpkg -l
  destination: _commands/dpkg
```

## Shell Emitter Commands

Run shell scripts that dynamically generate additional commands. This is
powerful for discovering objects at runtime and creating jobs for each.

### Syntax

```yaml
- name: Description
  shell_emitter: |
    script that outputs YAML command definitions
```

Or with explicit type:

```yaml
- name: Description
  type: shell_emitter
  command: |
    script that outputs YAML command definitions
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Human-readable description |
| `type` | Yes | Must be `shell_emitter` |
| `command` | Yes | Shell script that outputs YAML |

Note: `shell_emitter` commands do not have a `destination` field - they
generate other commands that have destinations.

### Behavior

- Script is executed with a 60-second timeout
- Script stdout must be valid YAML containing command definitions
- Generated commands are processed immediately (depth-first)
- Useful for iterating over dynamic lists (containers, namespaces, etc.)

### Output Format

The script must output valid YAML command definitions. Each generated command
should be a complete command definition with all required fields.

### Example: Network Namespaces

Discover and collect information from all network namespaces:

```yaml
- name: Network namespaces
  type: shell_emitter
  command: |
    for item in `find /var/run/netns -type f | sed 's/.*\///'`
    do
      echo "
    - name: Network interfaces (namespace ${item})
      type: shell
      command: ip netns exec ${item} ip link
      destination: _commands/netns-${item}/ip-link

    - name: Network routes (namespace ${item})
      type: shell
      command: ip netns exec ${item} ip route
      destination: _commands/netns-${item}/ip-route
    "
    done
```

### Example: Docker Containers

Collect information from all running Docker containers:

```yaml
- name: Container inspection
  type: shell_emitter
  command: |
    for item in `docker ps --all --format '{{ .Names }}'`
    do
      echo "
    - name: Inspect ${item} container
      type: shell
      command: docker inspect ${item}
      destination: _commands/docker-${item}-inspect

    - name: Docker logs for ${item} container
      type: shell
      command: docker logs --tail 2000 ${item}
      destination: _commands/docker-${item}-logs
    "
    done
```

### Tips for Shell Emitters

1. **Quote YAML properly**: Be careful with indentation and quoting in the
   generated YAML
2. **Handle empty results**: If the loop produces no output, that's fine -
   no jobs will be generated
3. **Use echo with heredoc-style output**: The example pattern with echo and
   embedded YAML works well
4. **Limit output**: For log collection, use `--tail` or similar to avoid
   collecting excessive data
