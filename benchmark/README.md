Requests-Racer seems to perform much better when your requests have a POST body.

Here are some benchmarks, collected using `benchmark.py` in this directory running on a laptop on a wireless connection in Seattle, against a server in Boston.

Values in the table are the spread (i.e., max value - min value) of the times when the requests were processed by the server. Values are given in milliseconds and are the median of 5 runs.

| Number of simultaneous requests |  GET | POST 128 bytes | POST 1024 bytes | POST 2048 bytes | POST 4096 bytes |
| ------------------------------- | ---: | -------------: | --------------: | --------------: | --------------: |
| 1 | 0 | 0 | 0 | 0 | 0 |
| 2 | 0 | 0 | 0 | 0 | 0 |
| 4 | 1 | 2 | 2 | 2 | 2 |
| 8 | 21 | 4 | 5 | 4 | 4 |
| 16 | 45 | 8 | 8 | 8 | 9 |
| 32 | 85 | 16 | 19 | 15 | 13 |
| 64 | 172 | 30 | 33 | 29 | 30 |
| 128 | 387 | 62 | 63 | 66 | 53 |
| 256 | 974 | 120 | 123 | 119 | 128 |
| 512 | 2098 | 264 | 249 | 238 | 253 |
