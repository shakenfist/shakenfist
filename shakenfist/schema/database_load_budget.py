# Copyright 2019 Michael Still and contributors
# Pydantic schema and loader for the shipped sf-database load budget.
#
# The budget expresses expected database load as a model rather than a number:
# a per-node base, a per-cluster constant, and a per-standing-instance
# coefficient, for each (operation, caller_daemon) pair. Everything which
# checks database load -- the functional CI idle-load test, the generated
# Prometheus rules, `sf-ctl database-load`, and our own nightly report --
# reads this one file so that the several consumers cannot drift apart.
#
# The data file is shipped inside the package, declared by
# [tool.setuptools.package-data] in pyproject.toml. It used to reach the
# wheel only through the implicit union of the setuptools_scm file finder
# with include-package-data, which nothing declared and nothing checked:
# reading it through importlib.resources does not test that, because a
# source checkout resolves shakenfist.data as a namespace package whatever
# packaging says. load_budget() still reads through importlib.resources,
# because that is how a deployed process finds it, but the thing which
# keeps it in the wheel is the declaration.

import functools
from importlib import resources
from typing import Optional

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator


BUDGET_PACKAGE = 'shakenfist.data'
BUDGET_RESOURCE = 'database_load_budget.yaml'


class Provisional(BaseModel):
    """Why a budget entry is not yet a floor worth defending.

    An entry is provisional when the load it records is a known defect
    rather than the cost of doing business. Committing such a measurement
    without saying so would make the defect the thing every detector then
    protects, so provisional entries are reported by every consumer and
    enforced by none of them.
    """

    model_config = ConfigDict(frozen=True)

    issue: int = Field(..., gt=0,
                       description='The open issue describing the defect.')
    reason: str = Field(..., min_length=1,
                        description='One line on what makes it provisional.')


class Measured(BaseModel):
    """The observation an entry's coefficients were fitted from."""

    model_config = ConfigDict(frozen=True)

    mean_qps: float = Field(..., ge=0.0)
    r2: float = Field(..., ge=0.0, le=1.0)


class BudgetEntry(BaseModel):
    """Expected load for one (operation, caller_daemon) pair."""

    model_config = ConfigDict(frozen=True)

    operation: str = Field(..., min_length=1)
    caller_daemon: str = Field(..., min_length=1)

    # The three model terms. per_node_base_qps scales with cluster size,
    # cluster_base_qps is work done once cluster wide (the elected cluster
    # daemon's sweeps), and per_instance_qps scales with standing instances.
    # cluster_base_qps may be negative: GetNodeDaemonState/cluster is
    # 0.5 per node less 0.3, because the one elected daemon polls from a 5s
    # loop rather than the 2s interval every other daemon idles at.
    per_node_base_qps: Optional[float] = Field(None, ge=0.0)
    cluster_base_qps: Optional[float] = None
    per_instance_qps: Optional[float] = Field(None, ge=0.0)

    # Traffic driven by what users and CI do rather than by a loop. The level
    # is workload specific, so consumers report it and do not alert on it.
    activity_coupled: bool = False
    provisional: Optional[Provisional] = None
    measured: Optional[Measured] = None
    note: str = Field(..., min_length=1)

    @model_validator(mode='after')
    def _has_a_term(self) -> 'BudgetEntry':
        if (self.per_node_base_qps is None and self.cluster_base_qps is None
                and self.per_instance_qps is None):
            raise ValueError(
                '%s/%s has no model terms; an entry which predicts nothing '
                'cannot be over or under budget'
                % (self.operation, self.caller_daemon))
        return self

    @property
    def key(self) -> tuple:
        return (self.operation, self.caller_daemon)

    @property
    def enforced(self) -> bool:
        """Whether exceeding this entry should fail a check.

        Provisional entries describe a known defect and activity coupled
        entries describe someone else's workload. Both are worth reporting
        and neither is worth failing on.
        """
        return self.provisional is None and not self.activity_coupled

    def expected_qps(self, nodes: int, standing_instances: float) -> float:
        """Modelled load for a cluster of this shape.

        Clamped at zero so the negative cluster_base_qps on
        GetNodeDaemonState/cluster cannot produce a negative expectation on
        an implausibly small cluster.
        """
        qps = 0.0
        if self.per_node_base_qps is not None:
            qps += self.per_node_base_qps * nodes
        if self.cluster_base_qps is not None:
            qps += self.cluster_base_qps
        if self.per_instance_qps is not None:
            qps += self.per_instance_qps * standing_instances
        return max(0.0, qps)

    def ceiling_qps(self, nodes: int, standing_instances: float,
                    multiplier: float, floor: float) -> float:
        return self.expected_qps(nodes, standing_instances) * multiplier + floor


class BudgetDefaults(BaseModel):
    """Tolerances shared by every consumer of the budget."""

    model_config = ConfigDict(frozen=True)

    tolerance_multiplier: float = Field(..., gt=1.0)
    tolerance_floor_qps: float = Field(..., ge=0.0)
    unbudgeted_fixed_rate_qps: float = Field(..., gt=0.0)


class DatabaseLoadBudget(BaseModel):
    """The whole shipped budget."""

    model_config = ConfigDict(frozen=True)

    version: int = Field(..., ge=1)
    defaults: BudgetDefaults
    entries: list[BudgetEntry]

    @model_validator(mode='after')
    def _keys_are_unique(self) -> 'DatabaseLoadBudget':
        seen = set()
        for entry in self.entries:
            if entry.key in seen:
                raise ValueError(
                    'duplicate budget entry for %s/%s' % entry.key)
            seen.add(entry.key)
        return self

    def get(self, operation: str, caller_daemon: str
            ) -> Optional[BudgetEntry]:
        for entry in self.entries:
            if entry.key == (operation, caller_daemon):
                return entry
        return None

    def expected_total_qps(self, nodes: int,
                           standing_instances: float) -> float:
        return sum(e.expected_qps(nodes, standing_instances)
                   for e in self.entries)


def budget_text() -> str:
    """The raw budget file, read as package data.

    Deliberately not a path relative to __file__: on a deployed node the
    only copy is the one inside the wheel, and this is how a process
    installed from one finds it. It is not a packaging assertion --
    a source checkout resolves it either way -- so what keeps the file
    in the wheel is the package-data declaration in pyproject.toml.
    """
    return (resources.files(BUDGET_PACKAGE)
            .joinpath(BUDGET_RESOURCE).read_text(encoding='utf-8'))


@functools.cache
def load_budget() -> DatabaseLoadBudget:
    """Parse and validate the shipped budget.

    Cached because `sf-ctl database-load` and the Prometheus rule generator
    both read it several times over one run, and it never changes within a
    process.
    """
    return DatabaseLoadBudget.model_validate(yaml.safe_load(budget_text()))
