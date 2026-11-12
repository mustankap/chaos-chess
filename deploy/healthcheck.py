"""Check HTTP, database, and Redis from inside the app container."""
import os
import urllib.request

request = urllib.request.Request(
    'http://127.0.0.1:8000/healthz/',
    headers={
        'Host': os.environ['ALLOWED_HOSTS'].split(',')[0],
        'X-Forwarded-Proto': 'https',
    },
)
with urllib.request.urlopen(request, timeout=4) as response:
    if response.status != 200:
        raise SystemExit('Application is unhealthy')
