import functools
import os
import re
import sys
import traceback
from typing import Callable, TypeVar

from shakenfist_utilities import logs


LOG, _ = logs.setup(__name__)

FILENAME_RE = re.compile('.*/dist-packages/shakenfist/(.*)')

# Type variable for preserving function signatures
F = TypeVar('F', bound=Callable)

# Enable/disable caller restriction checks. Set SHAKENFIST_CHECK_CALLERS=0 to
# disable. Uses sys._getframe() which adds ~0.1 microseconds per call.
CHECK_CALLERS_ENABLED = os.environ.get('SHAKENFIST_CHECK_CALLERS', '1') != '0'


def get_caller(offset=-2):
    """Get the caller's location as 'filename:lineno:name()'.

    Uses sys._getframe() for ~92x better performance than traceback.extract_stack().

    Args:
        offset: How many frames to go back. Default -2 means the caller of the
            function that called get_caller(). Use -3 for one more level up, etc.
    """
    # Convert negative offset to positive frame depth
    # offset=-2 means 2 levels up from extract_stack, which is 1 level up from here
    depth = abs(offset) - 1
    frame = sys._getframe(depth)
    filename = frame.f_code.co_filename
    f_match = FILENAME_RE.match(filename)
    if f_match:
        filename = f_match.group(1)
    return f'{filename}:{frame.f_lineno}:{frame.f_code.co_name}()'


def generate_traceback(offset=-2):
    stack = traceback.extract_stack()
    formatted = traceback.format_list(stack[:-offset])
    return '\n%s'.join(formatted)


def restrict_caller(*allowed_modules: str) -> Callable[[F], F]:
    """Decorator that warns when a function is called from non-allowed modules.

    This provides "soft enforcement" - violations log warnings but don't raise
    exceptions, allowing code to continue working while highlighting misuse.

    Uses sys._getframe() which adds ~0.1 microseconds overhead per call.
    Can be disabled by setting SHAKENFIST_CHECK_CALLERS=0.

    Args:
        allowed_modules: Module name patterns to allow. A module is allowed if
            its __name__ starts with any of these patterns.

    Example:
        @restrict_caller('shakenfist.daemons.database')
        def _direct_something():
            # Only database daemon should call this
            pass

        @restrict_caller('shakenfist.blob', 'shakenfist.instance')
        def some_method():
            # Only blob and instance modules should call this
            pass
    """
    def decorator(func: F) -> F:
        if not CHECK_CALLERS_ENABLED:
            return func

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Get the caller's module name using fast frame inspection
            frame = sys._getframe(1)
            caller_module = frame.f_globals.get('__name__', 'unknown')

            # Check if caller matches any allowed pattern
            allowed = any(
                caller_module.startswith(pattern)
                for pattern in allowed_modules
            )

            if not allowed:
                LOG.with_fields({
                    'caller': caller_module,
                    'function': func.__name__,
                    'allowed': allowed_modules
                }).warning(
                    'Function called from non-allowed module. This may '
                    'indicate an architectural violation.'
                )

            return func(*args, **kwargs)
        return wrapper
    return decorator
