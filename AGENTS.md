# AGENTS.md - AI Agent Instructions for Shaken Fist

This file provides instructions for AI agents (Claude Code, GitHub Copilot, etc.)
working on the Shaken Fist codebase.

## Project Context

Shaken Fist is a minimal cloud orchestration platform for VM and network
management. See [CLAUDE.md](CLAUDE.md) for detailed development guidance.

## CI/CD Architecture

### GitHub Actions Workflows

The repository uses several GitHub Actions workflows:

| Workflow | Purpose | Trigger |
|----------|---------|---------|
| `functional-tests.yml` | Main CI: lint, unit tests, functional tests | PR, merge_group |
| `documentation-tests.yml` | Build and test documentation | PR |
| `pin-indirect-dependencies.yml` | Keep indirect dependencies pinned | Daily schedule |
| `export-repo-config.yml` | Export GitHub repo settings to version control | Daily schedule |
| `pr-re-review.yml` | Re-review PR on bot command | `@shakenfist-bot please re-review` |
| `pr-address-comments.yml` | Address review comments on bot command | `@shakenfist-bot please address comments` |
| `pr-fix-tests.yml` | Fix test failures on bot command | `@shakenfist-bot please attempt to fix` |
| `test-drift-fix.yml` | Unit test fixer (called by pr-fix-tests) | workflow_call, workflow_dispatch |

### Merge Queue Pattern

The CI uses a two-stage merge queue pattern (see [this blog post](https://boinkor.net/2023/11/neat-github-actions-patterns-for-github-merge-queues/)):

1. **`Can enqueue`** - Runs on `pull_request` events, gates entry to merge queue
2. **`Can merge`** - Runs on `merge_group` events, gates the actual merge

**Important**: Only `Can see status` and `Can enqueue` are required status checks
in branch protection. `Can merge` is evaluated by the merge queue itself, not as
a required check.

### Exported Repository Configuration

Repository settings (rulesets, branch protection, merge queue config) are
exported to `.github/exported-config/` for version control and audit purposes:

- `repository-settings.json` - Repo-level settings
- `rulesets-summary.json` - List of all rulesets
- `ruleset-*.json` - Full details for each ruleset

If the `export-repo-config` workflow creates a PR, it means GitHub UI settings
have changed and should be reviewed.

## Automated CI Jobs

### Automated Delinter

When flake8 fails, the `automated_delinter` job runs Claude Code to fix lint
errors automatically. It skips if the last commit was from the bot to prevent
loops.

### Automated Exception Fixer

When functional tests detect exceptions in logs, the `automated_exception_fixer`
job downloads the test bundles and runs Claude Code to analyze and fix the
issues.

### Automated Reviewer

After successful tests, the `automated_reviewer` job uses the shared
`shakenfist/actions/review-pr-with-claude@main` action to review the PR.
The reviewer produces structured JSON reviews, creates GitHub issues for
actionable items, and embeds the JSON in the PR comment for automation.

### Developer Automation (Bot Commands)

Authorized users can trigger automation by commenting on PRs:

- **`@shakenfist-bot please re-review`** - Triggers a fresh automated
  review of the PR using the shared review action.
- **`@shakenfist-bot please address comments`** - Runs Claude Code to
  address actionable items from the automated review. Uses
  `tools/address-comments-with-claude.sh` with dual-checkout security
  (trusted tools from base branch, PR code separately).
- **`@shakenfist-bot please attempt to fix`** - Runs Claude Code to fix
  unit test failures (`tox -ecover`). Uses `test-drift-fix.yml` with
  structured commit summaries.

## Working with This Codebase

### Code Style

- Single quotes for strings, double quotes for docstrings
- 80 character line wrap
- Trim trailing whitespace
- See [CLAUDE.md](CLAUDE.md) for detailed style guide

### Testing

```bash
tox                              # Run all tests
tox -eflake8                     # Lint check
tox -emypy                       # Type checking
tox -ecover                      # Coverage report
stestr run {test_name}           # Run specific test
```

### Pre-commit Hooks

The repository uses pre-commit hooks to validate code before commits:

```bash
pip install pre-commit           # Install pre-commit
pre-commit install               # Set up git hooks
pre-commit run --all-files       # Run all hooks manually
```

Current hooks:
- `actionlint` - Validates GitHub Actions workflow files
- `ansible-lint` - Validates Ansible playbooks in the deployer
- `mypy` - Type checking via tox (incremental rollout)

### sf-net daemon topology

`sf-net` runs a `net-worker` job on **every** cluster node (not only the
elected network node). Each node's worker drains its own per-node
`{node_uuid}-network-*` queues for hypervisor-local operations
(`create_on_hypervisor`, `ensure_mesh`). Additionally, the elected network
node's worker also drains the cluster-wide `networknode-clusteroperation-*`
queues for network-node-only operations (`create_on_network_node`,
`add_floating_ip`, etc.). This two-family design means per-hypervisor network
mutations are parallelised across nodes while network-node-singleton operations
remain serialised.

### Network facade (Phases 2–7)

Key new files introduced across Phases 2–5 of the network-facade work:

- `shakenfist/operations/error_report.py` — `ErrorReport` Pydantic model
  (fields: `code`, `message`, `details`, `origin_class`, `traceback`).
  Contains the `_EXCEPTION_CODE_REGISTRY` dict that maps typed exceptions to
  stable codes. The principle: **errors are data, never rehydrated exceptions**.
- `shakenfist/network/bridged_vxlan_network.py` — `BridgedVXLanNetwork`,
  instantiated only inside the workitem dispatcher. External callers hold
  `Network`; the dispatcher constructs `BridgedVXLanNetwork` and calls
  `_apply_*` methods on it.

The consumer-side API lives on `BaseClusterOperation`
(`shakenfist/operations/baseoperation.py`): `op.error_report` reads the
persisted report; `op.raise_for_error(timeout=None)` polls and raises
`NetworkOperationFailed` if the op ended in `STATE_ERROR`.

**Migrated `Network` methods after Phase 5 (complete — all 15 host-mutating
methods)**: `ensure_mesh`, `add_floating_ip`, `remove_floating_ip`,
`route_address`, `unroute_address`, `remove_nat`, `update_dnsmasq`,
`remove_dnsmasq`, `remove_dhcp_lease`, `update_dns_entry`, `remove_dns_entry`,
`create_on_hypervisor`, `delete_on_hypervisor`, `create_on_network_node`,
`delete_on_network_node`.

`Network.enable_nat` has been removed from the public surface; it is now the
private `BridgedVXLanNetwork._apply_enable_nat` method, called only from
`_apply_create_on_network_node`.

**Op-type dispatchers after Phase 5**: `net_op`, `net_ip_op`, `net_iface_op`,
`net_iface_ip_op`, `net_macaddr_ip_op`, plus `node_net_op` and `node_inst_op` /
`node_inst_netdesc_op`. All route through `BridgedVXLanNetwork` and persist
`ErrorReport` on their outer exception branch.

**In-worker sibling call pattern**: when a `Network` method needs to invoke
another host-mutating operation from inside an executing worker context (e.g.
`create_on_network_node` calling `update_dnsmasq`), re-enqueueing would deadlock
the single-worker queue. The correct pattern is
`BridgedVXLanNetwork(self)._apply_X()` directly — host mutation stays inside
the worker-only surface and the queue round-trip is avoided.

**Phase 6 — maintain is discovery-only.** `shakenfist/daemons/network/maintain.py` no
longer blocks on `raise_for_error()`. Each maintain pass applies a five-guard pipeline
(queue-depth, per-network gating, cooldown, circuit-breaker) before enqueueing any
reconciliation op, always at `PRIORITY.background`. Three new config knobs control the
guards: `MAINTAIN_QUEUE_DEPTH_THRESHOLD` (default 50), `MAINTAIN_RECONCILE_COOLDOWN_SECONDS`
(default 60), `MAINTAIN_RECONCILE_CIRCUIT_K` (default 5). The three legacy reconciliation
handlers `_network_deploy`, `_network_destroy`, and `_network_update_dnsmasq` (NetOp tasks
1, 2, 3) have been retired — their bodies now raise `InvalidStateForTask`; the task-enum
values are kept for on-disk record compatibility.

**Phase 7 — REST contract.** The two network delete endpoints (`DELETE /networks/<uuid>` and
`DELETE /networks`) now return HTTP 202 (Accepted) with an op-handle body rather than a
synchronous 200. `@redirect_to_network_node` has been removed from three of its four call
sites (`InterfaceEndpoint.get`, `NetworkEndpoint.delete`, `NetworksEndpoint.delete`); the
decorator remains on `NetworkPingEndpoint.get` because the ping handler runs `ip netns exec`
directly on the network node — migrating it to queue-based requires op-output infrastructure
not yet built (deferred future work). Two new REST endpoints were added:
`GET /clusteroperations/<op_uuid>/chain` (transitive `depends_on` ancestor closure,
namespace-scoped — admin sees all, non-admin gets 403 on foreign-namespace members) and
`GET /clusteroperations?target_object_type=<type>&target_uuid=<uuid>` (ops targeting an
object, SQL-layer namespace filtering). The companion client-python changes (sibling repo,
feature branch `network-facade-phase-07`) make `delete_network` and `delete_all_networks`
handle 202 transparently by default (poll until terminal, raise on error); `wait=False`
returns the op handle without polling. Phase 8 is complete; Phase 9 (documentation sweep)
is the only remaining phase.

**Phase 8 — NodeLock removal.** The 13 `NodeLock(global_scope=False)` wrappers
inside `BridgedVXLanNetwork._apply_*` methods have been removed (commit `277b0572`).
The load-bearing reason is the single-threaded dispatcher in
`shakenfist/daemons/network/workitem.py`: its single-worker-per-queue invariant
(see the comment block at `self._defer_delays`) guarantees that exactly one
caller executes any `_apply_*` method at a time, making the per-network locks
redundant. Cross-daemon serialisation is now via the queue itself — only `sf-net`
dequeues and executes network work, so concurrent invocation across daemons cannot
happen by construction. Note that this reasoning is specific to
`NodeLock(global_scope=False)`; it does not extend to `ClusterLock`s, which
serialise across the cluster and remain in use elsewhere. Phase 9 (full
documentation sweep) is the only remaining phase.

### Key Directories

- `shakenfist/` - Core package
- `shakenfist/daemons/` - Background services
- `shakenfist/external_api/` - REST API
- `.github/workflows/` - CI workflows
- `.github/exported-config/` - Exported GitHub settings
