"""スマホの交代モード。game.py と rules.py だけを読む。

点は外から渡す。畳み込みはブラウザが行う。手を選ぶ材料は、見えている局面だけである。
"""

from __future__ import annotations

import json
import random
from collections import deque

from tarutaru.game import (
    Action,
    Game,
    Position,
    line_values,
    matching_lines,
    pack_line,
    set_line,
)
from tarutaru.rules import BARREL_FRAMES, CURSOR_FRAMES, MAX_CURSOR_SLACK, SIZE

N_ACTIONS = SIZE * 2 * 2
N_PLANES = 10
LINES_BINS = 26
LINES_DIM = LINES_BINS + 1
N_SYMMETRIES = 8
FRAME_SCALE = 5 * (CURSOR_FRAMES + BARREL_FRAMES)
MIN_POLICY_MOVES = 30
MAX_POLICY_MOVES = 60
UNDO_LIMIT = 3
RESERVED_SEEDS = set(range(1, 11)) | set(range(300, 350))
_LAST = SIZE - 1


def action_from_index(index: int) -> Action:
    axis = "row" if index < SIZE * 2 else "col"
    local = index % (SIZE * 2)
    return Action(axis, local // 2, local % 2 == 0)


def action_index(action: Action) -> int:
    base = 0 if action.axis == "row" else SIZE * 2
    return base + action.index * 2 + (0 if action.toward_start else 1)


def board_key(board) -> tuple[tuple[int, ...], ...]:
    return tuple(tuple(int(cell) for cell in row) for row in board)


def packed_board_key(position: Position, action: Action) -> tuple[tuple[int, ...], ...] | None:
    packed, distance = pack_line(line_values(position.board, action.axis, action.index), action.toward_start)
    if distance == 0:
        return None
    board = [row[:] for row in position.board]
    set_line(board, action.axis, action.index, packed)
    return board_key(board)


def moving_indices(position: Position, seen: set | None) -> list[int]:
    """タルが動く手。見た盤面へ戻る手は、他にあれば外す。"""

    if position.time_left <= 0:
        return []
    moving = []
    fresh = []
    for index in range(N_ACTIONS):
        key = packed_board_key(position, action_from_index(index))
        if key is None:
            continue
        moving.append(index)
        if seen is None or key not in seen:
            fresh.append(index)
    return fresh or moving


def tips_allowed_counts(lines_cleared: int) -> frozenset[int] | None:
    if lines_cleared in (0, 4, 8, 17):
        return frozenset({4})
    if lines_cleared == 12:
        return frozenset({2, 3})
    if lines_cleared == 14:
        return frozenset({3})
    if lines_cleared == 15:
        return frozenset({2})
    if lines_cleared >= 21 and (lines_cleared - 21) % 4 == 0:
        return frozenset({4})
    return None


def packed_clear_count(position: Position, action: Action) -> int:
    after = position.after_pack(action)
    return len(matching_lines(after.board, "row")) + len(matching_lines(after.board, "col"))


def masked_indices(position: Position, seen: set | None) -> list[int]:
    """動く手から、許されない本数の消去を外す。残らなければ動く手のまま。"""

    indices = moving_indices(position, seen)
    allowed = tips_allowed_counts(position.lines_cleared)
    if allowed is None or not indices:
        return indices
    kept = []
    for index in indices:
        size = packed_clear_count(position, action_from_index(index))
        if size == 0 or size in allowed:
            kept.append(index)
    return kept or indices


def _maps():
    return (
        lambda r, c: (r, c),
        lambda r, c: (c, _LAST - r),
        lambda r, c: (_LAST - r, _LAST - c),
        lambda r, c: (_LAST - c, r),
        lambda r, c: (r, _LAST - c),
        lambda r, c: (_LAST - r, c),
        lambda r, c: (c, r),
        lambda r, c: (_LAST - c, _LAST - r),
    )


_MAPS = _maps()


def _sources():
    sources = []
    for fn in _MAPS:
        grid = [[(0, 0) for _ in range(SIZE)] for _ in range(SIZE)]
        for row in range(SIZE):
            for col in range(SIZE):
                out_row, out_col = fn(row, col)
                grid[out_row][out_col] = (row, col)
        sources.append(grid)
    return sources


_SOURCES = _sources()
_SWAPS = tuple(fn(0, 0)[0] != fn(0, 1)[0] for fn in _MAPS)


def _action_from_cells(fn, action: Action) -> Action:
    if action.axis == "row":
        cells = [(action.index, col) for col in range(SIZE)]
    else:
        cells = [(row, action.index) for row in range(SIZE)]
    if not action.toward_start:
        cells = list(reversed(cells))
    mapped = [fn(row, col) for row, col in cells]
    rows = [row for row, _ in mapped]
    cols = [col for _, col in mapped]
    if len(set(rows)) == 1:
        return Action("row", rows[0], cols[0] < cols[-1])
    return Action("col", cols[0], rows[0] < rows[-1])


_ACTION_INDEX = tuple(
    tuple(action_index(_action_from_cells(fn, action_from_index(index))) for index in range(N_ACTIONS))
    for fn in _MAPS
)


def transform_board(board, index: int) -> list[list[int]]:
    source = _SOURCES[index]
    return [[board[source[row][col][0]][source[row][col][1]] for col in range(SIZE)] for row in range(SIZE)]


def transform_action_index(index: int, symmetry: int) -> int:
    return _ACTION_INDEX[symmetry][index]


def transform_position(position: Position, index: int) -> Position:
    row, col = _MAPS[index](position.row_cursor, position.col_cursor)
    if _SWAPS[index]:
        row_slack, col_slack = position.col_slack, position.row_slack
    else:
        row_slack, col_slack = position.row_slack, position.col_slack
    return Position(
        transform_board(position.board, index),
        position.time_left,
        position.lines_cleared,
        row,
        col,
        row_slack,
        col_slack,
    )


def lines_features(lines_cleared: int) -> list[float]:
    features = [0.0] * LINES_DIM
    features[min(lines_cleared, LINES_BINS - 1)] = 1.0
    features[-1] = lines_cleared / 64.0
    return features


def encode_planes(after: Position, frames: int) -> list[float]:
    """寄せたあとの局面を、10枚の 6×6 平面にする。並びは (平面, 行, 列)。"""

    planes = [0.0] * (N_PLANES * SIZE * SIZE)
    for row in range(SIZE):
        for col in range(SIZE):
            color = int(after.board[row][col])
            planes[(color * SIZE + row) * SIZE + col] = 1.0
    for col in range(SIZE):
        planes[(5 * SIZE + after.row_cursor) * SIZE + col] = 1.0
    for row in range(SIZE):
        planes[(6 * SIZE + row) * SIZE + after.col_cursor] = 1.0
    row_slack = after.row_slack / MAX_CURSOR_SLACK
    col_slack = after.col_slack / MAX_CURSOR_SLACK
    frame = frames / FRAME_SCALE
    for row in range(SIZE):
        for col in range(SIZE):
            planes[(7 * SIZE + row) * SIZE + col] = row_slack
            planes[(8 * SIZE + row) * SIZE + col] = col_slack
            planes[(9 * SIZE + row) * SIZE + col] = frame
    return planes


def score_inputs(position: Position) -> tuple[list[float], list[float]]:
    """8つの対称と24手の平面。点の並びは、対称ごとに元の手の番号順である。"""

    planes: list[float] = []
    for symmetry in range(N_SYMMETRIES):
        transformed = transform_position(position, symmetry)
        for index in range(N_ACTIONS):
            action = action_from_index(transform_action_index(index, symmetry))
            after = transformed.after_pack(action)
            planes.extend(encode_planes(after, transformed.time_left - after.time_left))
    return planes, lines_features(position.lines_cleared)


def mean_scores(scores: list[float]) -> list[float]:
    if len(scores) != N_SYMMETRIES * N_ACTIONS:
        raise RuntimeError("点の数が、対称と手の数に合いません")
    totals = [0.0] * N_ACTIONS
    for symmetry in range(N_SYMMETRIES):
        start = symmetry * N_ACTIONS
        for index in range(N_ACTIONS):
            totals[index] += scores[start + index]
    return [value / N_SYMMETRIES for value in totals]


def choose_index(position: Position, seen: set | None, scores: list[float]) -> int:
    allowed = masked_indices(position, seen)
    if not allowed:
        raise RuntimeError("タルが動く操作がありません")
    averaged = mean_scores(scores)
    best = allowed[0]
    best_score = averaged[best]
    for index in allowed[1:]:
        if averaged[index] > best_score:
            best = index
            best_score = averaged[index]
    return best


def export_game(game: Game) -> dict:
    version, state, gauss = game.rng.getstate()
    return {
        "board": [list(row) for row in game.board],
        "time_left": game.time_left,
        "lines_cleared": game.lines_cleared,
        "score": game.score,
        "row_cursor": game.row_cursor,
        "col_cursor": game.col_cursor,
        "row_slack": game.row_slack,
        "col_slack": game.col_slack,
        "done": game.done,
        "rng": [version, list(state), gauss],
    }


def restore_game(start: dict, seed: int) -> Game:
    game = Game(
        seed=seed,
        board=start["board"],
        time_left=int(start["time_left"]),
        lines_cleared=int(start["lines_cleared"]),
        row_cursor=int(start["row_cursor"]),
        col_cursor=int(start["col_cursor"]),
        row_slack=int(start["row_slack"]),
        col_slack=int(start["col_slack"]),
    )
    game.score = int(start["score"])
    game.done = bool(start["done"])
    version, state, gauss = start["rng"]
    game.rng.setstate((version, tuple(state), gauss))
    return game


def _allowed_label(lines_cleared: int) -> str:
    allowed = tips_allowed_counts(lines_cleared)
    if not allowed:
        return ""
    return "か".join(str(count) for count in sorted(allowed))


def _check_seed(seed: int) -> None:
    if seed in RESERVED_SEEDS:
        raise ValueError("種1から10と種300から349は記録しません")


def _new_seed(current: int, rng: random.Random) -> int:
    while True:
        seed = rng.randrange(1000, 1_000_000_000)
        if seed != current:
            return seed


class CoachSession:
    """1つの種で、方策の1手と人間の番を交互に進める。"""

    def __init__(self, seed: int | None = None, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()
        self.repairs: list[dict] = []
        self._pending: dict | None = None
        self.start_game(_new_seed(0, self.rng) if seed is None else int(seed))

    def start_game(self, seed: int) -> None:
        _check_seed(seed)
        self.seed = seed
        self.game = Game(seed=seed)
        self.seen = {board_key(self.game.board)}
        self.phase = "policy"
        self.since_score = 0
        self.budget = self.rng.randint(MIN_POLICY_MOVES, MAX_POLICY_MOVES)
        self.progress = 0
        self.reason = ""
        self.actions: list[Action] = []
        self.undos = 0
        self.turn_start: dict | None = None
        self._history: deque = deque(maxlen=UNDO_LIMIT)
        self._pending = None

    def begin_policy_move(self) -> dict:
        """点を付ける材料を返す。渡すときと終わるときは kind が idle。"""

        self._settle_policy()
        if self.phase != "policy":
            return {"kind": "idle"}
        position = self.game.visible()
        planes, lines = score_inputs(position)
        self._pending = {"position": position, "seen": set(self.seen)}
        return {"kind": "score", "planes": planes, "lines": lines, "count": N_SYMMETRIES * N_ACTIONS}

    def finish_policy_move(self, scores: list[float]) -> None:
        pending = self._pending
        self._pending = None
        if pending is None or self.phase != "policy":
            raise RuntimeError("方策の手を待っていません")
        position = pending["position"]
        index = choose_index(position, pending["seen"], list(scores))
        action = action_from_index(index)
        score_before = self.game.score
        self.game.step(action)
        self.progress += 1
        if self.game.score != score_before:
            self.seen.clear()
            self.since_score = 0
        else:
            self.since_score += 1
        self.seen.add(board_key(self.game.board))
        self._settle_policy()

    def slide(self, axis: str, index: int, toward_start: bool) -> str:
        """寄せる。許されない消去は打たない。戻り値は rejected、moved、cleared、idle。"""

        if self.phase != "human":
            return "idle"
        action = Action(axis, int(index), bool(toward_start))
        if pack_line(line_values(self.game.board, axis, action.index), action.toward_start)[1] == 0:
            return "rejected"
        position = self.game.visible()
        allowed = tips_allowed_counts(position.lines_cleared)
        size = packed_clear_count(position, action)
        if allowed is None or (size != 0 and size not in allowed):
            return "rejected"
        self._history.append(
            (self._copy_game(), list(self.actions), self.undos, set(self.seen), self.turn_start)
        )
        score_before = self.game.score
        self.game.step(action)
        self.actions.append(action)
        if self.game.score != score_before:
            self.seen.clear()
        self.seen.add(board_key(self.game.board))
        if size == 0:
            if self.game.done:
                self._finish("時間切れ")
            return "moved"
        self._save_turn()
        if self.game.done:
            self._finish("時間切れ")
        elif tips_allowed_counts(self.game.lines_cleared) is None:
            self._finish("流れから外れました")
        else:
            self._begin_stretch()
        return "cleared"

    def undo(self) -> bool:
        if self.phase != "human" or not self._history:
            return False
        self.game, self.actions, self.undos, seen, self.turn_start = self._history.pop()
        self.undos += 1
        self.seen = seen
        return True

    def skip(self) -> bool:
        if self.phase != "human" or self.actions:
            return False
        self._begin_stretch()
        return True

    def next_game(self) -> None:
        self.start_game(_new_seed(self.seed, self.rng))

    def wait_frames(self, axis: str, index: int) -> int:
        return self.game.visible().cursor_wait(Action(axis, int(index), True))

    def state(self) -> dict:
        game = self.game
        return {
            "seed": self.seed,
            "phase": self.phase,
            "board": [row[:] for row in game.board],
            "score": game.score,
            "lines": game.lines_cleared,
            "time_left": game.time_left,
            "row_cursor": game.row_cursor,
            "col_cursor": game.col_cursor,
            "reason": self.reason,
            "policy_moves": self.budget,
            "since_score": self.since_score,
            "progress": self.progress,
            "allowed": _allowed_label(game.lines_cleared) if self.phase == "human" else "",
            "undo_left": len(self._history),
            "can_skip": self.phase == "human" and not self.actions,
            "repairs": len(self.repairs),
        }

    def snapshot(self) -> dict:
        version, state, gauss = self.rng.getstate()
        return {
            "seed": self.seed,
            "phase": self.phase,
            "since_score": self.since_score,
            "budget": self.budget,
            "progress": self.progress,
            "reason": self.reason,
            "seen": [[list(row) for row in key] for key in self.seen],
            "game": export_game(self.game),
            "actions": [[action.axis, action.index, action.toward_start] for action in self.actions],
            "undos": self.undos,
            "turn_start": self.turn_start,
            "history": [
                {
                    "game": export_game(game),
                    "actions": [[action.axis, action.index, action.toward_start] for action in actions],
                    "undos": undos,
                    "seen": [[list(row) for row in key] for key in seen],
                    "turn_start": start,
                }
                for game, actions, undos, seen, start in self._history
            ],
            "rng": [version, list(state), gauss],
        }

    @classmethod
    def resume(cls, data: dict) -> CoachSession:
        session = cls(int(data["seed"]), random.Random(0))
        version, state, gauss = data["rng"]
        session.rng.setstate((version, tuple(state), gauss))
        session.phase = data["phase"]
        session.since_score = int(data["since_score"])
        session.budget = int(data["budget"])
        session.progress = int(data["progress"])
        session.reason = data["reason"]
        session.game = restore_game(data["game"], session.seed)
        session.seen = {board_key(key) for key in data["seen"]}
        session.actions = [Action(axis, int(index), bool(toward)) for axis, index, toward in data["actions"]]
        session.undos = int(data["undos"])
        session.turn_start = data["turn_start"]
        session._history.clear()
        for item in data["history"]:
            session._history.append(
                (
                    restore_game(item["game"], session.seed),
                    [Action(axis, int(index), bool(toward)) for axis, index, toward in item["actions"]],
                    int(item["undos"]),
                    {board_key(key) for key in item["seen"]},
                    item["turn_start"],
                )
            )
        return session

    def begin_policy_move_json(self) -> str:
        return json.dumps(self.begin_policy_move())

    def finish_policy_move_json(self, scores_json: str) -> str:
        self.finish_policy_move(json.loads(scores_json))
        return self.state_json()

    def slide_json(self, axis: str, index: int, toward_start: bool) -> str:
        status = self.slide(axis, index, toward_start)
        repair = self.repairs[-1] if status == "cleared" else None
        return json.dumps({"status": status, "repair": repair, "state": self.state()})

    def state_json(self) -> str:
        return json.dumps(self.state())

    def snapshot_json(self) -> str:
        return json.dumps(self.snapshot())

    def _settle_policy(self) -> None:
        if self.phase != "policy":
            return
        if self.game.done:
            self._finish("時間切れ")
            return
        if tips_allowed_counts(self.game.lines_cleared) is None:
            self._finish("流れから外れました")

    def take_over(self) -> bool:
        """方策の進行を止めて、人間に渡す。"""

        self._pending = None
        if self.phase != "policy":
            return False
        if self.game.done:
            self._finish("時間切れ")
            return False
        if tips_allowed_counts(self.game.lines_cleared) is None:
            self._finish("流れから外れました")
            return False
        self._handoff()
        return True

    def _handoff(self) -> None:
        self.phase = "human"
        self.actions = []
        self.undos = 0
        self.turn_start = export_game(self.game)
        self._history.clear()

    def _begin_stretch(self) -> None:
        self.phase = "policy"
        self.since_score = 0
        self.budget = self.rng.randint(MIN_POLICY_MOVES, MAX_POLICY_MOVES)
        self.progress = 0
        self.actions = []
        self.turn_start = None
        self._history.clear()

    def _finish(self, reason: str) -> None:
        self.phase = "finished"
        self.reason = reason
        self._pending = None

    def _save_turn(self) -> None:
        if self.turn_start is None or not self.actions:
            return
        self.repairs.append(
            {
                "seed": self.seed,
                "score": self.game.score,
                "undos": self.undos,
                "actions": [[action.axis, action.index, action.toward_start] for action in self.actions],
                "start": self.turn_start,
            }
        )

    def _copy_game(self) -> Game:
        return restore_game(export_game(self.game), self.seed)


def resume_json(text: str) -> CoachSession:
    return CoachSession.resume(json.loads(text))
