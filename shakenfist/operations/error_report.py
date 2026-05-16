# Copyright 2019 Michael Still and contributors
"""Structured failure record carried across the queue boundary.

``ErrorReport`` is the on-the-wire representation of a failed cluster
operation. The dispatcher catches in-worker exceptions, converts them
into an ``ErrorReport`` via :meth:`ErrorReport.from_exception`, and
persists the report on the operation row. The REST layer renders a
report into an HTTP response via :meth:`ErrorReport.to_http`.

The crucial property is that an ``ErrorReport`` is **data**, never
rehydrated into a Python exception. Every mature RPC framework
(gRPC, JSON-RPC, Twirp/Connect) has converged on this pattern;
OpenStack's ``oslo.messaging`` is the cautionary tale for the
alternative. The stable ``code`` field is the contract; the
``message``, ``details``, ``origin_class``, and ``traceback`` fields
are diagnostic and not part of the contract.

This module is also the **one canonical place for the exception ->
code registry**. New subsystems extending ``ErrorReport`` should
register their exception classes here, not in scattered handlers.
"""

import traceback as traceback_module
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from shakenfist.exceptions import CannotAssignFloatingGateway
from shakenfist.exceptions import CreateVXLANInterfaceFailed
from shakenfist.exceptions import DeadNetwork
from shakenfist.exceptions import EnsureMeshFailed


# Stable code namespace for known exception classes. Unknown exception
# classes map to ``internal.unknown`` with ``origin_class`` preserved so
# operators can still see what happened. Add entries here as new
# subsystems adopt the ErrorReport pattern.
_EXCEPTION_CODE_REGISTRY: dict[type[Exception], str] = {
    EnsureMeshFailed: 'network.ensure_mesh.failed',
    DeadNetwork: 'network.dead',
    CreateVXLANInterfaceFailed: 'network.create_vxlan.failed',
    CannotAssignFloatingGateway: 'network.floating.assign_failed',
}


# Mapping from stable error codes to HTTP status codes. Lives next to
# the registry so the full failure-mapping picture is in one file. Not
# a class attribute because the dict comprehension over the registry
# happens at import time but the HTTP layer reads this at request time.
_CODE_HTTP_STATUS: dict[str, int] = {
    'network.dead': 410,
    'network.ensure_mesh.failed': 500,
    'network.create_vxlan.failed': 500,
    'network.floating.assign_failed': 500,
    'internal.unknown': 500,
}


class ErrorReport(BaseModel):
    """Structured failure record persisted on a cluster operation row.

    Attributes:
        code: Stable error code (e.g. ``'network.ensure_mesh.failed'``).
            This is the contract field. Callers branch on this.
        message: Human-readable description of the failure. Diagnostic
            only; not part of the contract.
        details: Structured context dict for additional diagnostic
            data. Free-form; defaults to empty.
        origin_class: Fully qualified class path of the original
            exception (e.g. ``'shakenfist.exceptions.EnsureMeshFailed'``).
            Operator-only field; not surfaced via :meth:`to_http`.
        traceback: Formatted traceback captured from the worker's
            exception handler. Operator-only; not surfaced to REST
            clients.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    origin_class: str
    traceback: str = ''

    @classmethod
    def from_exception(
            cls,
            exc: Exception,
            details: Optional[dict[str, Any]] = None) -> 'ErrorReport':
        """Build an ErrorReport from a caught exception.

        Looks the exception's class up in the registry to determine the
        stable code. Unregistered exceptions get ``'internal.unknown'``
        with the actual class name preserved in ``origin_class``.

        The ``traceback`` field is filled from
        ``traceback.format_exc()`` and is only meaningful when this
        method is called from inside an ``except`` block.

        Args:
            exc: The caught exception.
            details: Optional caller-supplied diagnostic context.

        Returns:
            A new ``ErrorReport`` with the contract code, the
            exception's ``str()`` as the message, and (if called inside
            an except handler) the current traceback.
        """
        code = _EXCEPTION_CODE_REGISTRY.get(type(exc), 'internal.unknown')
        origin = f'{type(exc).__module__}.{type(exc).__qualname__}'
        tb = traceback_module.format_exc()
        # When called outside an except handler ``format_exc()`` returns
        # the literal string 'NoneType: None\n'; collapse that to empty.
        if tb.strip() == 'NoneType: None':
            tb = ''
        return cls(
            code=code,
            message=str(exc),
            details=details if details is not None else {},
            origin_class=origin,
            traceback=tb,
        )

    def to_http(self) -> tuple[int, dict[str, Any]]:
        """Render this report for a REST response.

        The body contains the stable code, the message, and the
        details dict. ``origin_class`` and ``traceback`` are deliberately
        **not** included; they are operator-only fields readable from
        the persisted report but never surfaced to API clients.

        Returns:
            A tuple of ``(http_status_code, body_dict)``.
        """
        status = _CODE_HTTP_STATUS.get(self.code, 500)
        body = {
            'code': self.code,
            'message': self.message,
            'details': self.details,
        }
        return status, body
