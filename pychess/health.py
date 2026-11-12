from django.conf import settings
from django.db import connection, DatabaseError
from django.http import JsonResponse
from redis import Redis, RedisError


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        with Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1) as client:
            client.ping()
    except (DatabaseError, RedisError):
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})
