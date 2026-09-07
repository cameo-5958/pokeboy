/* Pokeboy web player shell.
 *
 * Drives the app's bundled emulator runtime (embed.html) in a same-origin
 * srcdoc iframe, playing the role the React Native host plays on the phone:
 * it injects window.PokeboyRuntime + a ReactNativeWebView shim before the
 * embed's script runs, then talks the embed's string message protocol —
 * input/settings/paused in, ready/error/save/save-request/state-* out.
 * Battery saves and save states persist server-side behind the web session.
 */
(() => {
  "use strict";

  const BTN = { a: 0x01, b: 0x02, select: 0x04, start: 0x08 };
  const PAD = { right: 0x01, left: 0x02, up: 0x04, down: 0x08 };
  const GAME_ACTIONS = ["up", "down", "left", "right", "a", "b", "start", "select"];

  const DEFAULT_BINDS = {
    up: "ArrowUp",
    down: "ArrowDown",
    left: "ArrowLeft",
    right: "ArrowRight",
    a: "KeyX",
    b: "KeyZ",
    start: "Enter",
    select: "ShiftRight",
    fastForward: "Space",
    pause: "KeyP",
  };
  const ACTION_LABELS = {
    up: "D-PAD UP",
    down: "D-PAD DOWN",
    left: "D-PAD LEFT",
    right: "D-PAD RIGHT",
    a: "A",
    b: "B",
    start: "START",
    select: "SELECT",
    fastForward: "FAST-FORWARD (HOLD)",
    pause: "PAUSE",
  };
  const BINDS_KEY = "pokeboy.web.keybinds.v1";
  const CART_KEY = "pokeboy.web.cart";
  const AUDIO_KEY = "pokeboy.web.audio.v1";
  const SLOT_COUNT = 3;

  const $ = (id) => document.getElementById(id);
  const frame = $("emu");
  const overlay = $("overlay");

  let binds = loadJson(BINDS_KEY, { ...DEFAULT_BINDS });
  let audio = loadJson(AUDIO_KEY, { volume: 0.6, muted: false });
  let codeToAction = {};
  let cart = null;
  let embedReady = false;
  let userPaused = false;
  let fastForward = false;
  let lastBatteryData = null;
  let capture = null; // { action } while waiting for a rebind keypress
  const held = new Set();
  const pending = new Map(); // nonce -> { resolve, reject, timer, doneType }
  const staleConfirm = new Set(); // slots whose older-core warning was shown

  function loadJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      if (raw) return { ...fallback, ...JSON.parse(raw) };
    } catch {}
    return fallback;
  }
  function saveJson(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {}
  }
  function rebuildCodeMap() {
    codeToAction = {};
    for (const [action, code] of Object.entries(binds)) if (code) codeToAction[code] = action;
  }
  rebuildCodeMap();

  // ---- embed messaging ----------------------------------------------------

  function send(msg) {
    frame.contentWindow?.postMessage(JSON.stringify(msg), "*");
  }
  function sendSettings() {
    send({ type: "settings", speed: fastForward ? 4 : 1, muted: audio.muted, volume: audio.volume });
  }
  function masks() {
    let buttons = 0;
    let dpad = 0;
    for (const action of held) {
      if (BTN[action]) buttons |= BTN[action];
      if (PAD[action]) dpad |= PAD[action];
    }
    if ((dpad & 0x03) === 0x03) dpad &= ~0x03; // opposing left+right: drop both
    if ((dpad & 0x0c) === 0x0c) dpad &= ~0x0c; // opposing up+down: drop both
    return { buttons, dpad };
  }
  function sendInput() {
    if (embedReady) send({ type: "input", ...masks() });
  }

  function embedRequest(type, extra, doneType, timeoutMs) {
    return new Promise((resolve, reject) => {
      const nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
      const timer = setTimeout(() => {
        pending.delete(nonce);
        reject(new Error("EMULATOR TIMEOUT"));
      }, timeoutMs);
      pending.set(nonce, { resolve, reject, timer, doneType });
      send({ type, nonce, ...extra });
    });
  }

  window.addEventListener("message", (event) => {
    if (event.source !== frame.contentWindow || typeof event.data !== "string") return;
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    const detail = msg.detail;
    switch (msg.type) {
      case "ready":
        embedReady = true;
        overlay.hidden = true;
        sendSettings();
        sendInput();
        if (userPaused) send({ type: "paused", value: true });
        refreshStates();
        break;
      case "error":
        overlay.hidden = false;
        overlay.textContent = `EMULATOR ERROR\n${detail?.message ?? "unknown"}\n(${detail?.stage ?? "?"})`;
        break;
      case "save-request":
        answerSaveRequest(detail);
        break;
      case "save":
        mirrorBattery(detail);
        break;
      case "state-data":
      case "state-loaded":
      case "state-error": {
        const entry = detail && pending.get(detail.nonce);
        if (!entry) break;
        pending.delete(detail.nonce);
        clearTimeout(entry.timer);
        if (msg.type === "state-error") entry.reject(new Error(detail.message || "STATE ERROR"));
        else if (msg.type === entry.doneType) entry.resolve(detail);
        break;
      }
      default:
        break; // telemetry, cache-error, rom-source, … — uninteresting here
    }
  });

  // ---- battery saves ------------------------------------------------------

  async function answerSaveRequest(detail) {
    if (!detail || !cart || detail.id !== cart.id) return;
    let payload = { ts: 0, data: null };
    try {
      const res = await fetch(`/web/battery/${encodeURIComponent(cart.id)}`);
      if (res.ok) payload = await res.json();
    } catch {}
    lastBatteryData = payload.data ?? null;
    send({ type: "save-data", id: detail.id, nonce: detail.nonce, ts: payload.ts || 0, data: payload.data ?? null });
  }

  async function mirrorBattery(detail) {
    if (!detail || !cart || detail.id !== cart.id) return;
    if (typeof detail.data !== "string" || detail.data === lastBatteryData) return;
    try {
      const res = await fetch(`/web/battery/${encodeURIComponent(cart.id)}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ts: detail.ts || Date.now(), data: detail.data }),
      });
      if (res.ok) lastBatteryData = detail.data;
    } catch {}
  }

  // ---- input --------------------------------------------------------------

  function clusterButtons(action) {
    return document.querySelectorAll(`#cluster [data-act="${action}"]`);
  }
  function pressAction(action) {
    if (action === "fastForward") {
      if (!fastForward) {
        fastForward = true;
        sendSettings();
        updateTransport();
      }
      return;
    }
    if (action === "pause") {
      togglePause();
      return;
    }
    if (!held.has(action)) {
      held.add(action);
      for (const el of clusterButtons(action)) el.classList.add("pressed");
      sendInput();
    }
  }
  function releaseAction(action) {
    if (action === "fastForward") {
      if (fastForward) {
        fastForward = false;
        sendSettings();
        updateTransport();
      }
      return;
    }
    if (action === "pause") return;
    if (held.delete(action)) {
      for (const el of clusterButtons(action)) el.classList.remove("pressed");
      sendInput();
    }
  }
  function releaseEverything() {
    fastForward = false;
    for (const action of [...held]) releaseAction(action);
    sendSettings();
    updateTransport();
  }
  function isTyping(target) {
    return target instanceof Element && target.closest("input, select, textarea") !== null;
  }

  document.addEventListener("keydown", (event) => {
    if (capture) {
      event.preventDefault();
      finishCapture(event);
      return;
    }
    if (event.code === "Escape") {
      event.preventDefault();
      toggleDrawer();
      return;
    }
    if (isTyping(event.target)) return;
    const action = codeToAction[event.code];
    if (!action) return;
    event.preventDefault();
    if (event.repeat) return;
    pressAction(action);
  });
  document.addEventListener("keyup", (event) => {
    const action = codeToAction[event.code];
    if (action) releaseAction(action);
  });
  window.addEventListener("blur", releaseEverything);

  for (const btn of document.querySelectorAll("#cluster [data-act]")) {
    const action = btn.dataset.act;
    btn.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      btn.setPointerCapture(event.pointerId);
      pressAction(action);
    });
    btn.addEventListener("pointerup", () => releaseAction(action));
    btn.addEventListener("pointercancel", () => releaseAction(action));
    btn.addEventListener("contextmenu", (event) => event.preventDefault());
  }

  // First user gesture: resend settings so the embed can unlock its
  // AudioContext (same-origin iframes share the parent's user activation).
  const unlockAudio = () => sendSettings();
  document.addEventListener("pointerdown", unlockAudio, { once: true });
  document.addEventListener("keydown", unlockAudio, { once: true });

  // ---- transport ----------------------------------------------------------

  function togglePause() {
    userPaused = !userPaused;
    send({ type: "paused", value: userPaused });
    updateTransport();
  }
  function updateTransport() {
    $("pause").textContent = userPaused ? "▶" : "⏸";
    const badge = $("speed-badge");
    badge.textContent = fastForward ? "4× FF" : userPaused ? "∥" : "1×";
    badge.classList.toggle("fast", fastForward);
    $("mute").classList.toggle("muted", audio.muted);
    $("volume").value = String(Math.round(audio.volume * 100));
  }
  $("pause").addEventListener("click", (event) => {
    togglePause();
    event.currentTarget.blur();
  });
  $("volume").addEventListener("input", (event) => {
    audio.volume = Number(event.target.value) / 100;
    saveJson(AUDIO_KEY, audio);
    sendSettings();
  });
  $("mute").addEventListener("click", (event) => {
    audio.muted = !audio.muted;
    saveJson(AUDIO_KEY, audio);
    sendSettings();
    updateTransport();
    event.currentTarget.blur();
  });
  $("logout").addEventListener("click", async () => {
    try {
      await fetch("/web/logout", { method: "POST" });
    } catch {}
    location.href = "/web/login.html";
  });

  // ---- drawer + tabs ------------------------------------------------------

  function toggleDrawer() {
    document.body.classList.toggle("drawer-open");
    setTimeout(fitStage, 140); // after the drawer's 120ms width transition
  }
  $("drawer-toggle").addEventListener("click", (event) => {
    toggleDrawer();
    event.currentTarget.blur();
  });
  for (const tabBtn of document.querySelectorAll("#tabs button")) {
    tabBtn.addEventListener("click", () => {
      for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("active", b === tabBtn);
      for (const tab of document.querySelectorAll(".tab"))
        tab.classList.toggle("active", tab.id === `tab-${tabBtn.dataset.tab}`);
      tabBtn.blur();
    });
  }

  // ---- save states --------------------------------------------------------

  function slotStatus(slot, text, isError) {
    const el = document.querySelector(`.slot[data-slot="${slot}"] .status`);
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("error", Boolean(isError));
  }

  async function refreshStates() {
    const wrap = $("tab-states");
    if (!cart) {
      wrap.textContent = "";
      return;
    }
    let slots = [];
    try {
      const res = await fetch(`/web/states/${encodeURIComponent(cart.id)}`);
      if (res.ok) slots = await res.json();
    } catch {}
    const bySlot = new Map(slots.map((s) => [s.slot, s]));
    wrap.textContent = "";
    for (let n = 1; n <= SLOT_COUNT; n++) {
      const record = bySlot.get(n);
      const div = document.createElement("div");
      div.className = "slot";
      div.dataset.slot = String(n);
      const meta = record
        ? `${new Date(record.ts).toLocaleString()} · ${Math.max(1, Math.round(record.size / 1024))} KB`
        : "EMPTY";
      div.innerHTML = `
        <h3>SLOT ${n}</h3>
        <p class="meta">${meta}</p>
        <div class="row">
          <button data-op="save">SAVE</button>
          <button data-op="load" ${record ? "" : "disabled"}>LOAD</button>
          <button data-op="del" ${record ? "" : "disabled"}>DEL</button>
        </div>
        <p class="status"></p>`;
      div.querySelector('[data-op="save"]').addEventListener("click", () => saveSlot(n));
      div.querySelector('[data-op="load"]').addEventListener("click", () => loadSlot(n));
      div.querySelector('[data-op="del"]').addEventListener("click", () => deleteSlot(n));
      wrap.appendChild(div);
    }
  }

  async function saveSlot(slot) {
    if (!embedReady) return;
    staleConfirm.delete(slot);
    slotStatus(slot, "SNAPSHOTTING…");
    try {
      const data = await embedRequest("state-save", {}, "state-data", 30000);
      slotStatus(slot, "UPLOADING…");
      const res = await fetch(`/web/states/${encodeURIComponent(cart.id)}/${slot}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          base64: data.base64,
          gz: data.gz,
          heapLen: data.heapLen,
          cartridgeId: data.cartridgeId,
          cartridgeVersion: data.cartridgeVersion,
          mods: data.mods,
        }),
      });
      if (!res.ok) throw new Error("UPLOAD FAILED");
      await refreshStates();
      slotStatus(slot, "SAVED");
    } catch (error) {
      slotStatus(slot, String(error.message || error), true);
    }
  }

  async function loadSlot(slot) {
    if (!embedReady) return;
    slotStatus(slot, "FETCHING…");
    try {
      const [metaRes, recordRes] = await Promise.all([
        fetch("/web/meta"),
        fetch(`/web/states/${encodeURIComponent(cart.id)}/${slot}`),
      ]);
      if (!recordRes.ok) throw new Error("EMPTY SLOT");
      const meta = metaRes.ok ? await metaRes.json() : { coreHash: null };
      const record = await recordRes.json();
      if (meta.coreHash && record.coreHash !== meta.coreHash && !staleConfirm.has(slot)) {
        staleConfirm.add(slot);
        throw new Error("STATE FROM AN OLDER CORE — LOAD AGAIN TO FORCE");
      }
      slotStatus(slot, "RESTORING…");
      await embedRequest(
        "state-load",
        {
          base64: record.base64,
          gz: record.gz,
          heapLen: record.heapLen,
          cartridgeId: record.cartridgeId,
          cartridgeVersion: record.cartridgeVersion,
          mods: record.mods,
        },
        "state-loaded",
        30000,
      );
      staleConfirm.delete(slot);
      slotStatus(slot, "LOADED");
    } catch (error) {
      slotStatus(slot, String(error.message || error), true);
    }
  }

  async function deleteSlot(slot) {
    staleConfirm.delete(slot);
    try {
      await fetch(`/web/states/${encodeURIComponent(cart.id)}/${slot}`, { method: "DELETE" });
    } catch {}
    refreshStates();
  }

  // ---- keybind editor -----------------------------------------------------

  function prettyCode(code) {
    if (!code) return "—";
    const arrows = { ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→" };
    if (arrows[code]) return arrows[code];
    return code
      .replace(/^Key/, "")
      .replace(/^Digit/, "")
      .replace(/^(Shift|Control|Alt|Meta)(Left|Right)$/, "$2-$1")
      .toUpperCase();
  }

  function renderKeys() {
    const wrap = $("tab-keys");
    wrap.textContent = "";
    for (const action of Object.keys(DEFAULT_BINDS)) {
      const row = document.createElement("div");
      row.className = "bind-row";
      const label = document.createElement("span");
      label.textContent = ACTION_LABELS[action];
      const btn = document.createElement("button");
      btn.className = "bind chrome";
      btn.textContent = prettyCode(binds[action]);
      btn.addEventListener("click", () => {
        capture = { action };
        renderKeys();
        const active = wrap.querySelector(`[data-action="${action}"]`);
        if (active) {
          active.textContent = "PRESS A KEY…";
          active.classList.add("capturing");
        }
      });
      btn.dataset.action = action;
      row.append(label, btn);
      wrap.appendChild(row);
    }
    const reset = document.createElement("button");
    reset.className = "reset chrome";
    reset.textContent = "RESET DEFAULTS";
    reset.addEventListener("click", () => {
      binds = { ...DEFAULT_BINDS };
      saveJson(BINDS_KEY, binds);
      rebuildCodeMap();
      renderKeys();
    });
    wrap.appendChild(reset);
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "ESC toggles this drawer. Binding a key that is already in use steals it from the old action.";
    wrap.appendChild(hint);
  }

  function finishCapture(event) {
    const { action } = capture;
    capture = null;
    if (event.code !== "Escape") {
      for (const [other, code] of Object.entries(binds)) {
        if (code === event.code && other !== action) binds[other] = null;
      }
      binds[action] = event.code;
      saveJson(BINDS_KEY, binds);
      rebuildCodeMap();
    }
    renderKeys();
  }

  // ---- stage sizing -------------------------------------------------------

  function fitStage() {
    const wrap = $("stage-wrap");
    const stage = $("stage");
    const availableW = wrap.clientWidth - 28;
    const availableH = wrap.clientHeight - 190; // transport + cluster + gaps
    const k = Math.max(1, Math.min(Math.floor(availableW / 160), Math.floor(availableH / 144)));
    stage.style.width = `${160 * k + 8}px`; // + border
    stage.style.height = `${144 * k + 8}px`;
  }
  window.addEventListener("resize", fitStage);

  // ---- boot ---------------------------------------------------------------

  async function boot() {
    overlay.hidden = false;
    overlay.textContent = "CONNECTING…";
    const session = await fetch("/web/session");
    if (session.status === 401) {
      location.href = "/web/login.html";
      return;
    }

    let carts = [];
    try {
      const res = await fetch("/api/cartridges");
      if (!res.ok) throw new Error(`cartridges ${res.status}`);
      carts = await res.json();
    } catch (error) {
      overlay.textContent = `BACKEND UNREACHABLE\n${error.message || error}`;
      return;
    }
    if (!carts.length) {
      overlay.textContent = "NO CARTRIDGES INSTALLED";
      return;
    }

    const select = $("cart-select");
    select.textContent = "";
    for (const c of carts) {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.title;
      select.appendChild(opt);
    }
    const wanted = localStorage.getItem(CART_KEY);
    cart = carts.find((c) => c.id === wanted) ?? carts[0];
    select.value = cart.id;
    select.addEventListener("change", () => {
      localStorage.setItem(CART_KEY, select.value);
      location.reload();
    });
    document.title = `POKEBOY · ${cart.title}`;

    overlay.textContent = "INSERTING CARTRIDGE…";
    const embedRes = await fetch("/web/emulator/embed.html");
    if (!embedRes.ok) {
      overlay.textContent = "EMULATOR RUNTIME MISSING";
      return;
    }
    const embedHtml = await embedRes.text();
    const runtime = {
      gbcoreUri: `${location.origin}/web/emulator/gbcore.bin`,
      modCoreUri: `${location.origin}/web/emulator/mod-core.bin`,
      wasmUri: `${location.origin}/web/emulator/gbcore.wasm`,
      backendUrl: location.origin,
      cartridgeId: cart.id,
      cartridgeVersion: cart.version,
    };
    const prelude =
      `<script>window.PokeboyRuntime=${JSON.stringify(runtime)};` +
      `window.ReactNativeWebView={postMessage:function(m){parent.postMessage(m,'*')}};</script>`;
    // srcdoc documents inherit the parent's origin and base URL, so the
    // embed's same-origin fetches ride the session cookie automatically.
    frame.srcdoc = embedHtml.includes("<head>")
      ? embedHtml.replace("<head>", `<head>${prelude}`)
      : prelude + embedHtml;

    fitStage();
    renderKeys();
    refreshStates();
    updateTransport();
  }

  boot().catch((error) => {
    overlay.hidden = false;
    overlay.textContent = `SHELL ERROR\n${error.message || error}`;
  });
})();
