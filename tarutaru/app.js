"use strict";

// ゲームの進行はすべて Python の tarutaru.web_session が持つ。ここは画面と保存だけ。
const RECORDS_KEY = "tarutaru-records";
const CURRENT_KEY = "tarutaru-current";
const PYTHON_FILES = ["rules.py", "game.py", "web_session.py"];
const PACKAGE_DIR = "/home/pyodide/tarutaru";
const FPS = 60;
const SIZE = 6;
const SWIPE_PX = 22;

let web = null;
let session = null;
let state = null;
let version = { code: "?", build: "?" };
let chosen = null;
let gesture = null;
let savedId = null;
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
    if (!state.done && state.moves > 0 && !confirm("今のゲームをやめて、新しいゲームを始めますか？")) return;
    startGame();
  });
  $("next-game").addEventListener("click", startGame);
  $("open-records").addEventListener("click", () => {
    renderRecords();
    $("records").classList.remove("hidden");
  });
  $("close-records").addEventListener("click", () => $("records").classList.add("hidden"));
  $("export").addEventListener("click", exportRecords);
  $("delete-exported").addEventListener("click", deleteExported);
}

function cellAt(event) {
  const rect = $("cells").getBoundingClientRect();
  const col = Math.floor(((event.clientX - rect.left) / rect.width) * SIZE);
  const row = Math.floor(((event.clientY - rect.top) / rect.height) * SIZE);
  if (row < 0 || row >= SIZE || col < 0 || col >= SIZE) return null;
  return { row, col };
}

function resumeOrStart() {
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
  setSession(web.Session(), null);
}

function persist() {
  const data = JSON.parse(session.record_json());
  data.undo_left = JSON.parse(session.state_json()).undo_left;
  data.saved_id = savedId;
  localStorage.setItem(CURRENT_KEY, JSON.stringify(data));
}

function slide(axis, index, towardStart) {
  if (state.done || !session.slide(axis, index, towardStart)) return;
  state = JSON.parse(session.state_json());
  if (state.done && savedId === null) saveFinished();
  persist();
  render();
}

function undo() {
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
  state = JSON.parse(session.state_json());
  $("score").textContent = state.score;
  $("lines").textContent = state.lines;
  $("time").textContent = seconds(state.time_left, 1);
  $("seed").textContent = `種 ${state.seed}`;
  if (chosen) {
    const row = seconds(session.wait_frames("row", chosen.row), 2);
    const col = seconds(session.wait_frames("col", chosen.col), 2);
    $("wait").textContent = `待ち 行 ${row}秒 列 ${col}秒`;
  } else {
    $("wait").textContent = "マスに触れると待ちを表示";
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

  $("undo").textContent = `戻る (${state.undo_left})`;
  $("undo").disabled = state.undo_left === 0 || state.done;
  $("finished-undo").disabled = state.undo_left === 0;
  const waiting = loadRecords().filter((record) => !record.exported).length;
  $("open-records").textContent = waiting ? `記録 (${waiting})` : "記録";

  $("finished").classList.toggle("hidden", !state.done);
  $("final-score").textContent = state.score;
  $("final-note").textContent = savedId !== null ? "スマホに保存しました" : "";
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

async function exportRecords() {
  const records = loadRecords();
  if (records.length === 0) return;
  const payload = {
    format: "tarutaru-human-records",
    code: version.code,
    build: version.build,
    exported_at: new Date().toISOString(),
    records: records.map(({ id, exported, ...record }) => record),
  };
  const name = `tarutaru_records_${stamp()}.json`;
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
  storeRecords(loadRecords().map((record) => (done.has(record.id) ? { ...record, exported: true } : record)));
  renderRecords();
  render();
}

function deleteExported() {
  const records = loadRecords();
  const exported = records.filter((record) => record.exported).length;
  if (exported === 0) return;
  if (!confirm(`書き出した記録 ${exported} 件をスマホから消します。PC に取り込んだことを確かめてから消してください。`)) return;
  storeRecords(records.filter((record) => !record.exported));
  renderRecords();
  render();
}

boot();
