/* face.js - visor-style SVG robot avatar.
 *
 * Public API remains compatible with app.js:
 *   const face = new RobotFace(svgEl);
 *   face.setPhase("thinking" | "listening" | "speaking" | "error" | "inactive");
 *   face.setTalking(true | false);
 *   face.pulseMouth();
 */

const VIEW = { w: 600, h: 420 };

const EXPR = {
  inactive: {
    lid: 0.46,
    browY: 4,
    browTilt: 0.05,
    pupil: [0, 2],
    mouthOpen: 0.08,
    smile: 0.72,
    glow: 0.35,
  },
  listening: {
    lid: 0.08,
    browY: -6,
    browTilt: -0.06,
    pupil: [0, 0],
    mouthOpen: 0.2,
    smile: 0.34,
    glow: 0.8,
  },
  thinking: {
    lid: 0.2,
    browY: -10,
    browTilt: 0.36,
    pupil: [10, -7],
    mouthOpen: 0.06,
    smile: 0.02,
    glow: 0.62,
  },
  speaking: {
    lid: 0.05,
    browY: -2,
    browTilt: 0,
    pupil: [0, 0],
    mouthOpen: 0.34,
    smile: 0.22,
    glow: 1,
  },
  error: {
    lid: 0.34,
    browY: 8,
    browTilt: -0.7,
    pupil: [0, 4],
    mouthOpen: 0.04,
    smile: -0.52,
    glow: 0.5,
  },
};

const lerp = (a, b, t) => a + (b - a) * t;

class RobotFace {
  constructor(svg) {
    this.svg = svg;
    svg.setAttribute("viewBox", `0 0 ${VIEW.w} ${VIEW.h}`);
    this._uid = `rf${Math.random().toString(36).slice(2, 9)}`;
    this._build();

    this.s = { ...EXPR.inactive, pupil: [...EXPR.inactive.pupil] };
    this.t = { ...EXPR.inactive, pupil: [...EXPR.inactive.pupil] };

    this.phase = "inactive";
    this.blink = 0;
    this._talking = false;
    this._mouthPulse = 0;
    this.drift = [0, 0];
    this._nextBlink = performance.now() + 1900;
    this._nextDrift = performance.now() + 1300;
    requestAnimationFrame((ts) => this._loop(ts));
  }

  setPhase(phase) {
    if (!EXPR[phase]) return;
    this.phase = phase;
    if (phase !== "speaking") this._talking = false;
    const e = EXPR[phase];
    this.t = { ...e, pupil: [...e.pupil] };
  }

  setTalking(on) {
    this._talking = !!on;
  }

  pulseMouth() {
    this._mouthPulse = 1;
  }

  _build() {
    const NS = "http://www.w3.org/2000/svg";
    const mk = (tag, attrs) => {
      const n = document.createElementNS(NS, tag);
      for (const k in attrs) n.setAttribute(k, attrs[k]);
      return n;
    };

    const defs = mk("defs", {});
    defs.innerHTML = `<filter id="${this._uid}-glow" x="-80%" y="-80%" width="260%" height="260%">
         <feGaussianBlur stdDeviation="5" result="g"/>
         <feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge>
       </filter>`;
    this.svg.appendChild(defs);

    this.eye = { y: 165, x: 146, w: 168, h: 110, r: 28 };
    this.mouthY = 306;

    // Eye shells are dark sockets (no border) — also used as clip shapes for lids
    const eyeShellAttrs = (side) => ({
      x: VIEW.w / 2 + side * this.eye.x - this.eye.w / 2,
      y: this.eye.y - this.eye.h / 2,
      width: this.eye.w, height: this.eye.h, rx: this.eye.r,
      fill: "#0a1226",
    });
    this.lEyeShell = mk("rect", eyeShellAttrs(-1));
    this.rEyeShell = mk("rect", eyeShellAttrs(1));

    // Clip lids to the rounded eye-shell shape so corners are always round
    const makeClip = (id, side) => {
      const cp = mk("clipPath", { id });
      cp.appendChild(mk("rect", eyeShellAttrs(side)));
      return cp;
    };
    defs.appendChild(makeClip(`${this._uid}-clipL`, -1));
    defs.appendChild(makeClip(`${this._uid}-clipR`,  1));

    this.lEyeGlow = mk("rect", {
      x: VIEW.w / 2 - this.eye.x - this.eye.w / 2 + 10,
      y: this.eye.y - this.eye.h / 2 + 10,
      width: this.eye.w - 20,
      height: this.eye.h - 20,
      rx: 20,
      fill: "var(--face)",
      opacity: 0.7,
      filter: `url(#${this._uid}-glow)`,
    });
    this.rEyeGlow = mk("rect", {
      x: VIEW.w / 2 + this.eye.x - this.eye.w / 2 + 10,
      y: this.eye.y - this.eye.h / 2 + 10,
      width: this.eye.w - 20,
      height: this.eye.h - 20,
      rx: 20,
      fill: "var(--face)",
      opacity: 0.7,
      filter: `url(#${this._uid}-glow)`,
    });

    this.lPupil = mk("rect", {
      x: 0,
      y: 0,
      width: 36,
      height: 36,
      rx: 11,
      fill: "#050b18",
      stroke: "#7ee0ff",
      "stroke-width": 1,
    });
    this.rPupil = mk("rect", {
      x: 0,
      y: 0,
      width: 36,
      height: 36,
      rx: 11,
      fill: "#050b18",
      stroke: "#7ee0ff",
      "stroke-width": 1,
    });

    this.lLid = mk("rect", {
      x: VIEW.w / 2 - this.eye.x - this.eye.w / 2,
      y: this.eye.y - this.eye.h / 2,
      width: this.eye.w, height: 0, fill: "#0a1226",
      "clip-path": `url(#${this._uid}-clipL)`,
    });
    this.rLid = mk("rect", {
      x: VIEW.w / 2 + this.eye.x - this.eye.w / 2,
      y: this.eye.y - this.eye.h / 2,
      width: this.eye.w, height: 0, fill: "#0a1226",
      "clip-path": `url(#${this._uid}-clipR)`,
    });

    this.lBrow = mk("line", {
      x1: VIEW.w / 2 - this.eye.x - 64,
      y1: this.eye.y - 92,
      x2: VIEW.w / 2 - this.eye.x + 64,
      y2: this.eye.y - 92,
      stroke: "#88ebff",
      "stroke-width": 10,
      "stroke-linecap": "round",
      filter: `url(#${this._uid}-glow)`,
    });
    this.rBrow = mk("line", {
      x1: VIEW.w / 2 + this.eye.x - 64,
      y1: this.eye.y - 92,
      x2: VIEW.w / 2 + this.eye.x + 64,
      y2: this.eye.y - 92,
      stroke: "#88ebff",
      "stroke-width": 10,
      "stroke-linecap": "round",
      filter: `url(#${this._uid}-glow)`,
    });

    this.mouth = mk("path", {
      d: "",
      fill: "none",
      stroke: "#8be8ff",
      "stroke-width": 9,
      "stroke-linecap": "round",
      filter: `url(#${this._uid}-glow)`,
    });
    [
      this.lEyeShell,
      this.rEyeShell,
      this.lEyeGlow,
      this.rEyeGlow,
      this.lPupil,
      this.rPupil,
      this.lBrow,
      this.rBrow,
      this.mouth,
    ].forEach(el => this.svg.appendChild(el));
    // Lids appended last so they paint over the eye glow
    this.svg.appendChild(this.lLid);
    this.svg.appendChild(this.rLid);
  }

  _loop(ts) {
    const k = 0.12;
    for (const key of [
      "lid",
      "browY",
      "browTilt",
      "mouthOpen",
      "smile",
      "glow",
    ]) {
      this.s[key] = lerp(this.s[key], this.t[key], k);
    }
    this.s.pupil[0] = lerp(this.s.pupil[0], this.t.pupil[0] + this.drift[0], k);
    this.s.pupil[1] = lerp(this.s.pupil[1], this.t.pupil[1] + this.drift[1], k);

    if (ts > this._nextBlink) {
      this.blink = 1;
      this._nextBlink = ts + 2200 + Math.random() * 3400;
    }
    this.blink = Math.max(0, this.blink - 0.14);

    if (ts > this._nextDrift) {
      const range = this.phase === "speaking" ? 0 : 14;
      this.drift = [
        (Math.random() - 0.5) * range,
        (Math.random() - 0.5) * range * 0.6,
      ];
      this._nextDrift = ts + 1200 + Math.random() * 2200;
    }

    this._mouthPulse = Math.max(0, this._mouthPulse - 0.09);

    this._render(ts);
    requestAnimationFrame((t) => this._loop(t));
  }

  _render(ts) {
    const cx = VIEW.w / 2;
    const [px, py] = this.s.pupil;
    const eyeY = this.eye.y;
    const pupilSize = 36;

    const eyeLeftX = cx - this.eye.x;
    const eyeRightX = cx + this.eye.x;
    this.lPupil.setAttribute("x", eyeLeftX - pupilSize / 2 + px * 1.25);
    this.lPupil.setAttribute("y", eyeY - pupilSize / 2 + py * 1.25);
    this.rPupil.setAttribute("x", eyeRightX - pupilSize / 2 + px * 1.25);
    this.rPupil.setAttribute("y", eyeY - pupilSize / 2 + py * 1.25);

    const glowOpacity = 0.45 + 0.5 * this.s.glow;
    this.lEyeGlow.setAttribute("opacity", glowOpacity);
    this.rEyeGlow.setAttribute("opacity", glowOpacity);

    const closed = Math.min(1, this.s.lid + this.blink);
    const lidH = this.eye.h * closed;
    const lidTop = eyeY - this.eye.h / 2;
    this.lLid.setAttribute("height", lidH);
    this.rLid.setAttribute("height", lidH);

    const browBase = eyeY - 92 + this.s.browY;
    const tilt = this.s.browTilt * 28;
    this.lBrow.setAttribute("y1", browBase + tilt);
    this.lBrow.setAttribute("y2", browBase - tilt);
    this.rBrow.setAttribute("y1", browBase - tilt);
    this.rBrow.setAttribute("y2", browBase + tilt);

    const talkOsc = this._talking ? 0.5 + 0.5 * Math.sin(ts / 105) : 0;
    const open = this.s.mouthOpen + talkOsc * 0.52 + this._mouthPulse * 0.34;
    const smile = this.s.smile - talkOsc * 0.06;
    const halfW = 86 + talkOsc * 18;
    const y = this.mouthY;
    const controlY = y + smile * 34;
    const bottom = y + open * 18;
    const path = [
      `M ${cx - halfW} ${y}`,
      `Q ${cx} ${controlY} ${cx + halfW} ${y}`,
      `Q ${cx} ${bottom} ${cx - halfW} ${y}`,
      "Z",
    ].join(" ");
    this.mouth.setAttribute("d", path);
    this.mouth.setAttribute("stroke-width", 8 + open * 8);


  }
}

window.RobotFace = RobotFace;
