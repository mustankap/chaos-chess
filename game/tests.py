import chess
import json
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from .models import Game

from .chaos import apply_move, load_state
from .chessAI import call_chaos_AI


class ChaosChessTests(SimpleTestCase):
    def test_piece_defects_when_its_stamina_reaches_zero(self):
        fen, raw_state = apply_move(None, '{"e2": 1}', "e2", "e4", True)

        self.assertEqual(chess.Board(fen).piece_at(chess.E4), chess.Piece(chess.PAWN, chess.BLACK))
        self.assertEqual(load_state(raw_state)["e4"], 0)

    def test_captured_piece_is_kept_in_chaos_state(self):
        fen, state = apply_move(None, None, "e2", "e4", True)
        fen, state = apply_move(fen, state, "d7", "d5", False)
        _, state = apply_move(fen, state, "e4", "d5", True)

        self.assertEqual(load_state(state)["_captured"], [{"color": "black", "piece": "p"}])

    def test_ai_finds_mate_but_avoids_a_defecting_queen(self):
        fen, state = None, None
        for source, target, white in [
            ("f2", "f3", True), ("e7", "e5", False), ("g2", "g4", True),
        ]:
            fen, state = apply_move(fen, state, source, target, white)

        self.assertEqual(call_chaos_AI(fen, state, 1), "d8h4")
        state = load_state(state)
        state["d8"] = 9
        self.assertNotEqual(call_chaos_AI(fen, json.dumps(state), 1), "d8h4")


class CreateGameTests(TestCase):
    def test_create_page_lists_only_previous_opponents(self):
        player = User.objects.create_user(username="player", password="password")
        alice = User.objects.create_user(username="alice", password="password")
        bob = User.objects.create_user(username="bob", password="password")
        outsider = User.objects.create_user(username="outsider", password="password")
        Game.objects.create(owner=player, opponent=alice, status=2)
        Game.objects.create(owner=bob, opponent=player, status=2)
        Game.objects.create(owner=alice, opponent=outsider, status=2)

        self.client.force_login(player)
        response = self.client.get("/create/")

        self.assertEqual([user.username for user in response.context["opponents"]], ["alice", "bob"])
