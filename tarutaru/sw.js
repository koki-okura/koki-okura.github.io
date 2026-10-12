"use strict";

// 置き場の名前と写すファイルは tarutaru.build_web が埋める。中身が変わると名前も変わり、古い置き場は消える。
const CACHE = "tarutaru-a9bfc939b5a7";
const FILES = [
  "./",
  "version.json",
  "app.js",
  "cnn.js",
  "cnn_small.json",
  "icon-180.png",
  "icon-512.png",
  "index.html",
  "manifest.webmanifest",
  "py/game.py",
  "py/rules.py",
  "py/web_coach.py",
  "py/web_session.py",
  "pyodide/pyodide-lock.json",
  "pyodide/pyodide.asm.mjs",
  "pyodide/pyodide.asm.wasm",
  "pyodide/pyodide.js",
  "pyodide/python_stdlib.zip",
  "style.css"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(FILES.map((file) => new Request(file, { cache: "reload" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("tarutaru-") && key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    caches.open(CACHE).then((cache) => cache.match(event.request, { ignoreSearch: true })).then((hit) => hit || fetch(event.request)),
  );
});
