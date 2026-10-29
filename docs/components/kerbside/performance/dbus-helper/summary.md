# Pooled summary of the D-Bus helper measurements

`summarise.py`'s output over every completed repeat of the matrix, the
150 ms / 2 Mbit rerun and the std-vga row, as described in
[the measurements page](/components/kerbside/performance/dbus-helper-measurements/#procedure). Case
names are `<display>-relay`: `off` and `all` are spice-server's
`streaming-video`, `helper` is andris on virtio-gpu, and `stdhelper` is
andris on std-vga. 13 repeats were lost to a first key press over 15 s and
are excluded; see the Repeats column.

In the `helper-relay` and `stdhelper-relay` rows, the backend columns
(Backend Recv-Q, Backend skmem) and "qemu Send-Q" describe andris's SPICE
socket, not qemu's: andris listens where spice-server would. So the
1957 / 2570 KiB "qemu Send-Q" at 80 ms / 10 Mbit heavy is andris's send
queue, not a queue inside qemu.

| Link | Activity | Proxy | Repeats good (died, failed) | n (timeouts) | p50 ms | p95 ms | max ms | Display Mbit/s ss / metrics | Client Send-Q p50/p95 KiB | Client sRTT ms | cwnd | Backend Recv-Q p50/max KiB | Backend skmem max KiB (rcvbuf) | qemu Send-Q p50/p95 KiB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0ms-0mbit | busy | all-relay | 2 (0, 0) | 80 (0) | 20 | 32 | 35 | 8.9 / 8.7 | 0/0 | 0 | 82 | 0/0 | 0 (5441) | 0/0 |
| 0ms-0mbit | busy | helper-relay | 2 (0, 0) | 80 (0) | 21 | 35 | 40 | 1.2 / 1.2 | 0/0 | 1 | 69 | 0/0 | 0 (6144) | 0/5 |
| 0ms-0mbit | busy | off-relay | 2 (0, 0) | 80 (0) | 21 | 32 | 35 | 105.1 / 103.8 | 0/123 | 1 | 173 | 0/64 | 65 (5441) | 0/93 |
| 0ms-0mbit | busy | stdhelper-relay | 4 (0, 0) | 160 (1) | 45 | 245 | 5174 | 5.9 / 5.8 | 0/1 | 1 | 57 | 0/56 | 58 (6144) | 0/6 |
| 0ms-0mbit | heavy | all-relay | 2 (0, 0) | 80 (0) | 22 | 42 | 49 | 48.5 / 47.8 | 0/0 | 0 | 171 | 0/192 | 195 (5441) | 0/62 |
| 0ms-0mbit | heavy | helper-relay | 2 (0, 0) | 80 (0) | 22 | 36 | 42 | 2.0 / 2.1 | 0/0 | 1 | 26 | 0/0 | 0 (6144) | 0/0 |
| 0ms-0mbit | heavy | off-relay | 2 (0, 0) | 80 (0) | 33 | 173 | 237 | 152.7 / 151.0 | 0/10 | 0 | 116 | 0/155 | 158 (6144) | 37/128 |
| 0ms-0mbit | heavy | stdhelper-relay | 4 (0, 0) | 160 (0) | 52 | 580 | 4145 | 16.0 / 15.8 | 0/1 | 1 | 55 | 0/66 | 68 (6144) | 0/5 |
| 0ms-0mbit | idle | all-relay | 2 (0, 0) | 80 (0) | 19 | 30 | 31 | 0.0 / 0.0 | 0/0 | 1 | 10 | 0/0 | 0 (6144) | 0/0 |
| 0ms-0mbit | idle | helper-relay | 2 (0, 0) | 80 (0) | 3 | 5 | 5 | 0.0 / 0.0 | 0/0 | 1 | 10 | 0/0 | 0 (6144) | 0/0 |
| 0ms-0mbit | idle | off-relay | 2 (0, 0) | 80 (0) | 15 | 30 | 32 | 0.0 / 0.0 | 0/0 | 1 | 10 | 0/0 | 0 (5441) | 0/0 |
| 150ms-2mbit | busy | all-relay | 4 (0, 0) | 160 (0) | 185 | 239 | 331 | 1.6 / nan | 38/96 | 193 | 57 | 0/0 | 1 (5911) | 0/13 |
| 150ms-2mbit | busy | helper-relay | 0 (0, 4) | 0 (0) | nan | nan | nan | nan / nan | nan/nan | nan | nan | nan/nan | nan (nan) | nan/nan |
| 150ms-2mbit | busy | off-relay | 4 (0, 0) | 160 (0) | 869 | 1113 | 1189 | 1.8 / nan | 204/266 | 304 | 50 | 0/0 | 1 (6144) | 0/0 |
| 150ms-2mbit | heavy | all-relay | 4 (0, 0) | 160 (0) | 7315 | 7901 | 8011 | 1.9 / nan | 253/301 | 309 | 51 | 437/2691 | 2712 (4068) | 0/0 |
| 150ms-2mbit | heavy | helper-relay | 0 (0, 4) | 0 (0) | nan | nan | nan | nan / nan | nan/nan | nan | nan | nan/nan | nan (nan) | nan/nan |
| 150ms-2mbit | heavy | off-relay | 0 (0, 4) | 0 (0) | nan | nan | nan | nan / nan | nan/nan | nan | nan | nan/nan | nan (nan) | nan/nan |
| 20ms-50mbit | busy | all-relay | 2 (0, 0) | 80 (0) | 38 | 52 | 54 | 7.7 / 7.6 | 28/34 | 24 | 134 | 0/252 | 335 (4068) | 27/33 |
| 20ms-50mbit | busy | helper-relay | 2 (0, 0) | 80 (0) | 40 | 56 | 58 | 1.2 / 1.3 | 4/6 | 27 | 125 | 0/5 | 6 (1341) | 0/5 |
| 20ms-50mbit | busy | off-relay | 2 (0, 0) | 80 (0) | 135 | 172 | 183 | 43.7 / 43.4 | 625/834 | 38 | 157 | 0/252 | 253 (4555) | 0/56 |
| 20ms-50mbit | heavy | all-relay | 2 (0, 0) | 80 (0) | 39 | 53 | 57 | 35.3 / 35.0 | 173/250 | 29 | 122 | 0/817 | 961 (3581) | 0/44 |
| 20ms-50mbit | heavy | helper-relay | 2 (0, 0) | 80 (0) | 43 | 56 | 58 | 1.8 / 1.9 | 0/87 | 24 | 52 | 0/545 | 588 (6144) | 0/6 |
| 20ms-50mbit | heavy | off-relay | 2 (0, 0) | 80 (0) | 250 | 319 | 344 | 44.1 / 43.8 | 749/884 | 38 | 159 | 501/957 | 1224 (5016) | 0/500 |
| 80ms-10mbit | busy | all-relay | 2 (0, 0) | 80 (0) | 101 | 114 | 124 | 5.6 / 5.5 | 67/86 | 94 | 107 | 0/29 | 30 (4578) | 0/28 |
| 80ms-10mbit | busy | helper-relay | 2 (0, 0) | 80 (0) | 106 | 119 | 121 | 0.9 / 0.9 | 6/16 | 91 | 127 | 0/196 | 198 (1480) | 0/5 |
| 80ms-10mbit | busy | off-relay | 2 (0, 0) | 80 (0) | 184 | 216 | 226 | 8.8 / 8.7 | 196/245 | 141 | 121 | 0/57 | 58 (4578) | 0/0 |
| 80ms-10mbit | busy | stdhelper-relay | 2 (0, 0) | 80 (0) | 135 | 160 | 294 | 6.2 / 6.1 | 82/102 | 97 | 129 | 0/1 | 126 (5441) | 0/4 |
| 80ms-10mbit | heavy | all-relay | 2 (0, 0) | 80 (0) | 113 | 277 | 336 | 8.0 / 7.9 | 128/438 | 116 | 126 | 0/2345 | 2623 (2627) | 0/45 |
| 80ms-10mbit | heavy | helper-relay | 2 (0, 0) | 80 (0) | 3492 | 4238 | 4396 | 9.4 / 9.4 | 609/723 | 150 | 124 | 1177/1332 | 1341 (1341) | 1957/2570 |
| 80ms-10mbit | heavy | off-relay | 2 (0, 0) | 80 (0) | 2039 | 2444 | 2473 | 9.3 / 9.3 | 612/728 | 150 | 123 | 1806/2814 | 3052 (5506) | 0/25 |
| 80ms-10mbit | heavy | stdhelper-relay | 1 (0, 1) | 40 (0) | 4400 | 5872 | 6130 | 9.4 / 9.4 | 614/724 | 155 | 128 | 3541/5956 | 6149 (6144) | 0/2185 |
| 80ms-10mbit | idle | all-relay | 2 (0, 0) | 80 (0) | 98 | 111 | 115 | 0.1 / 0.1 | 0/2 | 81 | 10 | 0/0 | 1 (4068) | 0/0 |
| 80ms-10mbit | idle | helper-relay | 2 (0, 0) | 80 (0) | 88 | 88 | 91 | 0.1 / 0.1 | 0/5 | 85 | 10 | 0/0 | 0 (1341) | 0/0 |
| 80ms-10mbit | idle | off-relay | 2 (0, 0) | 80 (0) | 96 | 111 | 112 | 0.1 / 0.1 | 0/2 | 83 | 10 | 0/0 | 1 (4068) | 0/0 |
| 80ms-50mbit | busy | all-relay | 2 (0, 0) | 80 (0) | 100 | 111 | 115 | 6.5 / 6.4 | 61/88 | 84 | 495 | 0/26 | 27 (4591) | 0/28 |
| 80ms-50mbit | busy | helper-relay | 2 (0, 0) | 80 (0) | 102 | 116 | 119 | 1.3 / 1.3 | 10/18 | 87 | 569 | 0/4 | 5 (2491) | 0/5 |
| 80ms-50mbit | busy | off-relay | 2 (0, 0) | 80 (0) | 105 | 118 | 120 | 16.4 / 16.1 | 186/237 | 88 | 477 | 0/18 | 83 (4591) | 16/59 |
| 80ms-50mbit | heavy | all-relay | 2 (0, 0) | 80 (0) | 99 | 111 | 113 | 18.5 / 18.1 | 203/313 | 88 | 468 | 0/2691 | 2705 (6144) | 0/58 |
| 80ms-50mbit | heavy | helper-relay | 2 (0, 0) | 80 (0) | 98 | 118 | 159 | 1.5 / 1.5 | 0/82 | 83 | 20 | 0/2232 | 2249 (6144) | 0/0 |
| 80ms-50mbit | heavy | off-relay | 2 (0, 0) | 80 (0) | 462 | 583 | 610 | 44.7 / 44.3 | 1997/2393 | 140 | 576 | 38/2691 | 2708 (6144) | 0/516 |
| 80ms-5mbit | busy | all-relay | 2 (0, 0) | 80 (0) | 118 | 144 | 157 | 4.3 / 4.2 | 63/86 | 114 | 75 | 0/22 | 23 (5122) | 0/22 |
| 80ms-5mbit | busy | helper-relay | 2 (0, 0) | 80 (0) | 3263 | 7313 | 7521 | 4.7 / 4.7 | 331/393 | 165 | 68 | 957/2180 | 2453 (6144) | 0/0 |
| 80ms-5mbit | busy | off-relay | 2 (0, 0) | 80 (0) | 371 | 453 | 467 | 4.5 / 4.5 | 202/260 | 160 | 66 | 0/0 | 0 (5122) | 0/0 |
| 80ms-5mbit | heavy | all-relay | 2 (0, 0) | 80 (0) | 112 | 470 | 1135 | 3.9 / 3.8 | 65/361 | 120 | 69 | 0/2564 | 2639 (4068) | 0/32 |
| 80ms-5mbit | heavy | helper-relay | 2 (0, 0) | 80 (0) | 6931 | 8586 | 8882 | 4.7 / 4.7 | 329/389 | 165 | 68 | 1181/1335 | 1341 (1341) | 2093/2573 |
| 80ms-5mbit | heavy | off-relay | 2 (0, 0) | 80 (0) | 4952 | 5302 | 5340 | 4.7 / 4.7 | 329/389 | 162 | 66 | 2142/3042 | 3394 (4068) | 0/0 |

| Link | Activity | Proxy | Repeats | Window | Display msgs/s | Mean msg KiB | Client sRTT ms | Msgs per sRTT | 2W+1 | Window ceiling msgs/s | Stalls/s | Time at threshold | In flight p50: proxy / with backlog |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0ms-0mbit | busy | all-relay | 2 | 20 | 636 | 1.5 | 0 | 0 | 41 | 168067 | 0.2 | 0% | 11 / 11 |
| 0ms-0mbit | busy | helper-relay | 2 | 20 | 31 | 4.2 | 0 | 0 | 41 | 94229 | 0.0 | 0% | 9 / 10 |
| 0ms-0mbit | busy | off-relay | 2 | 20 | 635 | 22.1 | 1 | 0 | 41 | 56537 | 0.0 | 0% | 12 / 13 |
| 0ms-0mbit | busy | stdhelper-relay | 4 | 20 | 89 | 8.5 | 1 | 0 | 41 | 31965 | 0.0 | 0% | 10 / 10 |
| 0ms-0mbit | heavy | all-relay | 2 | 20 | 611 | 9.9 | 0 | 0 | 41 | 109364 | 0.1 | 0% | 11 / 12 |
| 0ms-0mbit | heavy | helper-relay | 2 | 20 | 8 | 16.9 | 0 | 0 | 41 | 88398 | 0.0 | 0% | 10 / 10 |
| 0ms-0mbit | heavy | off-relay | 2 | 20 | 472 | 43.2 | 0 | 0 | 41 | 163767 | 0.0 | 0% | 10 / 12 |
| 0ms-0mbit | heavy | stdhelper-relay | 4 | 20 | 65 | 27.7 | 1 | 0 | 41 | 36613 | 0.0 | 0% | 10 / 10 |
| 0ms-0mbit | idle | all-relay | 2 | 20 | 5 | 0.5 | 1 | 0 | 41 | 53050 | 0.0 | 0% | 8 / 8 |
| 0ms-0mbit | idle | helper-relay | 2 | 20 | 2 | 2.5 | 1 | 0 | 41 | 29144 | 0.0 | 0% | 9 / 9 |
| 0ms-0mbit | idle | off-relay | 2 | 20 | 5 | 0.5 | 1 | 0 | 41 | 30534 | 0.0 | 0% | 8 / 8 |
| 150ms-2mbit | busy | all-relay | 4 | 40 | 179 | 1.1 | 189 | 34 | 81 | 217 | 0.9 | 3% | 55 / 58 |
| 150ms-2mbit | busy | off-relay | 4 | 40 | 68 | 3.4 | 306 | 21 | 81 | 93 | 1.7 | 96% | 81 / 81 |
| 150ms-2mbit | heavy | all-relay | 4 | 40 | 19 | 12.3 | 309 | 6 | 81 | 35 | 0.2 | 9% | 41 / 66 |
| 20ms-50mbit | busy | all-relay | 2 | 20 | 629 | 1.5 | 24 | 15 | 41 | 1386 | 0.2 | 0% | 24 / 40 |
| 20ms-50mbit | busy | helper-relay | 2 | 20 | 32 | 4.2 | 26 | 1 | 41 | 994 | 0.0 | 0% | 10 / 11 |
| 20ms-50mbit | busy | off-relay | 2 | 20 | 267 | 21.6 | 39 | 10 | 41 | 364 | 13.4 | 91% | 41 / 41 |
| 20ms-50mbit | heavy | all-relay | 2 | 20 | 476 | 9.9 | 29 | 14 | 41 | 655 | 7.7 | 4% | 26 / 27 |
| 20ms-50mbit | heavy | helper-relay | 2 | 20 | 7 | 15.4 | 23 | 0 | 41 | 542 | 0.0 | 0% | 9 / 9 |
| 20ms-50mbit | heavy | off-relay | 2 | 20 | 134 | 43.1 | 39 | 5 | 41 | 222 | 1.9 | 1% | 28 / 41 |
| 80ms-10mbit | busy | all-relay | 2 | 40 | 524 | 1.4 | 93 | 49 | 81 | 575 | 9.3 | 19% | 66 / 77 |
| 80ms-10mbit | busy | helper-relay | 2 | 20 | 31 | 2.9 | 90 | 3 | 41 | 291 | 0.0 | 0% | 12 / 13 |
| 80ms-10mbit | busy | off-relay | 2 | 40 | 337 | 3.4 | 142 | 48 | 81 | 315 | 8.4 | 82% | 81 / 81 |
| 80ms-10mbit | busy | stdhelper-relay | 2 | 20 | 89 | 9.1 | 96 | 9 | 41 | 163 | 0.0 | 0% | 20 / 20 |
| 80ms-10mbit | heavy | all-relay | 2 | 40 | 161 | 6.4 | 113 | 18 | 81 | 249 | 0.5 | 3% | 43 / 44 |
| 80ms-10mbit | heavy | helper-relay | 2 | 20 | 8 | 150.4 | 151 | 1 | 41 | 15 | 0.0 | 0% | 14 / 36 |
| 80ms-10mbit | heavy | off-relay | 2 | 40 | 28 | 41.8 | 150 | 4 | 81 | 53 | 0.0 | 0% | 36 / 81 |
| 80ms-10mbit | heavy | stdhelper-relay | 1 | 20 | 8 | 154.1 | 155 | 1 | 41 | 15 | 0.0 | 0% | 14 / 38 |
| 80ms-10mbit | idle | all-relay | 2 | 40 | 5 | 0.4 | 81 | 0 | 81 | 852 | 0.0 | 0% | 20 / 20 |
| 80ms-10mbit | idle | helper-relay | 2 | 20 | 2 | 2.5 | 84 | 0 | 41 | 322 | 0.0 | 0% | 10 / 10 |
| 80ms-10mbit | idle | off-relay | 2 | 40 | 5 | 0.4 | 82 | 0 | 81 | 847 | 0.0 | 0% | 20 / 20 |
| 80ms-50mbit | busy | all-relay | 2 | 40 | 600 | 1.4 | 84 | 50 | 81 | 862 | 11.7 | 28% | 62 / 72 |
| 80ms-50mbit | busy | helper-relay | 2 | 20 | 31 | 4.2 | 87 | 3 | 41 | 398 | 0.0 | 0% | 12 / 13 |
| 80ms-50mbit | busy | off-relay | 2 | 40 | 600 | 3.6 | 88 | 53 | 81 | 718 | 13.1 | 34% | 62 / 78 |
| 80ms-50mbit | heavy | all-relay | 2 | 40 | 280 | 8.5 | 88 | 25 | 81 | 555 | 0.0 | 0% | 46 / 46 |
| 80ms-50mbit | heavy | helper-relay | 2 | 20 | 4 | 8.1 | 82 | 0 | 41 | 367 | 0.0 | 0% | 9 / 9 |
| 80ms-50mbit | heavy | off-relay | 2 | 40 | 136 | 42.8 | 143 | 19 | 81 | 189 | 1.6 | 18% | 65 / 81 |
| 80ms-5mbit | busy | all-relay | 2 | 40 | 389 | 1.4 | 112 | 44 | 81 | 389 | 7.8 | 14% | 62 / 69 |
| 80ms-5mbit | busy | helper-relay | 2 | 20 | 11 | 54.3 | 165 | 2 | 41 | 21 | 0.0 | 0% | 13 / 33 |
| 80ms-5mbit | busy | off-relay | 2 | 40 | 169 | 3.4 | 162 | 27 | 81 | 207 | 4.2 | 91% | 81 / 81 |
| 80ms-5mbit | heavy | all-relay | 2 | 40 | 95 | 5.1 | 115 | 11 | 81 | 179 | 0.5 | 7% | 40 / 41 |
| 80ms-5mbit | heavy | helper-relay | 2 | 20 | 4 | 151.0 | 165 | 1 | 41 | 8 | 0.0 | 0% | 12 / 35 |
| 80ms-5mbit | heavy | off-relay | 2 | 40 | 14 | 41.8 | 163 | 2 | 81 | 28 | 0.0 | 0% | 30 / 82 |

| Link | Activity | Proxy | Repeats | Proxy cores | Proxy RSS mean / peak MiB | qemu cores | qemu RSS mean / peak MiB | andris cores | andris RSS mean / peak MiB | Pixmap cache peak MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| 0ms-0mbit | busy | all-relay | 2 | 0.014 | 9 / 9 | 0.161 | 208 / 212 | - | - | - |
| 0ms-0mbit | busy | helper-relay | 2 | 0.004 | 9 / 10 | 0.087 | 189 / 189 | 0.207 | 49 / 52 | - |
| 0ms-0mbit | busy | off-relay | 2 | 0.054 | 9 / 9 | 0.312 | 223 / 224 | - | - | - |
| 0ms-0mbit | busy | stdhelper-relay | 4 | 0.008 | 9 / 10 | 0.775 | 195 / 198 | 0.221 | 54 / 65 | - |
| 0ms-0mbit | heavy | all-relay | 2 | 0.029 | 9 / 9 | 0.173 | 216 / 219 | - | - | - |
| 0ms-0mbit | heavy | helper-relay | 2 | 0.002 | 9 / 10 | 0.085 | 178 / 184 | 0.110 | 60 / 66 | - |
| 0ms-0mbit | heavy | off-relay | 2 | 0.076 | 10 / 10 | 0.677 | 218 / 223 | - | - | - |
| 0ms-0mbit | heavy | stdhelper-relay | 4 | 0.010 | 9 / 10 | 0.777 | 191 / 200 | 0.156 | 54 / 58 | - |
| 0ms-0mbit | idle | all-relay | 2 | 0.001 | 9 / 9 | 0.020 | 204 / 208 | - | - | - |
| 0ms-0mbit | idle | helper-relay | 2 | 0.001 | 9 / 9 | 0.018 | 181 / 188 | 0.004 | 35 / 37 | - |
| 0ms-0mbit | idle | off-relay | 2 | 0.001 | 9 / 9 | 0.019 | 205 / 208 | - | - | - |
| 150ms-2mbit | busy | all-relay | 4 | 0.008 | 9 / 9 | 0.141 | 216 / 224 | - | - | - |
| 150ms-2mbit | busy | off-relay | 4 | 0.002 | 9 / 9 | 0.128 | 219 / 227 | - | - | - |
| 150ms-2mbit | heavy | all-relay | 4 | 0.001 | 10 / 10 | 0.107 | 221 / 228 | - | - | - |
| 20ms-50mbit | busy | all-relay | 2 | 0.013 | 9 / 9 | 0.179 | 208 / 212 | - | - | - |
| 20ms-50mbit | busy | helper-relay | 2 | 0.004 | 9 / 10 | 0.100 | 188 / 189 | 0.227 | 47 / 49 | - |
| 20ms-50mbit | busy | off-relay | 2 | 0.011 | 9 / 9 | 0.196 | 222 / 223 | - | - | - |
| 20ms-50mbit | heavy | all-relay | 2 | 0.018 | 9 / 9 | 0.179 | 215 / 217 | - | - | - |
| 20ms-50mbit | heavy | helper-relay | 2 | 0.002 | 9 / 10 | 0.103 | 190 / 193 | 0.111 | 47 / 48 | - |
| 20ms-50mbit | heavy | off-relay | 2 | 0.011 | 9 / 10 | 0.230 | 221 / 222 | - | - | - |
| 80ms-10mbit | busy | all-relay | 2 | 0.010 | 9 / 9 | 0.139 | 205 / 211 | - | - | - |
| 80ms-10mbit | busy | helper-relay | 2 | 0.003 | 9 / 10 | 0.075 | 179 / 181 | 0.174 | 52 / 56 | - |
| 80ms-10mbit | busy | off-relay | 2 | 0.005 | 9 / 9 | 0.145 | 217 / 224 | - | - | - |
| 80ms-10mbit | busy | stdhelper-relay | 2 | 0.006 | 9 / 9 | 0.774 | 188 / 193 | 0.213 | 53 / 58 | - |
| 80ms-10mbit | heavy | all-relay | 2 | 0.007 | 9 / 10 | 0.120 | 210 / 213 | - | - | - |
| 80ms-10mbit | heavy | helper-relay | 2 | 0.003 | 10 / 10 | 0.096 | 185 / 189 | 0.054 | 41 / 42 | - |
| 80ms-10mbit | heavy | off-relay | 2 | 0.002 | 10 / 10 | 0.126 | 221 / 222 | - | - | - |
| 80ms-10mbit | heavy | stdhelper-relay | 1 | 0.002 | 10 / 10 | 0.764 | 197 / 198 | 0.060 | 50 / 51 | - |
| 80ms-10mbit | idle | all-relay | 2 | 0.001 | 9 / 9 | 0.017 | 203 / 207 | - | - | - |
| 80ms-10mbit | idle | helper-relay | 2 | 0.001 | 9 / 9 | 0.014 | 183 / 188 | 0.004 | 34 / 34 | - |
| 80ms-10mbit | idle | off-relay | 2 | 0.001 | 9 / 9 | 0.021 | 202 / 208 | - | - | - |
| 80ms-50mbit | busy | all-relay | 2 | 0.014 | 9 / 9 | 0.179 | 217 / 222 | - | - | - |
| 80ms-50mbit | busy | helper-relay | 2 | 0.004 | 9 / 10 | 0.099 | 186 / 190 | 0.229 | 46 / 46 | - |
| 80ms-50mbit | busy | off-relay | 2 | 0.013 | 9 / 9 | 0.221 | 224 / 228 | - | - | - |
| 80ms-50mbit | heavy | all-relay | 2 | 0.014 | 9 / 9 | 0.158 | 217 / 221 | - | - | - |
| 80ms-50mbit | heavy | helper-relay | 2 | 0.002 | 9 / 10 | 0.104 | 188 / 192 | 0.096 | 50 / 51 | - |
| 80ms-50mbit | heavy | off-relay | 2 | 0.010 | 9 / 10 | 0.296 | 227 / 228 | - | - | - |
| 80ms-5mbit | busy | all-relay | 2 | 0.011 | 9 / 9 | 0.160 | 215 / 221 | - | - | - |
| 80ms-5mbit | busy | helper-relay | 2 | 0.002 | 10 / 10 | 0.102 | 191 / 194 | 0.062 | 41 / 45 | - |
| 80ms-5mbit | busy | off-relay | 2 | 0.003 | 9 / 9 | 0.146 | 220 / 222 | - | - | - |
| 80ms-5mbit | heavy | all-relay | 2 | 0.007 | 9 / 9 | 0.135 | 223 / 225 | - | - | - |
| 80ms-5mbit | heavy | helper-relay | 2 | 0.002 | 10 / 10 | 0.101 | 188 / 194 | 0.039 | 37 / 38 | - |
| 80ms-5mbit | heavy | off-relay | 2 | 0.002 | 9 / 10 | 0.135 | 222 / 228 | - | - | - |

| Link | Activity | Proxy | Repeats | Background exact while animating (most px differing) | Frozen SSIM mean / min | Frozen PSNR min dB | Settled (mirror lagged) |
|---|---|---|---|---|---|---|---|
| 0ms-0mbit | busy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 0ms-0mbit | busy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 0ms-0mbit | busy | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 0ms-0mbit | busy | stdhelper-relay | 4 | 0 of 4 (53248) | 1.0000 / 1.0000 | inf | 4 of 4 (0) |
| 0ms-0mbit | heavy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 0ms-0mbit | heavy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 0ms-0mbit | heavy | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 0ms-0mbit | heavy | stdhelper-relay | 4 | 0 of 4 (53248) | 1.0000 / 1.0000 | inf | 4 of 4 (0) |
| 0ms-0mbit | idle | all-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 0ms-0mbit | idle | helper-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 0ms-0mbit | idle | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 150ms-2mbit | busy | all-relay | 4 | 0 of 4 (7680) | 1.0000 / 1.0000 | inf | 4 of 4 (0) |
| 150ms-2mbit | busy | off-relay | 4 | 4 of 4 (0) | 0.9888 / 0.9888 | 43.9 | 4 of 4 (0) |
| 150ms-2mbit | heavy | all-relay | 4 | 4 of 4 (0) | 1.0000 / 1.0000 | inf | 4 of 4 (0) |
| 20ms-50mbit | busy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 20ms-50mbit | busy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 20ms-50mbit | busy | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 20ms-50mbit | heavy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 20ms-50mbit | heavy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 20ms-50mbit | heavy | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | busy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | busy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | busy | off-relay | 2 | 2 of 2 (0) | 0.9888 / 0.9888 | 43.9 | 2 of 2 (0) |
| 80ms-10mbit | busy | stdhelper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | heavy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | heavy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | heavy | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | heavy | stdhelper-relay | 1 | 0 of 1 (53248) | 1.0000 / 1.0000 | inf | 1 of 1 (0) |
| 80ms-10mbit | idle | all-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | idle | helper-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-10mbit | idle | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-50mbit | busy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-50mbit | busy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-50mbit | busy | off-relay | 2 | 2 of 2 (0) | 0.9888 / 0.9888 | 43.9 | 2 of 2 (0) |
| 80ms-50mbit | heavy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-50mbit | heavy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-50mbit | heavy | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-5mbit | busy | all-relay | 2 | 0 of 2 (7680) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-5mbit | busy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-5mbit | busy | off-relay | 2 | 2 of 2 (0) | 0.9888 / 0.9888 | 43.9 | 2 of 2 (0) |
| 80ms-5mbit | heavy | all-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-5mbit | heavy | helper-relay | 2 | 0 of 2 (53248) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
| 80ms-5mbit | heavy | off-relay | 2 | 2 of 2 (0) | 1.0000 / 1.0000 | inf | 2 of 2 (0) |
