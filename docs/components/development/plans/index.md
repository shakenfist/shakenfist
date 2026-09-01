# Plans index

Every planning document in this repository, oldest first. The plans
here are about the fleet's tooling rather than about a product: what
the consistency audits check, how they are run, and how whole-codebase
human review is tracked.

None of these plans has separate phase files. Where a plan is phased
its phases are sections inside it, tracked in its own Execution table;
this page carries only the one-line status.

Status cells use the shared vocabulary from
`templates/shared-blocks/plan-status-vocabulary.md`, which is the same
list the `plan-index` audit enforces across the fleet. A status says
whether the plan still wants attention and nothing else -- the dates,
the phase arithmetic and the summary of what happened live in the plan.

| Date | Plan | Intent | Status |
|------|------|--------|--------|
| 2026-02-18 | [Project consistency](/components/development/plans/PLAN-consistency/) | The original per-project audit of every Shaken Fist repository, and the cleanup backlog it produced | Superseded |
| 2026-02-18 | [stestr / testtools pin](/components/development/plans/PLAN-stestr-testtools/) | Pin stestr and testtools around an upstream incompatibility, and record the conditions for removing the pin | Blocked |
| 2026-03-08 | [Consistency audits v2](/components/development/plans/PLAN-consistency-audits-v2/) | Rebuild the audit as modular specs, a CI runner and GitHub issue automation, so criteria can be added without re-auditing everything | In progress |
| 2026-07-09 | [Code review tracking](/components/development/plans/PLAN-code-review-tracking/) | Systematic whole-codebase human review: weAudit, signed review state, staleness pruning against blob SHAs | In progress |
| 2026-08-02 | [Review coverage steady state](/components/development/plans/PLAN-review-coverage/) | Take review tracking from a manual loop to a steady state: CI pruning on main, and coverage alerting from the daily audit | In progress |
| 2026-08-15 | [LLM doc structure](/components/development/plans/PLAN-llm-doc-structure/) | An audit keeping AGENTS.md and ARCHITECTURE.md a summary and an index rather than a second copy of docs/ | Complete |
| 2026-08-16 | [Plan template blocks](/components/development/plans/PLAN-plan-template-blocks/) | Shared blocks for PLAN-TEMPLATE.md, so plans are written to the conventions rather than corrected against them afterwards | In progress |
| 2026-08-24 | [Push audit phase](/components/development/plans/PLAN-push-audit-phase/) | Give the pre-push audit a trigger: a mandatory final phase in every master plan, and a consistency check that nothing drops the reference | In progress |
| 2026-08-27 | [Audit compliance split](/components/development/plans/PLAN-audit-compliance-split/) | Move the generated compliance tables out of the criterion specifications, so the prose that defines the standards can hold a human review mark | In progress |
| 2026-09-01 | [Audit scripts restructure](/components/development/plans/PLAN-audit-scripts-restructure/) | Turn the 6,657-line audit checker into a package of check classes with a GitHub seam, so a criterion is one module and every check is testable | Complete |
