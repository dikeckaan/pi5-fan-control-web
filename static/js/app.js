(function () {
  "use strict";
  const { t } = I18n;
  const $ = (id) => document.getElementById(id);

  const STATUS_EVERY = 1500;
  const HISTORY_EVERY = 2000;
  const RANGE_KEY = "pifan.range";
  const CRITICAL_EVENTS = new Set(["emergency_on", "sensor_failure", "fan_stall"]);
  const WARNING_EVENTS = new Set(["manual_override_on", "boost_started"]);
  const TONES = { emergency: "danger", sensor_failsafe: "danger", manual_override: "warn", boost: "accent", spinup: "accent" };
  // Gauge colour stops: cool blue, healthy green, warm amber, hot red.
  const GAUGE_STOPS = [[30, [14, 165, 233]], [50, [34, 197, 94]], [65, [245, 158, 11]], [80, [239, 68, 68]]];
  const GAUGE_MIN = 20;
  const GAUGE_MAX = 90;

  const state = {
    status: null,
    config: null,
    defaults: null,
    maxLevel: 4,
    history: [],
    lastTs: 0,
    curveDirty: false,
    safetyDirty: false,
    boostTotal: 0,
    minutes: 15,
  };

  // ------------------------------------------------------------------ api

  class ApiError extends Error {
    constructor(code, params) {
      super(code);
      this.code = code;
      this.params = params || {};
    }
  }

  async function api(method, url, body) {
    let res;
    try {
      res = await fetch(url, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
        credentials: "same-origin",
      });
    } catch (e) {
      throw new ApiError("network");
    }
    if (res.status === 401) {
      location.href = "login";
      throw new ApiError("unauthorized");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(data.error || "network", data.params);
    return data;
  }

  function errorText(err) {
    const params = { ...(err.params || {}) };
    if (params.field) {
      const label = t("safety." + params.field);
      if (label !== "safety." + params.field) params.field = label;
    }
    const key = "errors." + (err.code || "network");
    const msg = t(key, params);
    return msg === key ? String(err.message || err) : msg;
  }

  function toast(message, isError) {
    const el = document.createElement("div");
    el.className = "toast" + (isError ? " error" : "");
    el.textContent = message;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), isError ? 5000 : 2500);
  }

  async function run(fn, okMessage) {
    try {
      const out = await fn();
      if (okMessage) toast(okMessage);
      return out;
    } catch (err) {
      toast(t("toast.error", { msg: errorText(err) }), true);
      return null;
    }
  }

  // ------------------------------------------------------------------ status

  function gaugeColor(temp) {
    if (temp <= GAUGE_STOPS[0][0]) return GAUGE_STOPS[0][1];
    for (let i = 1; i < GAUGE_STOPS.length; i++) {
      const [t1, c1] = GAUGE_STOPS[i];
      const [t0, c0] = GAUGE_STOPS[i - 1];
      if (temp <= t1) {
        const f = (temp - t0) / (t1 - t0);
        return c0.map((v, k) => Math.round(v + (c1[k] - v) * f));
      }
    }
    return GAUGE_STOPS[GAUGE_STOPS.length - 1][1];
  }

  function gaugePoint(temp, r) {
    const f = Math.min(Math.max((temp - GAUGE_MIN) / (GAUGE_MAX - GAUGE_MIN), 0), 1);
    const a = ((135 + 270 * f) * Math.PI) / 180;
    return [100 + r * Math.cos(a), 100 + r * Math.sin(a)];
  }

  function renderGauge(temp, emergency) {
    const arc = $("gauge-arc");
    if (temp === null) {
      $("temp").textContent = "--";
      arc.style.strokeDasharray = "0 100";
      return;
    }
    $("temp").textContent = temp.toFixed(1);
    const f = Math.min(Math.max((temp - GAUGE_MIN) / (GAUGE_MAX - GAUGE_MIN), 0), 1);
    arc.style.strokeDasharray = `${(f * 100).toFixed(2)} 100`;
    arc.style.stroke = `rgb(${gaugeColor(temp).join(",")})`;
    if (emergency) {
      const [x1, y1] = gaugePoint(emergency, 74);
      const [x2, y2] = gaugePoint(emergency, 98);
      const mark = $("gauge-emergency");
      mark.setAttribute("x1", x1);
      mark.setAttribute("y1", y1);
      mark.setAttribute("x2", x2);
      mark.setAttribute("y2", y2);
    }
  }

  function renderLevelBars(level, max) {
    const bars = $("level-bars");
    if (bars.childElementCount !== max) {
      bars.innerHTML = "";
      for (let i = 1; i <= max; i++) {
        const b = document.createElement("i");
        b.style.height = 25 + (75 * i) / max + "%";
        bars.appendChild(b);
      }
    }
    [...bars.children].forEach((b, i) => b.classList.toggle("on", level !== null && i < level));
  }

  function renderLevelPicker(max) {
    const picker = $("level-picker");
    if (picker.childElementCount === max + 1) return;
    picker.innerHTML = "";
    for (let l = 0; l <= max; l++) {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.dataset.level = l;
      b.innerHTML = `<span>${l}</span><small></small>`;
      b.addEventListener("click", () => setManualLevel(l));
      picker.appendChild(b);
    }
    labelLevelPicker();
  }

  function labelLevelPicker() {
    const buttons = $("level-picker").children;
    const max = buttons.length - 1;
    [...buttons].forEach((b, l) => {
      b.querySelector("small").textContent = l === 0 ? t("control.off") : l === max ? t("control.max") : `${Math.round((100 * l) / max)}%`;
    });
  }

  function renderAlarms(alarms) {
    const box = $("alarms");
    box.innerHTML = "";
    for (const a of alarms) {
      const el = document.createElement("div");
      el.className = "banner banner-" + a.severity;
      el.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-alert"/></svg><span></span>';
      el.querySelector("span").textContent = t("alarm." + a.key, a.params);
      box.appendChild(el);
    }
  }

  function renderEvents(events) {
    const list = $("events");
    list.innerHTML = "";
    if (!events.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = t("events.empty");
      list.appendChild(li);
      return;
    }
    const fmt = new Intl.DateTimeFormat(I18n.lang, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    for (const ev of events) {
      const li = document.createElement("li");
      if (CRITICAL_EVENTS.has(ev.key)) li.className = "ev-critical";
      else if (WARNING_EVENTS.has(ev.key)) li.className = "ev-warning";
      const time = document.createElement("time");
      time.dateTime = new Date(ev.time * 1000).toISOString();
      time.textContent = fmt.format(ev.time * 1000);
      const params = { ...ev.params };
      if (params.mode) params.mode = t("mode." + params.mode);
      const text = document.createElement("span");
      text.textContent = t("event." + ev.key, params);
      li.append(time, text);
      list.appendChild(li);
    }
  }

  function formatUptime(s) {
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
  }

  function renderStatus() {
    const s = state.status;
    if (!s) return;
    const emergency = state.config ? state.config.safety.emergency_temp : null;
    renderGauge(s.temperature, emergency);

    $("rpm").textContent = s.rpm === null ? t("status.unavailable") : s.rpm.toLocaleString(I18n.lang);
    const spin = $("fan-spin");
    spin.classList.toggle("on", !!s.rpm);
    if (s.rpm) spin.style.setProperty("--spin-speed", Math.max(0.25, 1800 / s.rpm).toFixed(2) + "s");
    $("level").textContent = s.level === null ? "--" : `${s.level}/${s.max_level}`;
    renderLevelBars(s.level, s.max_level);

    const pill = $("reason-pill");
    pill.dataset.tone = TONES[s.reason] || "ok";
    $("reason-text").textContent = t("reason." + s.reason);

    document.querySelectorAll("#mode-switch button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === s.mode)));
    renderLevelPicker(s.max_level);
    [...$("level-picker").children].forEach((b) => b.setAttribute("aria-checked", String(Number(b.dataset.level) === s.manual_level)));
    $("level-picker").classList.toggle("disabled", s.mode !== "manual");

    const boosting = s.boost_remaining > 0;
    if (boosting) state.boostTotal = Math.max(state.boostTotal, s.boost_remaining);
    else state.boostTotal = 0;
    $("boost-progress").hidden = !boosting;
    $("boost-cancel").hidden = !boosting;
    if (boosting) {
      $("boost-bar").style.width = ((100 * s.boost_remaining) / state.boostTotal).toFixed(1) + "%";
      $("boost-left").textContent = t("boost.remaining", { s: Math.ceil(s.boost_remaining) });
    }

    renderAlarms(s.alarms);
    renderEvents(s.events);
    $("uptime").textContent = formatUptime(s.uptime);
    curve.setMarkers(s.temperature, emergency);
  }

  async function pollStatus() {
    try {
      state.status = await api("GET", "api/status");
      $("offline").hidden = true;
      renderStatus();
    } catch (err) {
      if (err.code === "network") $("offline").hidden = false;
    }
    setTimeout(pollStatus, STATUS_EVERY);
  }

  // ------------------------------------------------------------------ history

  const history = new Charts.HistoryChart($("history-chart"), $("history-tip"));

  function drawHistory() {
    history.setData(state.history, state.minutes, state.config ? state.config.safety.emergency_temp : null);
  }

  async function pollHistory() {
    try {
      const { points } = await api("GET", "api/history?since=" + state.lastTs);
      if (points.length) {
        state.history.push(...points);
        state.lastTs = points[points.length - 1][0];
        const cutoff = Date.now() / 1000 - 31 * 60;
        while (state.history.length && state.history[0][0] < cutoff) state.history.shift();
      }
      drawHistory();
    } catch (err) {
      /* the status poll shows the offline banner */
    }
    setTimeout(pollHistory, HISTORY_EVERY);
  }

  function setRange(minutes) {
    state.minutes = minutes;
    try { localStorage.setItem(RANGE_KEY, String(minutes)); } catch (e) { /* private mode */ }
    document.querySelectorAll("#history-range button").forEach((b) => b.classList.toggle("active", Number(b.dataset.min) === minutes));
    drawHistory();
  }

  document.querySelectorAll("#history-range button").forEach((b) => b.addEventListener("click", () => setRange(Number(b.dataset.min))));

  // ------------------------------------------------------------------ control

  async function setMode(mode) {
    await run(() => api("POST", "api/mode", { mode }));
    pollOnce();
  }

  async function setManualLevel(level) {
    await run(async () => {
      if (state.status && state.status.mode !== "manual") await api("POST", "api/mode", { mode: "manual" });
      await api("POST", "api/manual-level", { level });
    });
    pollOnce();
  }

  async function pollOnce() {
    try {
      state.status = await api("GET", "api/status");
      renderStatus();
    } catch (e) { /* next poll retries */ }
  }

  document.querySelectorAll("#mode-switch button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

  document.querySelectorAll("#boost-presets .chip").forEach((c) => c.addEventListener("click", () => {
    $("boost-seconds").value = c.dataset.s;
    startBoost();
  }));
  $("boost-btn").addEventListener("click", startBoost);
  $("boost-cancel").addEventListener("click", async () => {
    await run(() => api("DELETE", "api/boost", {}));
    pollOnce();
  });

  async function startBoost() {
    const seconds = parseInt($("boost-seconds").value, 10);
    const ok = await run(() => api("POST", "api/boost", { seconds }));
    if (ok) state.boostTotal = seconds;
    pollOnce();
  }

  // ------------------------------------------------------------------ curve

  const curve = new Charts.CurveEditor($("curve-chart"), () => {
    setCurveDirty(true);
    renderCurveTable();
  });

  function setCurveDirty(dirty) {
    state.curveDirty = dirty;
    $("curve-dirty").hidden = !dirty;
  }

  function renderCurveTable() {
    const tbody = document.querySelector("#curve-table tbody");
    tbody.innerHTML = "";
    curve.points.forEach((p, i) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><input class="input" type="number" min="0" max="100" step="1" data-k="t"></td>
        <td><input class="input" type="number" min="0" max="${state.maxLevel}" step="1" data-k="l"></td>
        <td><button type="button" class="row-del"><svg viewBox="0 0 24 24" aria-hidden="true"><use href="#i-trash"/></svg></button></td>`;
      const [tIn, lIn] = tr.querySelectorAll("input");
      tIn.value = p.t;
      lIn.value = p.l;
      tIn.setAttribute("aria-label", t("curve.temp"));
      lIn.setAttribute("aria-label", t("curve.level"));
      const del = tr.querySelector(".row-del");
      del.title = t("curve.remove");
      del.setAttribute("aria-label", t("curve.remove"));
      del.disabled = curve.points.length <= 1;
      del.addEventListener("click", () => curve.remove(i));
      const commit = () => {
        const nt = parseInt(tIn.value, 10);
        const nl = parseInt(lIn.value, 10);
        if (Number.isNaN(nt) || Number.isNaN(nl)) {
          renderCurveTable();
          return;
        }
        // Re-insert so the constraints are checked against the new neighbours.
        const old = curve.points.splice(i, 1)[0];
        if (!curve.add(Math.min(Math.max(nt, 0), 100), Math.min(Math.max(nl, 0), state.maxLevel))) {
          curve.points.splice(i, 0, old);
          curve.points.sort((a, b) => a.t - b.t);
          curve.draw();
          renderCurveTable();
        }
      };
      tIn.addEventListener("change", commit);
      lIn.addEventListener("change", commit);
      tbody.appendChild(tr);
    });
  }

  function loadCurve(c) {
    curve.setCurve(c, state.maxLevel);
    setCurveDirty(false);
    renderCurveTable();
  }

  $("curve-add").addEventListener("click", () => {
    const last = curve.points[curve.points.length - 1];
    const tNew = last ? Math.min(last.t + 5, 100) : 50;
    if (!curve.add(tNew, last ? last.l : 0)) {
      for (let tt = 40; tt <= 100; tt++) if (curve.add(tt, curve.levelAt(tt))) break;
    }
  });
  $("curve-revert").addEventListener("click", () => loadCurve(state.config.auto_thresholds));
  $("curve-defaults").addEventListener("click", () => {
    curve.setCurve(state.defaults.auto_thresholds, state.maxLevel);
    setCurveDirty(true);
    renderCurveTable();
  });
  $("curve-save").addEventListener("click", async () => {
    const out = await run(() => api("PATCH", "api/config", { auto_thresholds: curve.getCurve() }), t("toast.saved"));
    if (out) {
      state.config = out.config;
      loadCurve(out.config.auto_thresholds);
    }
  });

  // ------------------------------------------------------------------ safety

  const safetyForm = $("safety-form");

  function fillSafety(safety) {
    for (const [k, v] of Object.entries(safety)) {
      const input = safetyForm.elements[k];
      if (!input) continue;
      if (input.type === "checkbox") input.checked = v;
      else input.value = v;
      input.classList.remove("invalid");
    }
    state.safetyDirty = false;
  }

  safetyForm.addEventListener("input", () => { state.safetyDirty = true; });
  safetyForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const safety = {};
    let valid = true;
    for (const input of safetyForm.querySelectorAll("input")) {
      if (input.type === "checkbox") {
        safety[input.name] = input.checked;
        continue;
      }
      const v = Number(input.value);
      const bad = input.value === "" || !Number.isInteger(v) || v < Number(input.min) || v > Number(input.max);
      input.classList.toggle("invalid", bad);
      valid = valid && !bad;
      safety[input.name] = v;
    }
    if (!valid) {
      toast(t("toast.error", { msg: t("errors.invalid_json") }), true);
      return;
    }
    const out = await run(() => api("PATCH", "api/config", { safety }), t("toast.saved"));
    if (out) {
      state.config = out.config;
      fillSafety(out.config.safety);
      renderStatus();
      drawHistory();
    }
  });

  // ------------------------------------------------------------------ backup

  $("import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const out = await run(async () => {
      let cfg;
      try {
        cfg = JSON.parse(await file.text());
      } catch (err) {
        throw new ApiError("invalid_json");
      }
      return api("POST", "api/config/import", cfg);
    }, t("toast.imported"));
    if (out) applyConfig(out.config);
  });

  // ------------------------------------------------------------------ boot

  function applyConfig(cfg) {
    state.config = cfg;
    if (!state.curveDirty) loadCurve(cfg.auto_thresholds);
    if (!state.safetyDirty) fillSafety(cfg.safety);
    renderStatus();
    drawHistory();
  }

  async function loadConfig() {
    const data = await api("GET", "api/config");
    state.maxLevel = data.max_level;
    state.defaults = data.defaults;
    applyConfig(data.config);
  }

  function relabel() {
    document.querySelectorAll("#history-range button").forEach((b) => { b.textContent = t("history.minutes", { n: b.dataset.min }); });
    history.setLocale(I18n.lang);
    if ($("level-picker").childElementCount) labelLevelPicker();
    renderCurveTable();
    renderStatus();
    curve.draw();
  }

  $("theme-btn").addEventListener("click", () => Theme.toggle());
  const logout = $("logout-btn");
  if (logout) {
    logout.addEventListener("click", async () => {
      await api("POST", "api/logout", {}).catch(() => {});
      location.href = "login";
    });
  }

  I18n.bindSelect($("lang-select"));
  I18n.onChange(relabel);

  (async function boot() {
    let saved = 15;
    try { saved = Number(localStorage.getItem(RANGE_KEY)) || 15; } catch (e) { /* private mode */ }
    await I18n.init();
    setRange([5, 15, 30].includes(saved) ? saved : 15);
    try {
      await loadConfig();
    } catch (err) {
      toast(t("toast.error", { msg: errorText(err) }), true);
    }
    pollStatus();
    pollHistory();
    // Pick up changes made from another browser tab or device.
    setInterval(() => api("GET", "api/config").then((d) => applyConfig(d.config)).catch(() => {}), 15000);
  })();
})();
