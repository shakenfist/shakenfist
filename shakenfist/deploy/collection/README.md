# Shaken Fist Ansible collection (`shakenfist.shakenfist`)

This collection deploys [Shaken Fist](https://shakenfist.com/) — an
opinionated, minimal cloud orchestration platform for VM and network
management — onto hosts you already manage with Ansible.

The guiding principle is that Shaken Fist deploys its `sf-*` daemons on the
hosts you tell it about, against infrastructure (MariaDB, the network node)
whose addresses you tell it. The roles express *what a node runs* as plain
role variables; **your** playbook maps your inventory groups onto those
variables. No role in this collection reads an inventory group name or
another host's facts, so the roles compose cleanly with any inventory layout.

## Roles

| Role | Purpose |
|------|---------|
| `shakenfist.shakenfist.node` | Core node setup: packages, virtualenv, `/etc/sf/config`, systemd units, node registration. Folds in the database capability. **Arrives in a later step.** |
| `shakenfist.shakenfist.hypervisor` | Hypervisor host preparation: nested KVM detection/enable, KSM, `vhost_vsock`, SPICE TLS, and the libvirt AppArmor/config tweaks. Apply only to hosts where `node_is_hypervisor` is true. |
| `shakenfist.shakenfist.network` | Network node preparation: removes the distro `dnsmasq` unit, installs the DHCP/DNS templates, enables IPv4 forwarding, and validates the mesh interface MTU. Apply only to hosts where `node_is_network_node` is true. |
| `shakenfist.shakenfist.internal_ca` | Internal certificate authority: generates a CA on the control node, issues a per-host SPICE TLS certificate, and distributes the certificates to each host. |

The `node` role and the native Ansible modules (`sf_namespace`, `sf_network`,
`sf_instance`, `sf_snapshot`) arrive in later steps of the collection build-out
and are not yet present.

## Consuming the collection

Install the published collection on your Ansible control node:

```bash
ansible-galaxy collection install shakenfist.shakenfist
```

Then reference the roles by their fully-qualified collection name (FQCN) from
your own playbook, gating the capability roles on the relevant variables:

```yaml
- hosts: hypervisors
  become: true
  roles:
    - role: shakenfist.shakenfist.hypervisor

- hosts: network_node
  become: true
  roles:
    - role: shakenfist.shakenfist.network

- hosts: all
  become: true
  roles:
    - role: shakenfist.shakenfist.internal_ca
```

Your playbook is responsible for computing each role's input variables (for
example the per-node capability flags and any cluster-wide values) from your
inventory and passing them in. Example playbooks that demonstrate this mapping
ship under `examples/` in the Shaken Fist repository.

See each role's `meta/argument_specs.yml` for its full, documented variable
set.

## License

Apache-2.0. Copyright 2019 Michael Still and contributors.
