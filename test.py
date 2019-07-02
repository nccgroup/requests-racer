from synchronized_session import SynchronizedSession

s = SynchronizedSession(100)
s.cookies.update({'session': 'asdf', 'gdpr_consent': 'no'})

def g():
    yield b'hello'
    yield b'world'

responses = [
    s.post('http://aleksejs.scripts.mit.edu/race.php',
        params={'q': 0},
        data={'hello': 'world', 'whoam': 'i'},
        files={'a': ('asd.txt', 'asd')},
        auth=('aleksejs', 'p@ssword'),
    ),
    s.post('http://aleksejs.scripts.mit.edu/race.php', params={'q': 1}, data={'a': 's'}),
    # TODO: document the fact that chunked stuff can be broken server-side
    # s.post('http://aleksejs.scripts.mit.edu/race.php', params={'q': 2}, data=g()),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
    s.get('http://aleksejs.scripts.mit.edu/race.php', params={'hi': 'world'}),
]

print('before finishing:')
for resp in responses:
    print(resp, resp.text[:30])

import time
time.sleep(5)
print(time.time())

print('finishing:')
s.finish_all()

print('after:')
for resp in responses:
    print(resp, resp.text[:30])