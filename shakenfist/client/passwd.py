# Copyright 2020 Michael Still

import bcrypt
import sys

from shakenfist import etcd

# Very simple helper to bootstrap a user account during install
#
# Args are: key_name, key


def main():
    print('Creating key %s' % sys.argv[1])
    etcd.put('namespaces', None, 'all',
             {
                 'name': 'all',
                 'keys': {
                     sys.argv[1]: bcrypt.hashpw(
                         sys.argv[2].encode('utf-8'), bcrypt.gensalt())
                 }
             })


if __name__ == '__main__':
    main()
