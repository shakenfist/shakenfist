#!/usr/bin/python3

import sys
import telnetlib
import time


class LoggingSocket(object):
    ctrlc = '\x03'

    def __init__(self, port):
        self.s = telnetlib.Telnet('127.0.0.1', port, 30)
        for d in [self.ctrlc, self.ctrlc, '\nexit\n', 'cirros\n', 'gocubsgo\n']:
            self.send(d)
            time.sleep(0.5)
            self.recv()

    def send(self, data):
        # print('>> %s' % data.replace('\n', '\\n').replace('\r', '\\r'))
        self.s.write(data.encode('ascii'))

    def recv(self):
        data = self.s.read_eager().decode('ascii')
        # for line in data.split('\n'):
        #    print('<< %s' % line.replace('\n', '\\n').replace('\r', '\\r'))
        return data

    def execute(self, cmd):
        self.send(cmd + '\n')
        time.sleep(5)
        d = ''
        while not d.endswith('\n$ '):
            d += self.recv()
        return d


if __name__ == '__main__':
    console = LoggingSocket(int(sys.argv[1]))

    if sys.argv[2] == 'cat':
        print(console.execute('cat %s' % sys.argv[3]))
    if sys.argv[2] == 'exists':
        print(console.execute(
            'if [ -e %s ]\n'
            'then\n'
            '  echo "File exists"\n'
            'else\n'
            '  echo "File missing"\n'
            'fi' % sys.argv[3]))
    if sys.argv[2] == 'ifconfig':
        print(console.execute('ifconfig %s' % sys.argv[3]))
    if sys.argv[2] == 'netstat':
        print(console.execute('netstat %s' % sys.argv[3]))
    if sys.argv[2] == 'ping':
        print(console.execute('ping -c 3 -w 4 %s' % sys.argv[3]))
    if sys.argv[2] == 'touch':
        print(console.execute('touch %s' % sys.argv[3]))
