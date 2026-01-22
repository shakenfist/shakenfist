# Examples

This document provides example configurations for various use cases.

## Basic System Diagnostics

A minimal configuration for basic system information:

```yaml
---
- name: Kernel version and architecture
  type: shell
  command: uname -a
  destination: _commands/uname

- name: OS release information
  type: file
  source: /etc/os-release
  destination: etc/os-release

- name: Hostname
  type: shell
  command: hostname -f
  destination: _commands/hostname

- name: Running processes
  type: shell
  command: ps -aux
  destination: _commands/ps-aux

- name: System logs
  type: file
  source: /var/log/syslog
  destination: var/log/syslog
```

## Network Diagnostics

Collect comprehensive network information:

```yaml
---
- name: Network interfaces
  type: shell
  command: ip link
  destination: _commands/ip-link

- name: Network addresses
  type: shell
  command: ip addr
  destination: _commands/ip-addr

- name: Routing table
  type: shell
  command: ip route
  destination: _commands/ip-route

- name: Firewall rules
  type: shell
  command: iptables -L -v -n
  destination: _commands/iptables

- name: Listening ports
  type: shell
  command: ss -tulpn
  destination: _commands/ss-listening

- name: Network connections
  type: shell
  command: ss -tupn
  destination: _commands/ss-connections

- name: DNS configuration
  type: file
  source: /etc/resolv.conf
  destination: etc/resolv.conf
```

## Docker/Container Diagnostics

Collect information about Docker containers:

```yaml
---
- name: Docker daemon info
  type: shell
  command: docker info
  destination: _commands/docker-info

- name: Running containers
  type: shell
  command: docker ps -a
  destination: _commands/docker-ps

- name: Docker images
  type: shell
  command: docker images
  destination: _commands/docker-images

- name: Docker networks
  type: shell
  command: docker network ls
  destination: _commands/docker-network-ls

- name: Docker configuration
  type: directory
  source: /etc/docker
  destination: etc/docker

- name: Container details
  type: shell_emitter
  command: |
    for container in `docker ps --all --format '{{ .Names }}'`
    do
      echo "
    - name: Inspect ${container}
      type: shell
      command: docker inspect ${container}
      destination: _commands/docker/${container}/inspect

    - name: Logs for ${container}
      type: shell
      command: docker logs --tail 2000 ${container}
      destination: _commands/docker/${container}/logs
    "
    done
```

## Kubernetes Diagnostics

Collect information from a Kubernetes node:

```yaml
---
- name: Kubectl version
  type: shell
  command: kubectl version
  destination: _commands/kubectl-version

- name: Node status
  type: shell
  command: kubectl get nodes -o wide
  destination: _commands/nodes

- name: All pods
  type: shell
  command: kubectl get pods --all-namespaces -o wide
  destination: _commands/pods-all

- name: Pod details
  type: shell_emitter
  command: |
    kubectl get pods --all-namespaces -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' | while read pod
    do
      ns=$(echo $pod | cut -d/ -f1)
      name=$(echo $pod | cut -d/ -f2)
      echo "
    - name: Describe pod ${name} in ${ns}
      type: shell
      command: kubectl describe pod ${name} -n ${ns}
      destination: _commands/k8s/${ns}/${name}/describe

    - name: Logs for ${name} in ${ns}
      type: shell
      command: kubectl logs ${name} -n ${ns} --tail=1000
      destination: _commands/k8s/${ns}/${name}/logs
    "
    done

- name: Kubelet logs
  type: shell
  command: journalctl -u kubelet --no-pager -n 1000
  destination: _commands/journalctl-kubelet
```

## Systemd Service Diagnostics

Collect information about systemd services:

```yaml
---
- name: Systemd status
  type: shell
  command: systemctl status
  destination: _commands/systemctl-status

- name: Failed units
  type: shell
  command: systemctl --failed
  destination: _commands/systemctl-failed

- name: All services
  type: shell
  command: systemctl list-units --type=service
  destination: _commands/services

- name: Service logs
  type: shell_emitter
  command: |
    for service in nginx apache2 mysql postgresql docker
    do
      echo "
    - name: Journal for ${service}
      type: shell
      command: journalctl -u ${service} --no-pager -n 500
      destination: _commands/journalctl/${service}
    "
    done
```

## Application-Specific Collection

Example for a custom application with its own log directory:

```yaml
---
- name: Application configuration
  type: directory
  source: /etc/myapp
  destination: etc/myapp

- name: Application logs
  type: directory
  source: /var/log/myapp
  destination: var/log/myapp
  exclude: ".*\\.gz$"  # Skip compressed old logs

- name: Application data (excluding large files)
  type: directory
  source: /var/lib/myapp
  destination: var/lib/myapp
  exclude: "(cache|tmp|.*\\.db$)"

- name: Application status
  type: shell
  command: myapp-cli status
  destination: _commands/myapp-status

- name: Application health check
  type: shell
  command: curl -s http://localhost:8080/health
  destination: _commands/myapp-health
```

## Built-in Examples

Clingwrap ships with two comprehensive examples.

### Shaken Fist CI Failure

Use with:

```bash
clingwrap gather --target shakenfist-ci-failure --output debug.zip
```

This collects:

- System information (kernel, OS, hostname)
- Package lists (dpkg, pip)
- Configuration directories (Apache, AppArmor, Shaken Fist)
- Service logs (etcd, libvirtd, systemd units)
- Network diagnostics including namespaces
- VXLAN network details
- Libvirt VM list
- Shaken Fist instances and events (excluding disk images)
- etcd database contents
- Docker information

### OpenStack Kolla-Ansible

Use with:

```bash
clingwrap gather --target openstack-kolla-ansible --output debug.zip
```

This collects:

- System information (kernel, OS, hostname)
- Kolla configuration
- Package lists (dpkg, rpm, pip)
- Systemd status
- Syslog files
- Kolla-Ansible logs
- Running processes
- Docker containers with inspection and logs
- Container pip package lists
- Package manager configurations

## Running Examples

### Using a Built-in Example

```bash
clingwrap gather --target shakenfist-ci-failure --output debug.zip
```

### Using a Custom Configuration File

```bash
clingwrap gather --target /path/to/myconfig.cwd --output debug.zip
```

### Using Stdin

```bash
cat myconfig.cwd | clingwrap gather --output debug.zip
```

### With Elevated Privileges

Many diagnostics require root access:

```bash
sudo clingwrap gather --target myconfig.cwd --output debug.zip
```
