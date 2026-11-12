from channels.generic.websocket import AsyncJsonWebsocketConsumer, JsonWebsocketConsumer
from channels.db import database_sync_to_async
from urllib.parse import parse_qs
import chess
from .models import Game
from django.contrib.auth.models import User
from .chessAI import call_AI, call_chaos_AI
from .chaos import apply_move


class GameConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.joined = False
        if self.scope["user"].is_anonymous:
            await self.close()
            return
        self.game_id = self.scope['url_route']['kwargs']['game_id']
        try: 
            self.game_id=int(self.game_id)
        except:
            await self.close()
            return
        side = await self.verify(self.game_id)
        if side == False:
            await self.close()
            return
        await self.accept()
        await self.join_room(side)
        self.joined = True
        if side[2]:
            await self.opp_online()

    async def receive_json(self, content):
        command = content.get("command", None)
        try:
            if command == "new-move":
                if await self.is_chaos_game():
                    await self.chaos_move(content["source"], content["target"])
                else:
                    await self.new_move(content["source"],content["target"],content["fen"],content["pgn"])
            elif command == "game-over":
                await self.game_over(content["result"])
            elif command == "resign":
                await self.resign()
                await self.game_over(content["result"])
        except:
            pass

    async def disconnect(self, code):
        if not self.joined:
            return
        await self.channel_layer.group_discard(str(self.game_id), self.channel_name)
        await self.disconn()
        await self.opp_offline()

    async def join_room(self, data):
        await self.channel_layer.group_add(
            str(self.game_id),
            self.channel_name,
        )
        await self.send_json({
            "command":"join",
            "orientation": data[0],
            "pgn": data[1],
            "opp_online": data[2],
            "variant": data[3],
            "fen": data[4],
            "chaos_state": data[5],
        })

    async def opp_offline(self):
        await self.channel_layer.group_send(
            str(self.game_id),
            {
                "type": "offline.opp",
                'sender_channel_name': self.channel_name
            }
        )
    
    async def offline_opp(self,event):
        if self.channel_name != event['sender_channel_name']:
            await self.send_json({
                "command":"opponent-offline",
            })
            print("sending offline")

    async def opp_online(self):
        await self.channel_layer.group_send(
            str(self.game_id),
            {
                "type": "online.opp",
                'sender_channel_name': self.channel_name
            }
        )
    
    async def online_opp(self,event):
        if self.channel_name != event['sender_channel_name']:
            await self.send_json({
                "command":"opponent-online",
            })

    async def resign(self):
        await self.channel_layer.group_send(
            str(self.game_id),
            {
                "type": "resign.game",
                'sender_channel_name': self.channel_name
            }
        )
    
    async def resign_game(self,event):
        if self.channel_name != event['sender_channel_name']:
            await self.send_json({
                "command":"opponent-resigned",
            })

    async def new_move(self, source, target, fen, pgn):
        await self.channel_layer.group_send(
            str(self.game_id),
            {
                "type": "move.new",
                "source": source,
                "target": target,
                "fen": fen,
                "pgn": pgn,
                'sender_channel_name': self.channel_name
            }
        )

    async def chaos_move(self, source, target):
        move = await self.save_chaos_move(source, target)
        if not move:
            await self.send_json({"command": "invalid-move"})
            return
        await self.channel_layer.group_send(
            str(self.game_id),
            {"type": "chaos.state", **move},
        )

    async def chaos_state(self, event):
        await self.send_json({
            "command": "chaos-state",
            "fen": event["fen"],
            "chaos_state": event["chaos_state"],
        })
    
    async def move_new(self, event):
        if self.channel_name != event['sender_channel_name']:
            await self.send_json({
                "command":"new-move",
                "source": event["source"],
                "target": event["target"],
                "fen": event["fen"],
                "pgn": event["pgn"],
            })
        await self.update(event["fen"],event["pgn"])

    @database_sync_to_async
    def game_over(self, result):
        game = Game.objects.all().filter(id=self.game_id)[0]
        if game.status == 3:
            return
        game.winner = result
        game.status = 3
        game.save()

    @database_sync_to_async
    def verify(self, game_id):
        game = Game.objects.filter(id=game_id).first()
        if not game:
            return False
        user = self.scope["user"]
        side = "white"
        opp=False
        if game.opponent == user:
            game.opponent_online = True
            if game.owner_side == "white":
                side = "black"
            else:
                side = "white"
            if game.owner_online == True:
                opp = True
            print("Setting opponent online")
        elif game.owner == user:
            game.owner_online = True
            if game.owner_side == "white":
                side = "white"
            else:
                side = "black"
            if game.opponent_online == True:
                opp = True
            print("Setting owner online")
        else:
            return False
        game.save()
        return [side, game.pgn, opp, game.variant, game.fen, game.chaos_state]

    @database_sync_to_async
    def is_chaos_game(self):
        return Game.objects.filter(id=self.game_id, variant="chaos").exists()

    @database_sync_to_async
    def save_chaos_move(self, source, target):
        game = Game.objects.filter(id=self.game_id, status=2, variant="chaos").first()
        if not game:
            return None
        user = self.scope["user"]
        if user == game.owner:
            player_is_white = game.owner_side == "white"
        elif user == game.opponent:
            player_is_white = game.owner_side != "white"
        else:
            return None
        try:
            game.fen, game.chaos_state = apply_move(
                game.fen, game.chaos_state, source, target, player_is_white
            )
        except (TypeError, ValueError):
            return None
        game.save(update_fields=["fen", "chaos_state"])
        return {"fen": game.fen, "chaos_state": game.chaos_state}

    @database_sync_to_async
    def disconn(self):
        user = self.scope["user"]
        game = Game.objects.all().filter(id=self.game_id)[0]
        if game.opponent == user:
            game.opponent_online = False
            print("Setting opponent offline")
        elif game.owner == user:
            game.owner_online = False
            print("Setting owner offline")
        game.save()
        
    @database_sync_to_async
    def update(self, fen, pgn):
        game = Game.objects.all().filter(id=self.game_id)[0]
        if not game:
            print("Game not found")
            return
        game.fen = fen
        game.pgn = pgn
        game.save()
        print("Saving game details")


class SingleConsumer(JsonWebsocketConsumer):
    def connect(self):
        if self.scope["user"].is_anonymous:
            self.close()
            return
        query = parse_qs(self.scope.get("query_string", b"").decode())
        self.chaos_mode = query.get("variant") == ["chaos"]
        # shortcut: a reconnect starts a new Chaos game; persist state when resume is needed.
        self.fen = chess.STARTING_FEN
        self.chaos_state = None
        self.accept()
        self.send_json({
            "command": "join", "orientation": "white",
            "variant": "chaos" if self.chaos_mode else "standard",
            "fen": self.fen, "chaos_state": self.chaos_state,
        })

    def receive_json(self, content):
        command = content.get("command", None)
        if self.chaos_mode:
            if command == "new-move":
                self.chaos_move(content)
            return
        try:
            if command == "new-move":
                move = call_AI(content["pgn"], int(content["level"]))
                print(move)
                self.send_json({"command": "new-move", "move": move})
            else: 
                pass
        except Exception as e:
            print(f"Error: {e}")

    def chaos_move(self, content):
        try:
            level = max(1, min(3, int(content.get("level", 3))))
            self.fen, self.chaos_state = apply_move(
                self.fen, self.chaos_state, content["source"], content["target"], True,
            )
        except (KeyError, TypeError, ValueError):
            self.send_json({"command": "invalid-move"})
            return

        ai_turn = not chess.Board(self.fen).is_game_over(claim_draw=True)
        self.send_json({
            "command": "chaos-state", "fen": self.fen,
            "chaos_state": self.chaos_state, "ai_thinking": ai_turn,
        })
        if ai_turn:
            move = call_chaos_AI(self.fen, self.chaos_state, level)
            if move:
                self.fen, self.chaos_state = apply_move(
                    self.fen, self.chaos_state, move[:2], move[2:4], False,
                )
            self.send_json({
                "command": "chaos-state", "fen": self.fen,
                "chaos_state": self.chaos_state, "ai_thinking": False,
            })

    def disconnect(self, code):
        pass
