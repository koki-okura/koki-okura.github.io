"""タルタルをフレーム単位で進める。描画はしない。"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from itertools import combinations, product
from typing import Iterable, Sequence

from tarutaru.rules import (
    ALL_COLORS,
    BARREL_COUNT,
    BARREL_FRAMES,
    BLUE,
    CURSOR_FRAMES,
    EMPTY,
    GLYPH,
    GREEN,
    GREEN_AFTER,
    MAX_CURSOR_SLACK,
    RED,
    REFILL_FRAMES,
    SCORE_BY_LINES,
    SIZE,
    TIME_FRAMES,
    YELLOW,
    YELLOW_AFTER,
)

Axis = str  # "row" または "col"
MAX_CHAIN_WAVES = 8


@dataclass(frozen=True)
class Action:
    """1本の行または列を、どちらかの端へ寄せる。"""

    axis: Axis
    index: int
    toward_start: bool


@dataclass(frozen=True)
class StepResult:
    score_gained: int
    total_score: int
    frames_elapsed: int
    time_left: int
    done: bool
    lines_cleared: int


def active_colors(lines_cleared: int) -> list[int]:
    """その消去の直前の累計本数から、補充に使う色を決める。"""

    if lines_cleared >= GREEN_AFTER:
        return [RED, BLUE, YELLOW, GREEN]
    if lines_cleared >= YELLOW_AFTER:
        return [RED, BLUE, YELLOW]
    return [RED, BLUE]


def pack_line(line: Sequence[int], toward_start: bool) -> tuple[list[int], int]:
    """タルの相対順を保ったまま端へ詰める。戻り値は新しい列と最大移動マス数。"""

    barrels = [color for color in line if color != EMPTY]
    empties = [EMPTY] * (SIZE - len(barrels))
    packed = barrels + empties if toward_start else empties + barrels
    old_positions = [index for index, color in enumerate(line) if color != EMPTY]
    new_positions = [index for index, color in enumerate(packed) if color != EMPTY]
    distance = 0
    for old, new in zip(old_positions, new_positions):
        distance = max(distance, abs(old - new))
    return packed, distance


def line_values(board: Sequence[Sequence[int]], axis: Axis, index: int) -> list[int]:
    if axis == "row":
        return list(board[index])
    return [board[row][index] for row in range(SIZE)]


def set_line(board: list[list[int]], axis: Axis, index: int, values: Sequence[int]) -> None:
    if axis == "row":
        board[index] = list(values)
        return
    for row, color in enumerate(values):
        board[row][index] = color


def matching_lines(board: Sequence[Sequence[int]], axis: Axis) -> list[int]:
    found = []
    for index in range(SIZE):
        values = line_values(board, axis, index)
        color = values[0]
        if color != EMPTY and all(cell == color for cell in values):
            found.append(index)
    return found


def format_board(board: Sequence[Sequence[int]]) -> str:
    return "\n".join(" ".join(GLYPH[color] for color in row) for row in board)


def _double_color_assignments() -> dict[int, tuple[tuple[tuple[int, int], ...], ...]]:
    """4色のとき、各ラインが 2,2,1,1 になり、全体の個数も合う組。"""

    pairs = list(combinations(ALL_COLORS, 2))
    targets = {
        1: [0, 0, 1, 1],
        2: [1, 1, 1, 1],
        3: [1, 1, 2, 2],
        4: [2, 2, 2, 2],
    }
    found: dict[int, tuple[tuple[tuple[int, int], ...], ...]] = {}
    for count, target in targets.items():
        matches = []
        for assignment in product(pairs, repeat=count):
            used = Counter()
            for pair in assignment:
                used.update(pair)
            if sorted(used[color] for color in ALL_COLORS) == target:
                matches.append(assignment)
        if not matches:
            raise RuntimeError(f"4色を{count}本補充する割り当てがありません")
        found[count] = tuple(matches)
    return found


DOUBLE_COLOR_ASSIGNMENTS = _double_color_assignments()


def make_line_bundles(rng: random.Random, colors: Sequence[int], line_count: int) -> list[list[int]]:
    """同時に補充する各ラインの中身。ライン内の並びは乱数。"""

    if line_count < 1 or line_count > 4:
        raise RuntimeError(f"一度に補充できるのは1本から4本です: {line_count}")
    if len(colors) == 4:
        assignment = rng.choice(DOUBLE_COLOR_ASSIGNMENTS[line_count])
        bundles = []
        for pair in assignment:
            doubled = set(pair)
            singles = [color for color in colors if color not in doubled]
            bundle = [pair[0], pair[0], pair[1], pair[1], singles[0], singles[1]]
            rng.shuffle(bundle)
            bundles.append(bundle)
        return bundles

    each = SIZE // len(colors)
    base = [color for color in colors for _ in range(each)]
    bundles = []
    for _ in range(line_count):
        bundle = base[:]
        rng.shuffle(bundle)
        bundles.append(bundle)
    return bundles


def _deal(rng: random.Random) -> list[list[int]]:
    pool = [RED] * 12 + [BLUE] * 12 + [EMPTY] * 12
    for _ in range(10000):
        rng.shuffle(pool)
        board = [pool[row * SIZE:(row + 1) * SIZE] for row in range(SIZE)]
        if not matching_lines(board, "row") and not matching_lines(board, "col"):
            return board
    raise RuntimeError("最初から揃っていない配置を作れませんでした")


def _copy_board(board: Iterable[Sequence[int]]) -> list[list[int]]:
    copied = [list(row) for row in board]
    if len(copied) != SIZE or any(len(row) != SIZE for row in copied):
        raise ValueError("盤面は6x6です")
    return copied


def _barrel_count(board: Sequence[Sequence[int]]) -> int:
    return sum(color != EMPTY for row in board for color in row)


def _opposite(axis: Axis) -> Axis:
    return "col" if axis == "row" else "row"


def cursor_wait(cursor: int, slack: int, index: int) -> int:
    """寄せる前にカーソルを待つフレーム。空いていた slack フレームのぶんは、先に動けている。"""

    return max(0, abs(cursor - index) * CURSOR_FRAMES - slack)


def cursor_reached(cursor: int, slack: int, index: int, frames: int) -> int:
    """寄せの前に時間が尽きたとき、残り frames フレームでカーソルが着いている位置。"""

    lines = min(abs(index - cursor), max(0, (slack + frames - 1) // CURSOR_FRAMES))
    return cursor + (lines if index > cursor else -lines)


def _add_slack(slack: int, frames: int) -> int:
    return min(MAX_CURSOR_SLACK, slack + frames)


@dataclass
class Position:
    """手を選ぶときに渡す局面。補充の乱数は持たない。

    row_slack と col_slack は、そのカーソルを最後に寄せに使ってから空いていたフレーム。
    """

    board: list[list[int]]
    time_left: int
    lines_cleared: int
    row_cursor: int
    col_cursor: int
    row_slack: int = 0
    col_slack: int = 0

    def copy(self) -> Position:
        return Position(
            [row[:] for row in self.board],
            self.time_left,
            self.lines_cleared,
            self.row_cursor,
            self.col_cursor,
            self.row_slack,
            self.col_slack,
        )

    def cursor(self, axis: Axis) -> int:
        return self.row_cursor if axis == "row" else self.col_cursor

    def slack(self, axis: Axis) -> int:
        return self.row_slack if axis == "row" else self.col_slack

    def cursor_wait(self, action: Action) -> int:
        return cursor_wait(self.cursor(action.axis), self.slack(action.axis), action.index)

    def after_pack(self, action: Action) -> Position:
        """寄せた盤面へ進める。時間切れの扱いは Game.step に揃える。消去も補充もしない。"""

        pos = self.copy()
        axis = action.axis
        wait = pos.cursor_wait(action)
        if pos.time_left <= wait:
            pos._set_cursor(axis, cursor_reached(pos.cursor(axis), pos.slack(axis), action.index, pos.time_left))
            pos.time_left = 0
            return pos
        pos.time_left -= wait
        pos._set_cursor(axis, action.index)
        values = line_values(pos.board, axis, action.index)
        packed, distance = pack_line(values, action.toward_start)
        slide_frames = min(distance * BARREL_FRAMES, pos.time_left)
        pos.time_left -= slide_frames
        set_line(pos.board, axis, action.index, packed)
        other = _opposite(axis)
        pos._set_slack(axis, _add_slack(0, slide_frames))
        pos._set_slack(other, _add_slack(pos.slack(other), wait + slide_frames))
        return pos

    def _set_cursor(self, axis: Axis, index: int) -> None:
        if axis == "row":
            self.row_cursor = index
        else:
            self.col_cursor = index

    def _set_slack(self, axis: Axis, slack: int) -> None:
        if axis == "row":
            self.row_slack = slack
        else:
            self.col_slack = slack

    def legal_actions(self) -> list[Action]:
        if self.time_left <= 0:
            return []
        actions = []
        for axis in ("row", "col"):
            for index in range(SIZE):
                actions.append(Action(axis, index, True))
                actions.append(Action(axis, index, False))
        return actions

    def motion_frames(self, action: Action) -> int:
        """カーソルを待つ時間とタル移動にかかるフレーム。補充は含まない。"""

        distance = pack_line(line_values(self.board, action.axis, action.index), action.toward_start)[1]
        return self.cursor_wait(action) + distance * BARREL_FRAMES


def require_position(position: Position) -> Position:
    """手を選ぶ引数が、乱数を持つゲームそのものではないことを確かめる。"""

    if not isinstance(position, Position):
        raise TypeError("手を選ぶときは、乱数を含まない局面を渡してください")
    return position


class Game:
    """機械がプレイする1ゲーム。開始演出は持たない。"""

    def __init__(
        self,
        seed: int | None = None,
        board: Sequence[Sequence[int]] | None = None,
        time_left: int = TIME_FRAMES,
        lines_cleared: int = 0,
        row_cursor: int = 0,
        col_cursor: int = 0,
        row_slack: int = 0,
        col_slack: int = 0,
    ) -> None:
        self.rng = random.Random(seed)
        self.board = _deal(self.rng) if board is None else _copy_board(board)
        if _barrel_count(self.board) != BARREL_COUNT:
            raise ValueError("タルはちょうど24個です")
        self.time_left = time_left
        self.lines_cleared = lines_cleared
        self.row_cursor = row_cursor
        self.col_cursor = col_cursor
        self.row_slack = row_slack
        self.col_slack = col_slack
        self.score = 0
        self.done = False
        self.waves: list[int] = []
        if self.time_left <= 0:
            self.time_left = 0
            self.done = True

    def visible(self) -> Position:
        """手を選ぶ材料をコピーして返す。乱数は含めない。"""

        return Position(
            [row[:] for row in self.board],
            self.time_left,
            self.lines_cleared,
            self.row_cursor,
            self.col_cursor,
            self.row_slack,
            self.col_slack,
        )

    def legal_actions(self) -> list[Action]:
        if self.done:
            return []
        actions = []
        for axis in ("row", "col"):
            for index in range(SIZE):
                actions.append(Action(axis, index, True))
                actions.append(Action(axis, index, False))
        return actions

    def motion_frames(self, action: Action) -> int:
        """カーソルを待つ時間とタル移動にかかるフレーム。補充は含まない。"""

        return self._cursor_wait(action) + self._slide_distance(action) * BARREL_FRAMES

    def step(self, action: Action) -> StepResult:
        if self.done:
            raise RuntimeError("終了したゲームは進められません")
        self._check_action(action)
        time_before = self.time_left
        score_before = self.score
        axis = action.axis

        wait = self._cursor_wait(action)
        if self.time_left <= wait:
            reached = cursor_reached(self._cursor(axis), self._slack(axis), action.index, self.time_left)
            self._set_cursor(axis, reached)
            self.time_left = 0
            self.done = True
            return self._result(score_before, time_before)

        self.time_left -= wait
        self._set_cursor(axis, action.index)
        started = self.time_left
        slide_frames = self._apply_slide(action)
        if slide_frames > 0 and self.time_left <= slide_frames:
            self.time_left = 0
        elif slide_frames > 0:
            self.time_left -= slide_frames

        # 寄せが始まったあとの時間切れでも、消去、補充、連鎖まで解決する。
        self._resolve(_opposite(axis))
        busy = started - self.time_left
        other = _opposite(axis)
        self._set_slack(axis, _add_slack(0, busy))
        self._set_slack(other, _add_slack(self._slack(other), wait + busy))
        if self.time_left == 0:
            self.done = True
        self._check_barrels()
        return self._result(score_before, time_before)

    def _result(self, score_before: int, time_before: int) -> StepResult:
        return StepResult(
            score_gained=self.score - score_before,
            total_score=self.score,
            frames_elapsed=time_before - self.time_left,
            time_left=self.time_left,
            done=self.done,
            lines_cleared=self.lines_cleared,
        )

    def _check_action(self, action: Action) -> None:
        if action.axis not in ("row", "col") or not 0 <= action.index < SIZE:
            raise ValueError(f"不正な操作です: {action}")

    def _cursor(self, axis: Axis) -> int:
        return self.row_cursor if axis == "row" else self.col_cursor

    def _set_cursor(self, axis: Axis, index: int) -> None:
        if axis == "row":
            self.row_cursor = index
        else:
            self.col_cursor = index

    def _slack(self, axis: Axis) -> int:
        return self.row_slack if axis == "row" else self.col_slack

    def _set_slack(self, axis: Axis, slack: int) -> None:
        if axis == "row":
            self.row_slack = slack
        else:
            self.col_slack = slack

    def _cursor_wait(self, action: Action) -> int:
        return cursor_wait(self._cursor(action.axis), self._slack(action.axis), action.index)

    def _slide_distance(self, action: Action) -> int:
        values = line_values(self.board, action.axis, action.index)
        return pack_line(values, action.toward_start)[1]

    def _apply_slide(self, action: Action) -> int:
        values = line_values(self.board, action.axis, action.index)
        packed, distance = pack_line(values, action.toward_start)
        set_line(self.board, action.axis, action.index, packed)
        return distance * BARREL_FRAMES

    def _resolve(self, axis: Axis) -> None:
        for _ in range(MAX_CHAIN_WAVES):
            indices = matching_lines(self.board, axis)
            if not indices:
                return
            if len(indices) > 4:
                raise RuntimeError("5本以上が同時に揃いました")
            self.waves.append(len(indices))
            self.score += SCORE_BY_LINES[len(indices)]
            bundles = make_line_bundles(self.rng, active_colors(self.lines_cleared), len(indices))
            self.lines_cleared += len(indices)
            for index, bundle in zip(indices, bundles):
                set_line(self.board, axis, index, bundle)
            if self.time_left <= REFILL_FRAMES:
                self.time_left = 0
            else:
                self.time_left -= REFILL_FRAMES
            self._check_barrels()
            axis = _opposite(axis)
        raise RuntimeError("連鎖が上限を超えました")

    def _check_barrels(self) -> None:
        count = _barrel_count(self.board)
        if count != BARREL_COUNT:
            raise RuntimeError(f"タルが{count}個です")
