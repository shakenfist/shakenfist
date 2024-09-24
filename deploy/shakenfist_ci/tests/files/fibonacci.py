#!/usr/bin/python3
# A silly little demo to calculate some Fibonacci sequence numbers.

if __name__ == '__main__':
    f = [0, 1]

    for _ in range(998):
        f.append(f[-2] + f[-1])

    print(f)
