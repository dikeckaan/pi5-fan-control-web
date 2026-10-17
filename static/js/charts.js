// Dependency-free canvas charts: the live history chart and the draggable fan curve editor.
(function () {
  "use strict";

  const FONT = "11px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function alpha(color, a) {
    // Works for #rrggbb tokens, which is all the stylesheet uses.
    const n = parseInt(color.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }

  function setup(canvas) {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(rect.width * dpr));
    const h = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.font = FONT;
    return { ctx, w: rect.width, h: rect.height };
  }

  function observe(canvas, draw) {
    new ResizeObserver(() => draw()).observe(canvas);
    document.addEventListener("themechange", () => draw());
  }

  // ------------------------------------------------------------------ history

  class HistoryChart {
    constructor(canvas, tip) {
      this.canvas = canvas;
      this.tip = tip;
      this.points = [];
      this.minutes = 15;
      this.emergency = null;
      this.hoverX = null;
      this.timeFormat = null;
      observe(canvas, () => this.draw());
      canvas.addEventListener("pointermove", (e) => {
        this.hoverX = e.clientX - canvas.getBoundingClientRect().left;
        this.draw();
      });
      canvas.addEventListener("pointerleave", () => {
        this.hoverX = null;
        this.draw();
      });
    }

    setData(points, minutes, emergency) {
      this.points = points;
      this.minutes = minutes;
      this.emergency = emergency;
      this.draw();
    }

    setLocale(locale) {
      this.timeFormat = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" });
      this.tipFormat = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      this.draw();
    }

    draw() {
      const { ctx, w, h } = setup(this.canvas);
      const pad = { l: 38, r: 46, t: 8, b: 22 };
      const W = w - pad.l - pad.r;
      const H = h - pad.t - pad.b;
      const now = Date.now() / 1000;
      const x0 = now - this.minutes * 60;
      const pts = this.points.filter((p) => p[0] >= x0);
      const muted = css("--muted");
      const grid = css("--grid");
      const tempColor = css("--temp-line");
      const rpmColor = css("--rpm-line");
      this.tip.hidden = true;

      if (W <= 0 || H <= 0) return;
      if (!pts.length) {
        ctx.fillStyle = muted;
        ctx.textAlign = "center";
        ctx.fillText(I18n.t("history.empty"), w / 2, h / 2);
        return;
      }

      const temps = pts.map((p) => p[1]).filter((v) => v !== null);
      let tMin = Math.floor((Math.min(35, ...temps) - 2) / 5) * 5;
      let tMax = Math.ceil((Math.max(60, ...temps) + 2) / 5) * 5;
      if (this.emergency && this.emergency <= tMax + 10) tMax = Math.max(tMax, this.emergency + 2);
      // Four grid steps of a whole multiple of 5 °C keep the axis labels round.
      tMax = tMin + Math.ceil((tMax - tMin) / 20) * 20;
      const rpms = pts.map((p) => p[3]).filter((v) => v !== null);
      const rMax = Math.max(1000, Math.ceil(Math.max(0, ...rpms) / 1000) * 1000);

      const X = (ts) => pad.l + ((ts - x0) / (now - x0)) * W;
      const YT = (v) => pad.t + H - ((v - tMin) / (tMax - tMin)) * H;
      const YR = (v) => pad.t + H - (v / rMax) * H;

      // Grid and axes
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.fillStyle = muted;
      const steps = 4;
      for (let i = 0; i <= steps; i++) {
        const y = Math.round(pad.t + (H * i) / steps) + 0.5;
        ctx.beginPath();
        ctx.moveTo(pad.l, y);
        ctx.lineTo(pad.l + W, y);
        ctx.stroke();
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        ctx.fillText(Math.round(tMax - ((tMax - tMin) * i) / steps) + "°", pad.l - 6, y);
        ctx.textAlign = "left";
        const r = rMax - (rMax * i) / steps;
        ctx.fillText(r >= 1000 ? (r / 1000).toFixed(r % 1000 ? 1 : 0) + "k" : String(r), pad.l + W + 6, y);
      }
      const tickEvery = this.minutes <= 5 ? 60 : this.minutes <= 15 ? 180 : 300;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let ts = Math.ceil(x0 / tickEvery) * tickEvery; ts <= now; ts += tickEvery) {
        const x = X(ts);
        if (x < pad.l + 14 || x > pad.l + W - 14) continue;
        ctx.fillText(this.timeFormat ? this.timeFormat.format(ts * 1000) : "", x, pad.t + H + 6);
      }

      // Emergency threshold
      if (this.emergency && this.emergency <= tMax) {
        const y = YT(this.emergency);
        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = alpha(css("--danger"), 0.7);
        ctx.beginPath();
        ctx.moveTo(pad.l, y);
        ctx.lineTo(pad.l + W, y);
        ctx.stroke();
        ctx.restore();
      }

      const series = (idx, Y, color, fill) => {
        // Break the line where samples are missing or the panel was not running.
        const segments = [];
        let seg = [];
        let prev = null;
        for (const p of pts) {
          if (p[idx] === null || (prev && p[0] - prev[0] > 15)) {
            if (seg.length) segments.push(seg);
            seg = [];
          }
          if (p[idx] !== null) seg.push([X(p[0]), Y(p[idx])]);
          prev = p;
        }
        if (seg.length) segments.push(seg);
        for (const s of segments) {
          if (fill) {
            const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + H);
            grad.addColorStop(0, alpha(color, 0.22));
            grad.addColorStop(1, alpha(color, 0));
            ctx.fillStyle = grad;
            ctx.beginPath();
            ctx.moveTo(s[0][0], pad.t + H);
            s.forEach(([x, y]) => ctx.lineTo(x, y));
            ctx.lineTo(s[s.length - 1][0], pad.t + H);
            ctx.closePath();
            ctx.fill();
          }
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.lineJoin = "round";
          ctx.beginPath();
          s.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
          ctx.stroke();
        }
      };
      series(3, YR, rpmColor, true);
      series(1, YT, tempColor, true);

      // Hover crosshair and tooltip
      if (this.hoverX !== null && this.hoverX >= pad.l && this.hoverX <= pad.l + W) {
        let best = pts[0];
        for (const p of pts) if (Math.abs(X(p[0]) - this.hoverX) < Math.abs(X(best[0]) - this.hoverX)) best = p;
        const x = X(best[0]);
        ctx.strokeStyle = alpha(muted, 0.5);
        ctx.beginPath();
        ctx.moveTo(x, pad.t);
        ctx.lineTo(x, pad.t + H);
        ctx.stroke();
        const dot = (y, color) => {
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(x, y, 4, 0, Math.PI * 2);
          ctx.fill();
        };
        if (best[3] !== null) dot(YR(best[3]), rpmColor);
        if (best[1] !== null) dot(YT(best[1]), tempColor);
        const parts = [this.tipFormat ? this.tipFormat.format(best[0] * 1000) : ""];
        if (best[1] !== null) parts.push(best[1].toFixed(1) + " °C");
        if (best[3] !== null) parts.push(best[3] + " " + I18n.t("status.rpm"));
        if (best[2] !== null) parts.push(I18n.t("status.level") + " " + best[2]);
        this.tip.textContent = parts.join(" · ");
        this.tip.style.left = Math.min(Math.max(x, 90), w - 90) + "px";
        this.tip.style.top = pad.t + 4 + "px";
        this.tip.hidden = false;
      }
    }
  }

  // ------------------------------------------------------------------ curve editor

  class CurveEditor {
    constructor(canvas, onChange) {
      this.canvas = canvas;
      this.onChange = onChange;
      this.points = [];
      this.maxLevel = 4;
      this.domain = [20, 95];
      this.emergency = null;
      this.now = null;
      this.hover = null;
      this.selected = null;
      this.drag = null;
      this.pad = { l: 34, r: 14, t: 22, b: 26 };
      observe(canvas, () => this.draw());

      canvas.addEventListener("pointerdown", (e) => this.pointerDown(e));
      canvas.addEventListener("pointermove", (e) => this.pointerMove(e));
      canvas.addEventListener("pointerup", (e) => this.pointerUp(e));
      canvas.addEventListener("pointercancel", (e) => this.pointerUp(e));
      canvas.addEventListener("pointerleave", () => {
        if (!this.drag) {
          this.hover = null;
          this.draw();
        }
      });
      canvas.addEventListener("dblclick", (e) => this.doubleClick(e));
      canvas.addEventListener("keydown", (e) => this.keyDown(e));
    }

    setCurve(curve, maxLevel) {
      this.maxLevel = maxLevel;
      this.points = Object.entries(curve)
        .map(([t, l]) => ({ t: Number(t), l: Number(l) }))
        .sort((a, b) => a.t - b.t);
      this.selected = null;
      this.draw();
    }

    getCurve() {
      const out = {};
      for (const p of this.points) out[String(p.t)] = p.l;
      return out;
    }

    setMarkers(now, emergency) {
      if (now === this.now && emergency === this.emergency) return;
      this.now = now;
      this.emergency = emergency;
      if (!this.drag) this.draw();
    }

    // geometry
    size() {
      const r = this.canvas.getBoundingClientRect();
      return { w: r.width, h: r.height, W: r.width - this.pad.l - this.pad.r, H: r.height - this.pad.t - this.pad.b };
    }
    x(t) {
      const { W } = this.size();
      const [d0, d1] = this.domain;
      return this.pad.l + ((Math.min(Math.max(t, d0), d1) - d0) / (d1 - d0)) * W;
    }
    y(l) {
      const { H } = this.size();
      return this.pad.t + H - (l / this.maxLevel) * H;
    }
    tAt(px) {
      const { W } = this.size();
      const [d0, d1] = this.domain;
      return Math.round(d0 + ((px - this.pad.l) / W) * (d1 - d0));
    }
    lAt(py) {
      const { H } = this.size();
      return Math.round(((this.pad.t + H - py) / H) * this.maxLevel);
    }
    local(e) {
      const r = this.canvas.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    }
    hit(px, py) {
      let best = null;
      let bestD = 16;
      this.points.forEach((p, i) => {
        const d = Math.hypot(this.x(p.t) - px, this.y(p.l) - py);
        if (d < bestD) {
          best = i;
          bestD = d;
        }
      });
      return best;
    }

    // Keep the curve valid while editing: strictly increasing temperatures and
    // levels that never drop as the temperature rises.
    constrain(i, t, l) {
      const prev = this.points[i - 1];
      const next = this.points[i + 1];
      const tMin = prev ? prev.t + 1 : 0;
      const tMax = next ? next.t - 1 : 100;
      const lMin = prev ? prev.l : 0;
      const lMax = next ? next.l : this.maxLevel;
      return {
        t: Math.min(Math.max(t, tMin), tMax),
        l: Math.min(Math.max(l, lMin), lMax),
      };
    }

    levelAt(t) {
      let level = 0;
      for (const p of this.points) if (t >= p.t) level = p.l;
      return level;
    }

    changed() {
      this.draw();
      this.onChange(this.getCurve());
    }

    // interaction
    pointerDown(e) {
      const [px, py] = this.local(e);
      const i = this.hit(px, py);
      this.selected = i;
      if (i !== null) {
        this.drag = { i, origT: this.points[i].t };
        this.canvas.setPointerCapture(e.pointerId);
        e.preventDefault();
      }
      this.draw();
    }

    pointerMove(e) {
      const [px, py] = this.local(e);
      if (this.drag) {
        const { i, origT } = this.drag;
        let t = this.tAt(px);
        // A point below the visible range (usually 0 °C) stays put when dragged along the left edge.
        if (origT < this.domain[0] && px <= this.x(this.domain[0]) + 6) t = origT;
        const c = this.constrain(i, t, this.lAt(py));
        const p = this.points[i];
        if (c.t !== p.t || c.l !== p.l) {
          p.t = c.t;
          p.l = c.l;
          this.changed();
        }
        return;
      }
      const hover = this.hit(px, py);
      if (hover !== this.hover) {
        this.hover = hover;
        this.canvas.style.cursor = hover === null ? "crosshair" : "grab";
        this.draw();
      }
    }

    pointerUp(e) {
      if (!this.drag) return;
      this.drag = null;
      if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
      this.draw();
    }

    doubleClick(e) {
      const [px, py] = this.local(e);
      const i = this.hit(px, py);
      if (i !== null) {
        this.remove(i);
        return;
      }
      this.add(this.tAt(px), this.lAt(py));
    }

    add(t, l) {
      t = Math.min(Math.max(t, 0), 100);
      if (this.points.length >= 16 || this.points.some((p) => p.t === t)) return false;
      const prev = [...this.points].reverse().find((p) => p.t < t);
      const next = this.points.find((p) => p.t > t);
      l = Math.min(Math.max(l, prev ? prev.l : 0), next ? next.l : this.maxLevel);
      this.points.push({ t, l });
      this.points.sort((a, b) => a.t - b.t);
      this.selected = this.points.findIndex((p) => p.t === t);
      this.changed();
      return true;
    }

    remove(i) {
      if (this.points.length <= 1) return;
      this.points.splice(i, 1);
      this.selected = null;
      this.hover = null;
      this.changed();
    }

    keyDown(e) {
      const i = this.selected;
      if (i === null || i === undefined) return;
      const p = this.points[i];
      const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
      if (moves[e.key]) {
        const c = this.constrain(i, p.t + moves[e.key][0], p.l + moves[e.key][1]);
        p.t = c.t;
        p.l = c.l;
        this.changed();
        e.preventDefault();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        this.remove(i);
        e.preventDefault();
      }
    }

    // rendering
    draw() {
      const { ctx, w, h } = setup(this.canvas);
      const { W, H } = this.size();
      if (W <= 0 || H <= 0) return;
      const [d0, d1] = this.domain;
      const muted = css("--muted");
      const grid = css("--grid");
      const primary = css("--primary");
      const danger = css("--danger");
      const surface = css("--surface");
      const pl = this.pad.l;
      const pt = this.pad.t;

      // Emergency zone
      if (this.emergency !== null && this.emergency < d1) {
        const x = this.x(this.emergency);
        ctx.fillStyle = alpha(danger, 0.08);
        ctx.fillRect(x, pt, pl + W - x, H);
        ctx.save();
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = alpha(danger, 0.7);
        ctx.beginPath();
        ctx.moveTo(x, pt);
        ctx.lineTo(x, pt + H);
        ctx.stroke();
        ctx.restore();
        ctx.fillStyle = danger;
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        ctx.fillText(I18n.t("curve.emergency"), x + 5, pt + 4);
      }

      // Grid
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.fillStyle = muted;
      ctx.textBaseline = "middle";
      ctx.textAlign = "right";
      for (let l = 0; l <= this.maxLevel; l++) {
        const y = Math.round(this.y(l)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(pl, y);
        ctx.lineTo(pl + W, y);
        ctx.stroke();
        ctx.fillText(String(l), pl - 8, y);
      }
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let t = Math.ceil(d0 / 10) * 10; t <= d1; t += 10) {
        const x = Math.round(this.x(t)) + 0.5;
        ctx.beginPath();
        ctx.moveTo(x, pt);
        ctx.lineTo(x, pt + H);
        ctx.stroke();
        ctx.fillText(t + "°", x, pt + H + 7);
      }

      // Step curve
      const path = new Path2D();
      path.moveTo(pl, this.y(this.levelAt(d0)));
      for (const p of this.points) {
        if (p.t <= d0) continue;
        path.lineTo(this.x(p.t), this.y(this.levelAt(p.t - 1)));
        path.lineTo(this.x(p.t), this.y(p.l));
      }
      path.lineTo(pl + W, this.y(this.levelAt(d1)));
      const area = new Path2D(path);
      area.lineTo(pl + W, pt + H);
      area.lineTo(pl, pt + H);
      area.closePath();
      const grad = ctx.createLinearGradient(0, pt, 0, pt + H);
      grad.addColorStop(0, alpha(primary, 0.28));
      grad.addColorStop(1, alpha(primary, 0.03));
      ctx.fillStyle = grad;
      ctx.fill(area);
      ctx.strokeStyle = primary;
      ctx.lineWidth = 2.5;
      ctx.lineJoin = "round";
      ctx.stroke(path);

      // Current temperature
      if (this.now !== null && this.now >= d0 && this.now <= d1) {
        const x = this.x(this.now);
        ctx.strokeStyle = alpha(css("--temp-line"), 0.85);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(x, pt);
        ctx.lineTo(x, pt + H);
        ctx.stroke();
        ctx.fillStyle = css("--temp-line");
        ctx.beginPath();
        ctx.arc(x, this.y(this.levelAt(this.now)), 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.fillText(`${I18n.t("curve.now")} ${this.now.toFixed(1)}°`, Math.min(Math.max(x, pl + 30), pl + W - 30), pt - 4);
      }

      // Points
      this.points.forEach((p, i) => {
        const x = this.x(p.t);
        const y = this.y(p.l);
        const active = i === this.hover || i === this.selected || (this.drag && this.drag.i === i);
        ctx.beginPath();
        ctx.arc(x, y, active ? 8 : 6.5, 0, Math.PI * 2);
        ctx.fillStyle = active ? primary : surface;
        ctx.fill();
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = primary;
        ctx.stroke();
        if (active) {
          const label = `${p.t}°C · ${p.l}`;
          const tw = ctx.measureText(label).width + 12;
          const lx = Math.min(Math.max(x - tw / 2, 2), w - tw - 2);
          const ly = y - 32 < 2 ? y + 14 : y - 32;
          ctx.fillStyle = css("--text");
          ctx.beginPath();
          ctx.roundRect(lx, ly, tw, 20, 6);
          ctx.fill();
          ctx.fillStyle = surface;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(label, lx + tw / 2, ly + 10);
        }
      });
    }
  }

  window.Charts = { HistoryChart, CurveEditor };
})();
