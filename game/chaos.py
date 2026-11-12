import json

import chess


MOVE_COSTS = {
    chess.PAWN: 1,
    chess.KNIGHT: 4,
    chess.BISHOP: 4,
    chess.ROOK: 5,
    chess.QUEEN: 9,
}


def load_state(raw_state):
    try:
        state = json.loads(raw_state or "{}")
        return state if isinstance(state, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def apply_move(fen, raw_state, source, target, player_is_white):
    """Apply one legal Chaos Chess move and return its board and stamina state."""
    board = chess.Board(fen) if fen else chess.Board()
    move = chess.Move.from_uci(f"{source}{target}q")
    if move not in board.legal_moves:
        move = chess.Move.from_uci(f"{source}{target}")
    if move not in board.legal_moves or board.turn != player_is_white:
        raise ValueError("Illegal move")

    piece = board.piece_at(move.from_square)
    if not piece:
        raise ValueError("No piece on source square")

    state = load_state(raw_state)
    captured = state.setdefault("_captured", [])
    source_name = chess.square_name(move.from_square)
    target_name = chess.square_name(move.to_square)
    previous_score = state.pop(source_name, 20)
    moving_score = previous_score - MOVE_COSTS.get(piece.piece_type, 0)

    if board.is_en_passant(move):
        captured_square = move.to_square - 8 if piece.color else move.to_square + 8
        captured_piece = board.piece_at(captured_square)
        captured.append({"color": "white" if captured_piece.color else "black", "piece": "p"})
        state.pop(chess.square_name(captured_square), None)
    else:
        captured_piece = board.piece_at(move.to_square)
        if captured_piece:
            captured.append({"color": "white" if captured_piece.color else "black", "piece": captured_piece.symbol().lower()})

    rook_move = None
    if board.is_castling(move):
        if move.to_square > move.from_square:
            rook_move = (move.to_square + 1, move.to_square - 1)
        else:
            rook_move = (move.to_square - 2, move.to_square + 1)

    board.push(move)
    moved_piece = board.piece_at(move.to_square)
    if previous_score > 0 and moving_score <= 0:
        board.set_piece_at(move.to_square, chess.Piece(moved_piece.piece_type, not moved_piece.color))
    state[target_name] = moving_score

    if rook_move:
        rook_from, rook_to = rook_move
        rook_previous_score = state.pop(chess.square_name(rook_from), 20)
        rook_score = rook_previous_score - MOVE_COSTS[chess.ROOK]
        rook = board.piece_at(rook_to)
        if rook_previous_score > 0 and rook_score <= 0:
            board.set_piece_at(rook_to, chess.Piece(chess.ROOK, not rook.color))
        state[chess.square_name(rook_to)] = rook_score

    return board.fen(), json.dumps(state, separators=(",", ":"))
