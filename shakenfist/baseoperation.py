from shakenfist.baseobject import DatabaseBackedObject as dbo


class BaseOperation(dbo):
    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_QUEUED = 'queued'
    STATE_PREFLIGHT = 'preflight'
    STATE_EXECUTING = 'executing'
    STATE_COMPLETE = 'complete'

    ACTIVE_STATES = {dbo.STATE_CREATED, STATE_QUEUED,
                     STATE_EXECUTING, STATE_COMPLETE}

    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (STATE_PREFLIGHT, STATE_QUEUED, dbo.STATE_DELETED,
                            dbo.STATE_ERROR),
        STATE_PREFLIGHT: (STATE_QUEUED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_QUEUED: (STATE_EXECUTING, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_EXECUTING: (STATE_COMPLETE, dbo.STATE_DELETED, dbo.STATE_ERROR),
        STATE_COMPLETE: (dbo.STATE_DELETED),
        dbo.STATE_ERROR: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: None,
    }
