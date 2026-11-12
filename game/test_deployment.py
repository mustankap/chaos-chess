import os
import json
from unittest.mock import patch

import chess
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TransactionTestCase, override_settings
from redis import ConnectionError

from .models import Game
from pychess.asgi import application


class HealthTests(SimpleTestCase):
    @patch('pychess.health.connection')
    @patch('pychess.health.Redis.from_url')
    def test_health_reports_redis_outage_without_exposing_details(self, redis, db):
        redis.return_value.__enter__.return_value.ping.side_effect = ConnectionError('secret-host')
        response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'unavailable'})


TEST_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
if os.environ.get('TEST_REDIS_URL'):
    TEST_LAYERS = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer',
                               'CONFIG': {'hosts': [os.environ['TEST_REDIS_URL']]}}}


@override_settings(CHANNEL_LAYERS=TEST_LAYERS)
class WebsocketDeploymentTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner')
        self.opponent = User.objects.create_user(username='opponent')
        self.outsider = User.objects.create_user(username='outsider')
        self.game = Game.objects.create(owner=self.owner, opponent=self.opponent,
                                        status=2, variant='chaos')
        self.cookies = {}
        for user in (self.owner, self.opponent, self.outsider):
            client = Client()
            client.force_login(user)
            self.cookies[user.pk] = ('sessionid=' + client.cookies['sessionid'].value).encode()

    def socket(self, user=None, origin=b'http://localhost'):
        headers = [(b'origin', origin)]
        if user:
            headers.append((b'cookie', self.cookies[user.pk]))
        return WebsocketCommunicator(application, f'/game/{self.game.pk}', headers=headers)

    async def test_untrusted_origin_cannot_use_authenticated_session(self):
        socket = self.socket(self.owner, b'https://attacker.example')
        connected, _ = await socket.connect()
        self.assertFalse(connected)
        await socket.disconnect()

    async def test_anonymous_and_nonparticipant_disconnect_cleanly(self):
        for user in (None, self.outsider):
            socket = self.socket(user)
            connected, _ = await socket.connect()
            self.assertFalse(connected)
            await socket.disconnect()

    async def test_authenticated_players_exchange_and_persist_chaos_move(self):
        owner, opponent = self.socket(self.owner), self.socket(self.opponent)
        self.assertTrue((await owner.connect())[0])
        self.assertEqual((await owner.receive_json_from())['command'], 'join')
        self.assertTrue((await opponent.connect())[0])
        self.assertEqual((await opponent.receive_json_from())['command'], 'join')
        self.assertEqual((await owner.receive_json_from())['command'], 'opponent-online')
        await owner.send_json_to({'command': 'new-move', 'source': 'e2', 'target': 'e4'})
        first, second = await owner.receive_json_from(), await opponent.receive_json_from()
        self.assertEqual(first['command'], 'chaos-state')
        self.assertEqual(first, second)
        saved = await database_sync_to_async(Game.objects.get)(pk=self.game.pk)
        self.assertEqual(saved.fen, first['fen'])
        await owner.disconnect()
        await opponent.disconnect()

    async def test_single_player_chaos_applies_human_and_ai_moves(self):
        socket = WebsocketCommunicator(
            application, '/single/?variant=chaos',
            headers=[(b'origin', b'http://localhost'),
                     (b'cookie', self.cookies[self.owner.pk])],
        )
        self.assertTrue((await socket.connect())[0])
        joined = await socket.receive_json_from()
        self.assertEqual(joined['variant'], 'chaos')
        await socket.send_json_to({'command': 'new-move', 'source': 'e2', 'target': 'e4', 'level': 1})
        human = await socket.receive_json_from()
        ai = await socket.receive_json_from()
        self.assertEqual(human['command'], 'chaos-state')
        self.assertTrue(human['ai_thinking'])
        self.assertEqual(json.loads(human['chaos_state'])['e4'], 19)
        self.assertFalse(ai['ai_thinking'])
        self.assertTrue(chess.Board(ai['fen']).turn)
        await socket.disconnect()
