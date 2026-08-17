# Pydantic schema for NamespaceClaim static values.
#
# A claim is a namespace's promise of aggregate capacity from the
# cluster: so many cpus, so much memory and so much disk, guaranteed
# against cluster_capacity for as long as the claim is active. See
# docs/plans/PLAN-scheduler-reservations-phase-04-claims-api.md.
#
# Unlike most of the models in this package, this one is *not* the source
# of truth for its table. namespace_claims is one of the three scheduler
# capacity tables and is declared by hand in
# _get_namespace_claims_table() in shakenfist/mariadb.py, because its
# columns are written by guarded UPDATE statements and by the reconciler
# rather than through the generic object persistence path. This model is
# therefore a typed data transfer object for the static half of a claim
# only, and adding a field here does not add a column there.

from typing import Annotated

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import UUID4

from shakenfist.schema.sqlalchemy import SQLNativeUUID


class NamespaceClaimData(BaseModel):
    """Schema for NamespaceClaim static values.

    Everything else a claim carries -- the limits, the materialised
    usage counters, the coverage state and the expiry -- is mutable and
    lives in the same namespace_claims row, maintained by the claim
    admission transactions and the reconciler. The object reads those
    live rather than caching them, so they are deliberately absent here:
    a snapshot of a counter three other writers move is a stale number
    wearing the appearance of a fresh one.

    Table: namespace_claims (declared in shakenfist/mariadb.py)
    Primary key: uuid

    Attributes:
        uuid: The claim's unique identifier.
        namespace: The name of the claiming namespace, matching
            namespaces.name. Indexed in the table, because admission
            looks a claim up by namespace on every instance create.
        version: Object version number for schema migrations. The table
            has no version column -- a claim's shape is owned by the
            table schema, which ensure_schema() migrates -- so this is
            always the reading build's current_version.
    """

    model_config = ConfigDict(frozen=True)  # Immutable

    # The claim's UUID - primary key, stored as a native MariaDB UUID
    uuid: Annotated[UUID4, SQLNativeUUID()]

    # The claiming namespace's name
    namespace: Annotated[str, Field(max_length=255)]

    # Object version number for schema migrations
    version: int
