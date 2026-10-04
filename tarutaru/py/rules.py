"""タルタルの数値と、機械側の進行で固定する決め事。

画面で人間が見る開始演出と、補充中に新しいタルが見える演出は、この計算には入れない。
ゲーム開始時点でタルは配置済みで、制限時間はその時点から進む。
次の手は、補充と連鎖が終わった盤面に対して選ぶ。補充そのものの130フレームは時計に足す。
カーソルは、その軸で最後に寄せた時点から、寄せや補充の最中も次の行き先へ動いていたとみなす。
"""

SIZE = 6
BARREL_COUNT = 24
EMPTY_COUNT = SIZE * SIZE - BARREL_COUNT

TIME_SECONDS = 180
FPS = 60
TIME_FRAMES = TIME_SECONDS * FPS

CURSOR_FRAMES = 8
BARREL_FRAMES = 8
REFILL_FRAMES = 130
# カーソルが端から端まで動くフレーム。これより長く空いていても、それ以上は先に動けない。
MAX_CURSOR_SLACK = (SIZE - 1) * CURSOR_FRAMES
# 人間が画面で見る開始演出だけに使う。機械の進行はここを待たない。
INTRO_FRAMES = 120

YELLOW_AFTER = 8
GREEN_AFTER = 16

SCORE_BY_LINES = {1: 1, 2: 4, 3: 8, 4: 16}

EMPTY = 0
RED = 1
BLUE = 2
YELLOW = 3
GREEN = 4

INITIAL_COLORS = (RED, BLUE)
ALL_COLORS = (RED, BLUE, YELLOW, GREEN)

GLYPH = {
    EMPTY: ".",
    RED: "R",
    BLUE: "B",
    YELLOW: "Y",
    GREEN: "G",
}
