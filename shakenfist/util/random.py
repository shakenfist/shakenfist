import random
import string


def random_id():
    """ Returns a short random string for queue entries. """
    return ''.join(random.choices(string.ascii_letters + string.digits, k=16))
