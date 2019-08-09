# Requests-Racer

Requests-Racer is a small Python library that lets you use the [Requests library](https://requests.readthedocs.io/) to submit multiple requests that will be processed by their destination servers at approximately the same time, even if the requests have different destinations or have payloads of different sizes. This can be helpful for detecting and exploiting race condition vulnerabilities in web applications. (For more information, see [`motivation.md`](motivation.md).)

# Disclaimer

Requests (and urllib3, which Requests uses internally) were never intended to allow you to do somehting like this. Requests-Racer is therefore forced to resort to some fairly terrible hacks to get fine-grained control over how requests are submitted.

These hacks include messing with the private state of some urllib3 objects, so an urllib3 update that is backwards-compatible w.r.t. its public API might still break Requests-Racer. Therefore, I recommend using Requests-Racer in a virtualenv and installing it *before* Requests or urllib3, so that a known-compatible version of these libraries is pulled as a dependancy.

# Installation

To use Requests-Racer, you will need Python 3.5 or later. First, create and activate a Python virtual environment:

```
python3 -m venv env
source env/bin/activate
```

Then download a copy of the library and install it:

```
git clone https://github.com/nccgroup/requests-racer.git
cd requests-racer
python setup.py install
```

# Usage

Requests-Racer works by providing an alternative [Transport Adapter](https://requests.readthedocs.io/en/master/user/advanced/#transport-adapters) for Requests called `SynchronizedAdapter`. It will collect all requests that you make through it and finish them only when the `finish_all()` method is called:

```python
import requests
from requests_racer import SynchronizedAdapter

s = requests.Session()
sync = SynchronizedAdapter()
s.mount('http://', sync)
s.mount('https://', sync)

resp1 = s.get('http://example.com/a', params={'hello': 'world'})
resp2 = s.post('https://example.net/b', data={'one': 'two'})

# at this point, the requests have been started but not finished.
# resp1 and resp2 should *not* be used.

sync.finish_all()

print(resp1.status_code)
print(resp2.text)
```

To make your code simpler, you can also use `SynchronizedSession`, which is just a `requests.Session` object that automatically mounts a `SynchronizedAdapter` for HTTP[S] and proxies the `finish_all()` method, so the code above can be rewritten as follows:

```python
from requests_racer import SynchronizedSession

s = SynchronizedSession()

resp1 = s.get('http://example.com/a', params={'hello': 'world'})
resp2 = s.post('https://example.net/b', data={'one': 'two'})

# at this point, the requests have been started but not finished.
# resp1 and resp2 should *not* be used.

s.finish_all()

print(resp1.status_code)
print(resp2.text)
```

Here are some caveats to keep in mind and fancier things you can do:

- `SynchronizedAdapter` is *not* thread-safe.
- Requests made through a `SynchronizedAdapter` will *not* update the session object (e.g., cookies from `Set-Cookie` headers will not be added to the session's cookie jar).
- Redirects might not be followed, try to avoid them.
- If an exception occurs while starting a request, it will be re-raised exactly like with regular requests. However, if one occurs while finishing a request, it will not be re-raised. Instead, the response to the request will have status code `999` and contain a traceback in its `.text` attribute.
- `SynchronizedSession.from_regular_session` constructs a `SynchronizedSession` from a `requests.Session` instance. This is helpful if you need to make some simple requests to e.g. log into a service and get a session cookie that you'll need for the synchronized requests.
- `SynchronizedAdapter` and `SynchronizedSession` accept an optional parameter called `num_threads`, which gives the maximum number of threads the adapter will use. If not specified, the adapter will use one thread per request.
- `finish_all()` accepts an optional parameter called `timeout`, which gives the maximum amount of time (in seconds) that the adapter will wait for a thread to finish.

See [`benchmark/`](benchmark/) for notes on performance.

# License

Copyright (C) 2019 Aleksejs Popovs, NCC Group

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
