import re
import traceback


FILENAME_RE = re.compile('.*/dist-packages/shakenfist/(.*)')


def get_caller(offset=-2):
    # Determine the name of the calling method
    filename = traceback.extract_stack()[offset].filename
    f_match = FILENAME_RE.match(filename)
    if f_match:
        filename = f_match.group(1)
    return '%s:%s:%s()' % (filename,
                           traceback.extract_stack()[offset].lineno,
                           traceback.extract_stack()[offset].name)
