/* face.js — SVG robot avatar: eyes, pupils, eyebrows, mouth.
 *
 * A tiny tween engine animates between per-phase expression targets, with idle
 * micro-behaviours (blink, pupil drift) so it feels alive, and a talking mouth
 * gated by the `speaking` phase. No dependencies, no framework.
 *
 * Public API:
 *   const face = new RobotFace(svgEl);
 *   face.setPhase("thinking" | "listening" | "speaking" | "error" | "inactive");
 *   face.pulseMouth();   // call on each tts_audio chunk for a talking twitch
 */

const VIEW = { w: 600, h: 420 };

// Per-phase expression targets. Values are tweened toward.
const EXPR = {
  inactive: { lid: 0.5, browY: 2, browTilt: 0, pupil: [0, 2], mouthOpen: 0.05, mouthCurve: 0.18, glow: 0.3 },
  listening:{ lid: 0.06, browY: -4, browTilt: 0, pupil: [0, 0], mouthOpen: 0.1, mouthCurve: 0.3, glow: 0.75 },
  thinking: { lid: 0.12, browY: -10, browTilt: 0.5, pupil: [7, -6], mouthOpen: 0.07, mouthCurve: 0.08, glow: 0.6 },
  speaking: { lid: 0.04, browY: -2, browTilt: 0, pupil: [0, 0], mouthOpen: 0.42, mouthCurve: 0.2, glow: 1.0 },
  error:    { lid: 0.28, browY: 8, browTilt: -0.9, pupil: [0, 3], mouthOpen: 0.16, mouthCurve: -0.5, glow: 0.5 },
};

const lerp = (a, b, t) => a + (b - a) * t;

class RobotFace {
  constructor(svg) {
    this.svg = svg;
    svg.setAttribute("viewBox", `0 0 ${VIEW.w} ${VIEW.h}`);
    this._build();
    // current + target state
    this.s = { ...EXPR.inactive, pupil: [...EXPR.inactive.pupil] };
    this.t = { ...EXPR.inactive, pupil: [...EXPR.inactive.pupil] };
    this.phase = "inactive";
    this.blink = 0;          // 0..1 transient blink amount
    this.talk = 0;           // talking mouth oscillation amplitude
    this.drift = [0, 0];     // idle pupil drift
    this._nextBlink = performance.now() + 2000;
    this._nextDrift = performance.now() + 1500;
    this._mouthPulse = 0;
    requestAnimationFrame((ts) => this._loop(ts));
  }

  setPhase(phase) {
    if (!EXPR[phase]) return;
    this.phase = phase;
    const e = EXPR[phase];
    this.t = { ...e, pupil: [...e.pupil] };
  }

  pulseMouth() { this._mouthPulse = 1; }

  // -- build SVG nodes -------------------------------------------------
  _build() {
    const NS = "http://www.w3.org/2000/svg";
    const mk = (tag, attrs) => {
      const n = document.createElementNS(NS, tag);
      for (const k in attrs) n.setAttribute(k, attrs[k]);
      return n;
    };
    const defs = mk("defs", {});
    defs.innerHTML =
      `<filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
         <feGaussianBlur stdDeviation="6" result="b"/>
         <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
       </filter>`;
    this.svg.appendChild(defs);

    const EY = 170, EX = 150, ER = 78;            // eye geometry
    this.eyeGeom = { EY, EX, ER };
    // eye sockets (dark) + iris (glow)
    this.lEye = mk("circle", { cx: VIEW.w/2 - EX, cy: EY, r: ER, fill: "#0a1428" });
    this.rEye = mk("circle", { cx: VIEW.w/2 + EX, cy: EY, r: ER, fill: "#0a1428" });
    this.lIris = mk("circle", { cx: VIEW.w/2 - EX, cy: EY, r: 40, fill: "var(--face)", filter: "url(#glow)" });
    this.rIris = mk("circle", { cx: VIEW.w/2 + EX, cy: EY, r: 40, fill: "var(--face)", filter: "url(#glow)" });
    this.lPup = mk("circle", { cx: VIEW.w/2 - EX, cy: EY, r: 16, fill: "#040a18" });
    this.rPup = mk("circle", { cx: VIEW.w/2 + EX, cy: EY, r: 16, fill: "#040a18" });
    // eyelids (rects that slide down to blink/half-close)
    this.lLid = mk("rect", { x: VIEW.w/2 - EX - ER, y: EY - ER, width: 2*ER, height: 2*ER, fill: "#0b1020" });
    this.rLid = mk("rect", { x: VIEW.w/2 + EX - ER, y: EY - ER, width: 2*ER, height: 2*ER, fill: "#0b1020" });
    // eyebrows (lines)
    this.lBrow = mk("line", { x1: VIEW.w/2 - EX - 55, y1: EY - 95, x2: VIEW.w/2 - EX + 55, y2: EY - 95,
      stroke: "var(--face)", "stroke-width": 12, "stroke-linecap": "round", filter: "url(#glow)" });
    this.rBrow = mk("line", { x1: VIEW.w/2 + EX - 55, y1: EY - 95, x2: VIEW.w/2 + EX + 55, y2: EY - 95,
      stroke: "var(--face)", "stroke-width": 12, "stroke-linecap": "round", filter: "url(#glow)" });
    // mouth (path)
    this.mouth = mk("path", { d: "", fill: "none", stroke: "var(--face)", "stroke-width": 12,
      "stroke-linecap": "round", "stroke-linejoin": "round", filter: "url(#glow)" });

    [this.lEye, this.rEye, this.lIris, this.rIris, this.lPup, this.rPup,
     this.lLid, this.rLid, this.lBrow, this.rBrow, this.mouth].forEach(n => this.svg.appendChild(n));

    // clip lids to their eye circles so they look like eyelids
    const clipL = mk("clipPath", { id: "clipL" }); clipL.appendChild(this.lEye.cloneNode());
    const clipR = mk("clipPath", { id: "clipR" }); clipR.appendChild(this.rEye.cloneNode());
    defs.appendChild(clipL); defs.appendChild(clipR);
    this.lLid.setAttribute("clip-path", "url(#clipL)");
    this.rLid.setAttribute("clip-path", "url(#clipR)");
  }

  // -- per-frame update ------------------------------------------------
  _loop(ts) {
    const k = 0.12; // tween speed
    // ease scalar fields toward target
    for (const key of ["lid", "browY", "browTilt", "mouthOpen", "mouthCurve", "glow"]) {
      this.s[key] = lerp(this.s[key], this.t[key], k);
    }
    this.s.pupil[0] = lerp(this.s.pupil[0], this.t.pupil[0] + this.drift[0], k);
    this.s.pupil[1] = lerp(this.s.pupil[1], this.t.pupil[1] + this.drift[1], k);

    // idle behaviours
    if (ts > this._nextBlink) { this.blink = 1; this._nextBlink = ts + 2200 + Math.random()*3500; }
    this.blink = Math.max(0, this.blink - 0.14);
    if (ts > this._nextDrift) {
      const r = this.phase === "speaking" ? 0 : 14;
      this.drift = [(Math.random()-0.5)*r, (Math.random()-0.5)*r*0.6];
      this._nextDrift = ts + 1400 + Math.random()*2600;
    }
    // talking mouth: oscillation while speaking, plus per-chunk pulses
    if (this.phase === "speaking") {
      this.talk = 0.5 + 0.5*Math.abs(Math.sin(ts/90));
    } else {
      this.talk = lerp(this.talk, 0, 0.1);
    }
    this._mouthPulse = Math.max(0, this._mouthPulse - 0.08);

    this._render();
    requestAnimationFrame((t) => this._loop(t));
  }

  _render() {
    const { EY, EX, ER } = this.eyeGeom;
    const cx = VIEW.w/2;
    // pupils + iris follow gaze
    const [px, py] = this.s.pupil;
    for (const [iris, pup, sign] of [[this.lIris, this.lPup, -1], [this.rIris, this.rPup, 1]]) {
      const ex = cx + sign*EX;
      iris.setAttribute("cx", ex + px); iris.setAttribute("cy", EY + py);
      pup.setAttribute("cx", ex + px*1.4); pup.setAttribute("cy", EY + py*1.4);
      iris.setAttribute("opacity", 0.55 + 0.45*this.s.glow);
    }
    // eyelids: lid (0 open .. 1 closed) + transient blink
    const closed = Math.min(1, this.s.lid + this.blink);
    for (const [lid, sign] of [[this.lLid, -1], [this.rLid, 1]]) {
      const ex = cx + sign*EX;
      const h = 2*ER*closed;
      lid.setAttribute("x", ex - ER); lid.setAttribute("y", EY - ER);
      lid.setAttribute("width", 2*ER); lid.setAttribute("height", h);
    }
    // eyebrows: vertical offset + tilt (tilt sign mirrored per side)
    const by = EY - 95 + this.s.browY;
    const tilt = this.s.browTilt * 26;
    this.lBrow.setAttribute("x1", cx - EX - 55); this.lBrow.setAttribute("y1", by + tilt);
    this.lBrow.setAttribute("x2", cx - EX + 55); this.lBrow.setAttribute("y2", by - tilt);
    this.rBrow.setAttribute("x1", cx + EX - 55); this.rBrow.setAttribute("y1", by - tilt);
    this.rBrow.setAttribute("x2", cx + EX + 55); this.rBrow.setAttribute("y2", by + tilt);
    // mouth: corners fixed at the baseline; the middle dips DOWN for a smile
    // (mouthCurve > 0) or up for a frown (< 0). `open` adds talking aperture.
    const my = 312, mw = 150;
    const open = (this.s.mouthOpen + this.talk*0.55 + this._mouthPulse*0.25) * 70;
    const smile = this.s.mouthCurve * 90;   // + = happy (U), - = sad (∩)
    const x0 = cx - mw, x1 = cx + mw;
    const upper = my - open + smile*0.35;
    const lower = my + open + smile;
    const d = `M ${x0} ${my} Q ${cx} ${upper} ${x1} ${my} `
            + `Q ${cx} ${lower} ${x0} ${my} Z`;
    this.mouth.setAttribute("d", d);
    this.mouth.setAttribute("fill", open > 8 ? "rgba(126,224,255,0.18)" : "none");
  }
}

window.RobotFace = RobotFace;
