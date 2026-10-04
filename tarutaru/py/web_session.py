"""スマホの記録用モードの進行。ブラウザ上の Python で動くので、game.py と rules.py だけを使う。

PC の記録用モードと同じく、寄せるたびに機械と同じ計算で時間を減らし、3手まで戻れる。
"""

from __future__ import annotations

import copy
import json
import random
from collections import deque

from tarutaru.game import Action, Game, line_values, pack_line

# teacher_log.RESERVED_SEEDS と同じ。ブラウザでは学習側のモジュールを読み込めないので、ここにも置く。
RESERVED_SEEDS = set(range(1, 11)) | set(range(300, 350))
UNDO_LIMIT = 3


def new_seed() -> int:
    return random.randrange(1000, 1_000_000_000)


class Session:
    def __init__(self, seed: int | None = None) -> None:
        seed = new_seed() if seed is None else int(seed)
        if seed in RESERVED_SEEDS:
            raise ValueError("種1から10と種300から349は記録しません")
        self.seed = seed
        self.game = Game(seed=seed)
        self.actions: list[Action] = []
        self.undos = 0
        # 補充の乱数ごと戻すので、戻したあとに打ち直しても種から同じ進行になる。
        self._history: deque[Game] = deque(maxlen=UNDO_LIMIT)

    @classmethod
    def resume(cls, record: dict) -> Session:
        """record() で残した途中のゲームを、種から打ち直して続きにする。

        undo_left があれば戻れる手数をそこまで減らす。無いと、打ち直しで戻れる手数が増えてしまう。
        """

        session = cls(record["seed"])
        for axis, index, toward_start in record["actions"]:
            if not session.slide(axis, index, toward_start):
                raise ValueError("打ち直せない寄せが記録にあります")
        session.undos = int(record.get("undos", 0))
        while len(session._history) > int(record.get("undo_left", UNDO_LIMIT)):
            session._history.popleft()
        return session

    def slide(self, axis: str, index: int, toward_start: bool) -> bool:
        """寄せる。タルが動かない寄せは無視して False を返す。"""

        if self.game.done:
            return False
        action = Action(axis, int(index), bool(toward_start))
        if pack_line(line_values(self.game.board, axis, action.index), action.toward_start)[1] == 0:
            return False
        self._history.append(copy.deepcopy(self.game))
        self.game.step(action)
        self.actions.append(action)
        return True

    def undo(self) -> bool:
        if not self._history:
            return False
        self.game = self._history.pop()
        self.actions.pop()
        self.undos += 1
        return True

    def wait_frames(self, axis: str, index: int) -> int:
        return self.game.visible().cursor_wait(Action(axis, int(index), True))

    def state(self) -> dict:
        game = self.game
        return {
            "seed": self.seed,
            "board": [row[:] for row in game.board],
            "score": game.score,
            "lines": game.lines_cleared,
            "time_left": game.time_left,
            "done": game.done,
            "row_cursor": game.row_cursor,
            "col_cursor": game.col_cursor,
            "undo_left": len(self._history),
            "moves": len(self.actions),
        }

    def record(self) -> dict:
        return {
            "seed": self.seed,
            "score": self.game.score,
            "undos": self.undos,
            "actions": [[action.axis, action.index, action.toward_start] for action in self.actions],
        }

    def state_json(self) -> str:
        return json.dumps(self.state())

    def record_json(self) -> str:
        return json.dumps(self.record())


def resume_json(text: str) -> Session:
    return Session.resume(json.loads(text))
