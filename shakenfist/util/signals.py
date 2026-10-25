# Copyright 2026 Michael Still and contributors

from typing import Any
from typing import Callable
from typing import TypeVar


F = TypeVar('F', bound=Callable[..., Any])


def signal_safe(func: F) -> F:
    """Mark a function as safe to call from a signal handler.

    This does nothing at run time. tools/check-signal-handlers.py, run by
    pre-commit, lets a signal handler call a function only if it carries
    this decorator, and checks the decorated function's body by the same
    rules as the handler's: no logging, no database or other library calls,
    no context managers or loops, and only other @signal_safe functions or
    raw os.open/os.write/os.close. See that tool's docstring for why.
    """
    return func
