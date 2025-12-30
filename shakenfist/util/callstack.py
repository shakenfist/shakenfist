import re
import traceback


FILENAME_RE = re.compile('.*/dist-packages/shakenfist/(.*)')


def get_caller(offset=-2):
    # Determine the name of the calling method
    filename = traceback.extract_stack()[offset].filename
    f_match = FILENAME_RE.match(filename)
    if f_match:
        filename = f_match.group(1)
    stack = traceback.extract_stack()[offset]
    return f'{filename}:{stack.lineno}:{stack.name}()'


def generate_traceback(offset=-2):
    stack = traceback.extract_stack()
    formatted = traceback.format_list(stack[:-offset])
    return '\n%s'.join(formatted)
