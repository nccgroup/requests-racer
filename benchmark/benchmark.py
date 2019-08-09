import sys
import time

from requests_racer import SynchronizedSession

# the script is just "<?= microtime(true) ?>"
URL = 'http://example.com/time_raw.php'
TRIES = 5

def median(l):
	n = len(l)
	ls = sorted(l)
	if n % 2 == 1:
		return ls[n // 2]
	return (ls[n // 2 - 1] + ls[n // 2]) / 2

def main():
	session = SynchronizedSession()

	for i in range(10):
		n = 2**i

		spreads = []

		for payload_size in [0, 128, 1024, 2048, 4096]:
			spreads_here = []

			for j in range(TRIES):
				if payload_size == 0:
					requests = [session.get(URL) for _ in range(n)]
				else:
					requests = [session.post(URL, data='a'*payload_size) for _ in range(n)]

				session.finish_all()

				request_times = [float(r.text) for r in requests]
				spread = max(request_times) - min(request_times)

				spreads_here.append(spread)

			spreads.append(median(spreads_here))

		spreads_ms = [round(t * 1000) for t in spreads]
		print(n, *spreads_ms, sep=' | ')

if __name__ == '__main__':
	main()
