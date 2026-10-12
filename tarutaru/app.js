"use strict";

// ゲームの進行はすべて Python の tarutaru.web_session が持つ。ここは画面と保存だけ。
const RECORDS_KEY = "tarutaru-records";
const REPAIRS_KEY = "tarutaru-repairs";
const CURRENT_KEY = "tarutaru-current";
const COACH_KEY = "tarutaru-coach";
const PYTHON_FILES = ["rules.py", "game.py", "web_session.py", "web_coach.py"];
const PACKAGE_DIR = "/home/pyodide/tarutaru";
const FPS = 60;
const SIZE = 6;
const SWIPE_PX = 22;

let web = null;
let coachMod = null;
let weights = null;
let session = null;
let coach = null;
let mode = "record";
let state = null;
let version = { code: "?", build: "?" };
let chosen = null;
let gesture = null;
let savedId = null;
let notice = "";
let advancing = false;
const cellViews = [];

const $ = (id) => document.getElementById(id);

function loadRecords() {
  try {
    return JSON.parse(localStorage.getItem(RECORDS_KEY)) || [];
  } catch {
    return [];
  }
}

function storeRecords(records) {
  localStorage.setItem(RECORDS_KEY, JSON.stringify(records));
}

function loadRepairs() {
  try {
    return JSON.parse(localStorage.getItem(REPAIRS_KEY)) || [];
  } catch {
    return [];
  }
}

function storeRepairs(records) {
  localStorage.setItem(REPAIRS_KEY, JSON.stringify(records));
}

async function fetchText(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.text();
}

async function boot() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch((error) => console.warn(error));
  }
  try {
    version = JSON.parse(await fetchText("version.json"));
    const pyodide = await loadPyodide();
    pyodide.FS.mkdirTree(PACKAGE_DIR);
    // GitHub Pages は __init__.py を配信しないので、ここで作る。
    pyodide.FS.writeFile(`${PACKAGE_DIR}/__init__.py`, "");
    for (const name of PYTHON_FILES) {
      pyodide.FS.writeFile(`${PACKAGE_DIR}/${name}`, await fetchText(`py/${name}`));
    }
    pyodide.runPython("import sys\nif '/home/pyodide' not in sys.path:\n    sys.path.insert(0, '/home/pyodide')");
    web = pyodide.pyimport("tarutaru.web_session");
    coachMod = pyodide.pyimport("tarutaru.web_coach");
    weights = JSON.parse(await fetchText("cnn_small.json"));
  } catch (error) {
    $("loading-text").textContent = `読み込めませんでした: ${error.message || error}`;
    return;
  }
  buildBoard();
  bindControls();
  resumeOrStart();
  $("loading").classList.add("hidden");
}

function buildBoard() {
  const cells = $("cells");
  for (let row = 0; row < SIZE; row++) {
    const line = [];
    for (let col = 0; col < SIZE; col++) {
      const cell = document.createElement("div");
      const barrel = document.createElement("div");
      cell.appendChild(barrel);
      cells.appendChild(cell);
      line.push({ cell, barrel });
    }
    cellViews.push(line);
  }
  for (const [id, glyph] of [["col-marks", "▼"], ["row-marks", "▶"]]) {
    for (let index = 0; index < SIZE; index++) {
      const mark = document.createElement("div");
      mark.className = "mark";
      mark.textContent = glyph;
      $(id).appendChild(mark);
    }
  }
}

function bindControls() {
  const cells = $("cells");
  cells.addEventListener("pointerdown", (event) => {
    const cell = cellAt(event);
    if (!cell) return;
    try {
      cells.setPointerCapture(event.pointerId);
    } catch {}
    gesture = { id: event.pointerId, x: event.clientX, y: event.clientY, ...cell, used: false };
    chosen = cell;
    render();
  });
  cells.addEventListener("pointermove", (event) => {
    if (!gesture || gesture.used || event.pointerId !== gesture.id) return;
    const dx = event.clientX - gesture.x;
    const dy = event.clientY - gesture.y;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < SWIPE_PX) return;
    gesture.used = true;
    if (Math.abs(dx) >= Math.abs(dy)) {
      slide("row", gesture.row, dx < 0);
    } else {
      slide("col", gesture.col, dy < 0);
    }
  });
  for (const type of ["pointerup", "pointercancel"]) {
    cells.addEventListener(type, (event) => {
      if (gesture && event.pointerId === gesture.id) gesture = null;
    });
  }
  document.addEventListener("gesturestart", (event) => event.preventDefault());
  document.addEventListener("dblclick", (event) => event.preventDefault());

  $("undo").addEventListener("click", undo);
  $("finished-undo").addEventListener("click", undo);
  $("new-game").addEventListener("click", () => {
    if (mode === "record" && state && !state.done && state.moves > 0 && !confirm("今のゲームをやめて、新しいゲームを始めますか？")) return;
    startGame();
  });
  $("next-game").addEventListener("click", startGame);
  $("coach").addEventListener("click", startCoach);
  $("record-mode").addEventListener("click", () => {
    if (mode !== "coach") return;
    if (!confirm("交代をやめて、記録用モードに戻りますか？")) return;
    leaveCoach();
  });
  $("skip").addEventListener("click", skipCoach);
  $("coach-next").addEventListener("click", nextCoachSeed);
  $("open-records").addEventListener("click", () => {
    renderRecords();
    $("records").classList.remove("hidden");
  });
  $("close-records").addEventListener("click", () => $("records").classList.add("hidden"));
  $("export").addEventListener("click", () => exportStored(loadRecords, storeRecords, "tarutaru-human-records", "tarutaru_records"));
  $("delete-exported").addEventListener("click", () => deleteExported(loadRecords, storeRecords, "完成局"));
  $("export-repairs").addEventListener("click", () => exportStored(loadRepairs, storeRepairs, "tarutaru-repair-records", "tarutaru_repairs"));
  $("delete-repairs").addEventListener("click", () => deleteExported(loadRepairs, storeRepairs, "交代の記録"));
}

function cellAt(event) {
  const rect = $("cells").getBoundingClientRect();
  const col = Math.floor(((event.clientX - rect.left) / rect.width) * SIZE);
  const row = Math.floor(((event.clientY - rect.top) / rect.height) * SIZE);
  if (row < 0 || row >= SIZE || col < 0 || col >= SIZE) return null;
  return { row, col };
}

function resumeOrStart() {
  const coachText = localStorage.getItem(COACH_KEY);
  if (coachText) {
    try {
      enterCoach(coachMod.resume_json(coachText));
      return;
    } catch (error) {
      console.warn(error);
      localStorage.removeItem(COACH_KEY);
    }
  }
  const text = localStorage.getItem(CURRENT_KEY);
  if (text) {
    try {
      setSession(web.resume_json(text), JSON.parse(text).saved_id ?? null);
      return;
    } catch (error) {
      console.warn(error);
    }
  }
  startGame();
}

function setSession(next, saved) {
  if (session) session.destroy();
  session = next;
  savedId = saved;
  chosen = null;
  persist();
  render();
}

function startGame() {
  localStorage.removeItem(COACH_KEY);
  mode = "record";
  releaseCoach();
  setSession(web.Session(), null);
}

function startCoach() {
  if (mode === "record" && state && !state.done && state.moves > 0 && !confirm("今のゲームをやめて、方策と交代しますか？")) return;
  if (session) {
    session.destroy();
    session = null;
  }
  localStorage.removeItem(CURRENT_KEY);
  enterCoach(coachMod.CoachSession());
}

function leaveCoach() {
  releaseCoach();
  localStorage.removeItem(COACH_KEY);
  startGame();
}

function releaseCoach() {
  if (coach) coach.destroy();
  coach = null;
  mode = "record";
}

function enterCoach(next) {
  releaseCoach();
  mode = "coach";
  coach = next;
  chosen = null;
  persist();
  render();
  advancePolicy();
}

function nextCoachSeed() {
  if (mode !== "coach" || !coach) return;
  coach.next_game();
  persist();
  render();
  advancePolicy();
}

function skipCoach() {
  if (mode !== "coach" || !coach || advancing || !coach.skip()) return;
  persist();
  render();
  advancePolicy();
}

function sleep() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

async function advancePolicy() {
  if (mode !== "coach" || !coach || advancing) return;
  advancing = true;
  $("policy").classList.remove("hidden");
  try {
    while (mode === "coach" && coach) {
      const job = JSON.parse(coach.begin_policy_move_json());
      if (job.kind !== "score") break;
      const scores = TarutaruCnn.scoreBatch(job.planes, job.lines, job.count, weights);
      coach.finish_policy_move_json(JSON.stringify(scores));
      render();
      await sleep();
    }
  } catch (error) {
    notice = `方策が止まりました: ${error.message || error}`;
  } finally {
    advancing = false;
    if (mode === "coach") $("policy").classList.add("hidden");
    persist();
    render();
  }
}

function persist() {
  if (mode === "coach" && coach) {
    localStorage.setItem(COACH_KEY, coach.snapshot_json());
    return;
  }
  if (!session) return;
  const data = JSON.parse(session.record_json());
  data.undo_left = JSON.parse(session.state_json()).undo_left;
  data.saved_id = savedId;
  localStorage.setItem(CURRENT_KEY, JSON.stringify(data));
}

function slide(axis, index, towardStart) {
  if (mode === "coach") {
    if (!coach || advancing) return;
    const result = JSON.parse(coach.slide_json(axis, index, towardStart));
    if (result.repair) storeRepair(result.repair);
    if (result.status === "rejected") flash("その本数は消せません");
    persist();
    render();
    if (result.state.phase === "policy") advancePolicy();
    return;
  }
  if (state.done || !session.slide(axis, index, towardStart)) return;
  state = JSON.parse(session.state_json());
  if (state.done && savedId === null) saveFinished();
  persist();
  render();
}

function storeRepair(repair) {
  repair.code = version.code;
  repair.finished_at = new Date().toISOString();
  repair.id = `${repair.seed}-${Date.now()}`;
  repair.exported = false;
  const records = loadRepairs();
  records.push(repair);
  storeRepairs(records);
}

function flash(text) {
  notice = text;
  render();
  setTimeout(() => {
    if (notice === text) {
      notice = "";
      render();
    }
  }, 1200);
}

function undo() {
  if (mode === "coach") {
    if (!coach || advancing || !coach.undo()) return;
    persist();
    render();
    return;
  }
  if (!session.undo()) return;
  if (savedId !== null) {
    storeRecords(loadRecords().filter((record) => record.id !== savedId));
    savedId = null;
  }
  persist();
  render();
}

function saveFinished() {
  const record = JSON.parse(session.record_json());
  record.code = version.code;
  record.finished_at = new Date().toISOString();
  record.id = `${record.seed}-${Date.now()}`;
  record.exported = false;
  const records = loadRecords();
  records.push(record);
  storeRecords(records);
  savedId = record.id;
}

function seconds(frames, digits) {
  return (frames / FPS).toFixed(digits);
}

function render() {
  const coaching = mode === "coach" && coach;
  state = JSON.parse(coaching ? coach.state_json() : session.state_json());
  $("score").textContent = state.score;
  $("lines").textContent = state.lines;
  $("time").textContent = seconds(state.time_left, 1);
  $("seed").textContent = `種 ${state.seed}`;
  const player = coaching ? coach : session;
  if (chosen && player) {
    const row = seconds(player.wait_frames("row", chosen.row), 2);
    const col = seconds(player.wait_frames("col", chosen.col), 2);
    $("wait").textContent = `待ち 行 ${row}秒 列 ${col}秒`;
  } else {
    $("wait").textContent = "マスに触れると待ちを表示";
  }
  if (coaching && state.phase === "human" && state.allowed) {
    $("hint").textContent = notice || `${state.allowed}本を消す`;
  } else if (coaching && state.phase === "finished") {
    $("hint").textContent = state.reason || "終了";
  } else {
    $("hint").textContent = notice || "行は横に、列は縦にスワイプして寄せる";
  }
  for (let row = 0; row < SIZE; row++) {
    for (let col = 0; col < SIZE; col++) {
      const { cell, barrel } = cellViews[row][col];
      const marks = ["cell"];
      if (chosen && (chosen.row === row || chosen.col === col)) marks.push("chosen");
      if (state.row_cursor === row) marks.push("cursor-row");
      if (state.col_cursor === col) marks.push("cursor-col");
      cell.className = marks.join(" ");
      const color = state.board[row][col];
      barrel.className = color ? `barrel c${color}` : "";
    }
  }
  [...$("col-marks").children].forEach((mark, index) => mark.classList.toggle("on", index === state.col_cursor));
  [...$("row-marks").children].forEach((mark, index) => mark.classList.toggle("on", index === state.row_cursor));

  const finished = coaching ? state.phase === "finished" : state.done;
  $("undo").textContent = `戻る (${state.undo_left})`;
  $("undo").disabled = advancing || state.undo_left === 0 || finished;
  $("finished-undo").disabled = state.undo_left === 0;
  $("skip").classList.toggle("hidden", !coaching);
  $("skip").disabled = advancing || !state.can_skip;
  $("new-game").classList.toggle("hidden", coaching);
  $("coach").classList.toggle("hidden", coaching);
  $("record-mode").classList.toggle("hidden", !coaching);
  const waiting = loadRecords().filter((record) => !record.exported).length;
  const repairWaiting = loadRepairs().filter((record) => !record.exported).length;
  const waitingTotal = waiting + repairWaiting;
  $("open-records").textContent = waitingTotal ? `記録 (${waitingTotal})` : "記録";

  $("finished").classList.toggle("hidden", !finished);
  $("finished-title").textContent = coaching && state.reason === "流れから外れました" ? "流れから外れました" : "終了";
  $("final-score").textContent = state.score;
  $("final-note").textContent = coaching ? "" : savedId !== null ? "スマホに保存しました" : "";
  $("finished-undo").classList.toggle("hidden", coaching);
  $("next-game").classList.toggle("hidden", coaching);
  $("coach-next").classList.toggle("hidden", !coaching);
  if (coaching && advancing) {
    $("policy-text").textContent = `方策が進めています\n最後の得点から ${state.since_score}/${state.policy_moves} 手`;
  }
}

function renderRecords() {
  const records = loadRecords();
  const repairs = loadRepairs();
  const waiting = records.filter((record) => !record.exported).length;
  const repairWaiting = repairs.filter((record) => !record.exported).length;
  $("records-summary").textContent =
    `完成局 ${records.length} 件（未書き出し ${waiting} 件）\n交代の記録 ${repairs.length} 件（未書き出し ${repairWaiting} 件）`;
  $("export").disabled = records.length === 0;
  $("delete-exported").disabled = records.length === waiting;
  $("export-repairs").disabled = repairs.length === 0;
  $("delete-repairs").disabled = repairs.length === repairWaiting;
  $("version").textContent = `ゲームのコード ${version.code} ・ 組み立て ${version.build}`;
}

function renderRecords() {
  const records = loadRecords();
  const waiting = records.filter((record) => !record.exported).length;
  $("records-summary").textContent = `スマホにある記録 ${records.length} 件\nまだ書き出していない記録 ${waiting} 件`;
  $("export").disabled = records.length === 0;
  $("delete-exported").disabled = records.length === waiting;
  $("version").textContent = `ゲームのコード ${version.code} ・ 組み立て ${version.build}`;
}

function stamp() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

async function exportStored(load, store, format, stem) {
  const records = load();
  if (records.length === 0) return;
  const payload = {
    format,
    code: version.code,
    build: version.build,
    exported_at: new Date().toISOString(),
    records: records.map(({ id, exported, ...record }) => record),
  };
  const name = `${stem}_${stamp()}.json`;
  const file = new File([JSON.stringify(payload)], name, { type: "application/json" });
  try {
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file] });
    } else {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(file);
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    }
  } catch (error) {
    if (error.name !== "AbortError") alert(`書き出せませんでした: ${error.message}`);
    return;
  }
  const done = new Set(records.map((record) => record.id));
  store(load().map((record) => (done.has(record.id) ? { ...record, exported: true } : record)));
  renderRecords();
  render();
}

function deleteExported(load, store, label) {
  const records = load();
  const exported = records.filter((record) => record.exported).length;
  if (exported === 0) return;
  if (!confirm(`書き出した${label} ${exported} 件をスマホから消します。PC に取り込んだことを確かめてから消してください。`)) return;
  store(records.filter((record) => !record.exported));
  renderRecords();
  render();
}

boot();
