# Copyright 2020 Michael Still

import base64
import bcrypt
import sys

from shakenfist import db

# Very simple helper to bootstrap a user account during install
#
# Args are: key_name, key


def main():
    print('Creating key %s' % sys.argv[1])

    encoded = str(base64.b64encode(bcrypt.hashpw(
        sys.argv[2].encode('utf-8'), bcrypt.gensalt())), 'utf-8')

    db.persist_namespace('system',
                         {
                             'name': 'system',
                             'keys': {
                                 sys.argv[1]: encoded
                             }
                         })


if __name__ == '__main__':
    main()
