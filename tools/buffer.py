#!/usr/bin/python

import os
import sys


def get_status(fd):
    if os.get_blocking(fd):
        return 'blocking'
    else:
        return 'non-blocking'


if __name__ == '__main__':
    stdin = sys.stdin.fileno()
    stdout = sys.stdout.fileno()
    stderr = sys.stderr.fileno()

    print('stdin: %s, stdout: %s, stderr: %s'
          % (get_status(stdin), get_status(stdout), get_status(stderr)))
