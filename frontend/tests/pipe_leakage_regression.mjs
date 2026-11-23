/**
 * Regression tests for the pipe-leakage false-positive fix.
 *
 * These are DETERMINISTIC RULE-VALIDATION tests, not model inference tests.
 * The two functions under test are read directly from the real source file and
 * evaluated in a sandbox with a canvas shim, so the tests exercise the actual
 * shipped code (no copy, no re-implementation).
 *
 * Synthetic pixel scenes are fixtures used only to validate decision rules.
 * They are never treated as operational footage and no detection is fabricated:
 * every case asserts the honest verdict.
 *
 * Run: node tests/pipe_leakage_regression.mjs
 */

import assert from "node:assert";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = path.resolve(__dirname, "../src/services/realVisionProcessor.js");

// --------------------------------------------------------------- scene builder
function makeScene(kind, width = 320, height = 240) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < data.length; i += 4) {
    data[i] = 120; data[i + 1] = 120; data[i + 2] = 120; data[i + 3] = 255;
  }
  const set = (x, y, r, g, b) => {
    if (x < 0 || y < 0 || x >= width || y >= height) return;
    const i = (y * width + x) * 4;
    data[i] = r; data[i + 1] = g; data[i + 2] = b; data[i + 3] = 255;
  };
  const rect = (x0, y0, x1, y1, r, g, b) => {
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) set(x, y, r, g, b);
  };

  switch (kind) {
    case "room":
      rect(0, 0, width, 60, 190, 190, 185);
      rect(20, 70, 90, 150, 230, 230, 225);
      rect(200, 90, 300, 220, 95, 70, 50);
      rect(130, 130, 180, 230, 60, 60, 65);
      rect(140, 100, 170, 130, 210, 190, 170);
      break;
    case "cupboard":
      rect(0, 0, width, height, 200, 195, 185);
      rect(60, 40, 260, 210, 110, 75, 45);
      rect(60, 120, 260, 128, 70, 45, 25);
      break;
    case "wall":
      rect(0, 0, width, height, 215, 212, 205);
      rect(0, 150, width, 158, 150, 148, 142);
      break;
    case "dark_clothing":
      rect(0, 0, width, height, 205, 200, 195);
      rect(110, 60, 210, 235, 28, 28, 32);
      rect(95, 70, 115, 235, 35, 35, 40);
      break;
    case "pipe":
    case "pipe_leak": {
      rect(0, 0, width, height, 90, 92, 96);
      rect(10, 100, 310, 106, 35, 35, 38);
      rect(10, 140, 310, 146, 35, 35, 38);
      rect(10, 106, 310, 140, 205, 208, 212);
      rect(10, 112, 310, 120, 245, 246, 248);
      if (kind === "pipe_leak") {
        // Escaping jet from a joint on the underside of the pipe: many small
        // bright droplets (2px wide so a luminance gradient exists) separated by
        // darker background. This is deliberately NOT a smooth bright region, so
        // it is distinguishable from a specular highlight on a clean pipe.
        for (let y = 144; y < 188; y += 3) {
          for (let x = 138; x < 232; x += 5) {
            const v = 205 + ((x * 7 + y * 13) % 50);
            set(x, y, v, v, v);
            set(x + 1, y, v, v, v);
          }
        }
      }
      break;
    }
    default:
      throw new Error(`unknown scene ${kind}`);
  }
  return { data, width, height };
}

// ------------------------------------------------------------------ canvas shim
let pendingScene = null;

function makeCanvas() {
  const canvas = {
    _w: 0,
    _h: 0,
    _pixels: null,
    getContext() {
      const self = this;
      return {
        drawImage() {},
        getImageData() {
          if (!self._pixels) throw new Error("canvas has no pixel data");
          return { data: self._pixels, width: self._w, height: self._h };
        }
      };
    }
  };
  Object.defineProperty(canvas, "width", {
    get() { return this._w; },
    set(v) { this._w = v; this._sync(); }
  });
  Object.defineProperty(canvas, "height", {
    get() { return this._h; },
    set(v) { this._h = v; this._sync(); }
  });
  canvas._sync = () => {
    if (!canvas._pixels && pendingScene && canvas._w > 0 && canvas._h > 0) {
      canvas._pixels = resize(pendingScene, canvas._w, canvas._h);
    }
  };
  return canvas;
}

function resize(scene, w, h) {
  const out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const sx = Math.min(scene.width - 1, Math.floor((x / w) * scene.width));
      const sy = Math.min(scene.height - 1, Math.floor((y / h) * scene.height));
      const si = (sy * scene.width + sx) * 4;
      const di = (y * w + x) * 4;
      out[di] = scene.data[si];
      out[di + 1] = scene.data[si + 1];
      out[di + 2] = scene.data[si + 2];
      out[di + 3] = 255;
    }
  }
  return out;
}

const sandbox = {
  console,
  document: { createElement: () => makeCanvas() },
  performance: { now: () => Date.now() },
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => {},
  setTimeout,
  clearTimeout
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;

// ----------------------------------------------- load the real functions only
// Strip imports/exports so the file can be evaluated standalone in the sandbox.
const raw = fs.readFileSync(SOURCE, "utf8");
const stripped = raw
  .replace(/^import[\s\S]*?from\s+["'][^"']+["'];?\s*$/gm, "")
  .replace(/^export\s+(async\s+)?function/mg, "$1function")
  .replace(/^export\s+const/mg, "const")
  .replace(/^export\s+default\s+/m, "");

const context = vm.createContext(sandbox);
try {
  vm.runInContext(stripped, context, { filename: SOURCE });
} catch (err) {
  // Some module-level references (e.g. tf) are unused by the functions under
  // test; only fail if the functions themselves are missing.
  console.log(`note: module evaluation warning: ${err.message}`);
}

const detectPipeLeakage = sandbox.detectPipeLeakage;
const detectPipeContext = sandbox.detectPipeContext;

if (typeof detectPipeLeakage !== "function" || typeof detectPipeContext !== "function") {
  console.error("FATAL: could not load detectPipeLeakage/detectPipeContext from source");
  process.exit(2);
}

// ------------------------------------------------------------------- utilities
function sourceFor(kind) {
  const scene = makeScene(kind);
  return {
    width: scene.width,
    height: scene.height,
    videoWidth: scene.width,
    videoHeight: scene.height,
    naturalWidth: scene.width,
    naturalHeight: scene.height
  };
}

function runLeakage(kind, options = {}) {
  pendingScene = makeScene(kind);
  return detectPipeLeakage(sourceFor(kind), { source: "UPLOADED_IMAGE", ...options });
}

function runContext(kind) {
  pendingScene = makeScene(kind);
  return detectPipeContext(sourceFor(kind));
}

let passed = 0;
let failed = 0;
const failures = [];

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  PASS  ${name}`);
  } catch (err) {
    failed++;
    failures.push([name, err.message]);
    console.log(`  FAIL  ${name}  :: ${err.message}`);
  }
}

// --------------------------------------------------------------------- tests
console.log("=== Pipe-context detection ===");

test("plain bright wall has no pipe", () => {
  const ctx = runContext("wall");
  assert.strictEqual(ctx.pipeVisible, false, `signals=${JSON.stringify(ctx.signals)}`);
});

test("cupboard scene has no pipe", () => {
  const ctx = runContext("cupboard");
  assert.strictEqual(ctx.pipeVisible, false);
});

test("ordinary room is not mistaken for a pipe", () => {
  const ctx = runContext("room");
  assert.strictEqual(ctx.pipeVisible, false, "a bright room must not be classified as a pipe");
});

test("synthetic pipe scene IS detected as a pipe", () => {
  const ctx = runContext("pipe");
  assert.strictEqual(ctx.pipeVisible, true, `signals=${JSON.stringify(ctx.signals)}`);
  assert.ok(Array.isArray(ctx.region) && ctx.region.length === 4, "pipe region required");
});

console.log("\n=== Required regression scenarios (1-10) ===");

// 1. Person sitting in an ordinary room -> NO pipe leakage alert
test("1. person in ordinary room -> NO pipe leakage alert", () => {
  const res = runLeakage("room");
  assert.strictEqual(res.detected, false, "room must not be flagged as leakage");
  assert.strictEqual(res.status, "NO_PIPE_DETECTED");
  assert.match(res.operator_message, /PIPE NOT VISIBLE/);
});

// 2. Cupboard / furniture scene -> NO pipe defect alert
test("2. cupboard/furniture scene -> NO pipe leakage alert", () => {
  const res = runLeakage("cupboard");
  assert.strictEqual(res.detected, false);
  assert.strictEqual(res.status, "NO_PIPE_DETECTED");
});

// 3. Wall-only scene -> NO pipe leakage alert
test("3. wall-only scene -> NO pipe leakage alert", () => {
  const res = runLeakage("wall");
  assert.strictEqual(res.detected, false);
  assert.strictEqual(res.status, "NO_PIPE_DETECTED");
});

// 4. Person wearing dark clothing -> NO pipe defect alert
test("4. person in dark clothing -> NO pipe leakage alert", () => {
  const res = runLeakage("dark_clothing");
  assert.strictEqual(res.detected, false);
  assert.strictEqual(res.status, "NO_PIPE_DETECTED");
});

// 5. Actual pipe with no visible leakage -> NO leakage alert
test("5. pipe with no leakage -> NO leakage alert (pipe still recognised)", () => {
  const res = runLeakage("pipe");
  assert.strictEqual(res.detected, false, "a clean pipe must not be flagged");
  assert.strictEqual(res.status, "NO_LEAKAGE");
  assert.strictEqual(res.pipe_detected, true);
});

// 6. Actual pipe with a supported spray pattern -> valid review-level detection
test("6. pipe with spray pattern -> review-level detection inside pipe region", () => {
  const res = runLeakage("pipe_leak");
  assert.strictEqual(res.detected, true, "spray inside the pipe region should be flagged for review");
  assert.strictEqual(res.pipe_detected, true);
  assert.strictEqual(res.status, "REVIEW_REQUIRED");
  assert.ok(res.pipe_context && res.pipe_context.region, "pipe context region required");
  assert.ok(res.evidence_region, "evidence region required for a flagged frame");
});

// 7. Confidence must be honest and capped (no fabricated 0.85+)
test("7. heuristic confidence is computed and capped below confirmation", () => {
  const res = runLeakage("pipe_leak");
  assert.ok(res.confidence <= 0.75, `confidence ${res.confidence} must be capped at 0.75`);
  assert.ok(res.confidence >= 0.35, `confidence ${res.confidence} must be a real computed score`);
  assert.strictEqual(res.confidence_source, "PIXEL_HEURISTIC_NOT_A_TRAINED_MODEL");
});

// 8. No pipe detected -> explicit status, never a confirmed alert
test("8. no pipe -> NO_PIPE_DETECTED, zero confidence, no evidence", () => {
  const res = runLeakage("room");
  assert.strictEqual(res.status, "NO_PIPE_DETECTED");
  assert.strictEqual(res.evidence_region, null, "no evidence region may be attached");
  assert.strictEqual(res.confidence, 0);
});

// 9. Model/source unavailable -> clear unavailable response, never a fake detection
test("9. missing source -> UNABLE_TO_INSPECT, never a fake detection", () => {
  const res = detectPipeLeakage(null, { source: "UPLOADED_IMAGE" });
  assert.strictEqual(res.detected, false);
  assert.strictEqual(res.status, "UNABLE_TO_INSPECT");
  assert.strictEqual(res.confidence, 0);
});

// 10. Every non-detection path carries zero confidence and no evidence
test("10. all no-detection paths carry zero confidence and no evidence", () => {
  for (const kind of ["room", "cupboard", "wall", "dark_clothing"]) {
    const res = runLeakage(kind);
    assert.strictEqual(res.detected, false, kind);
    assert.strictEqual(res.confidence, 0, `${kind} must report zero confidence`);
    assert.strictEqual(res.evidence_region, null, `${kind} must not attach evidence`);
    assert.strictEqual(res.evidence_region, res.evidence_region, "stable");
  }
});

console.log("\n=== Robustness ===");

test("operator ROI that is too small is rejected safely", () => {
  const res = runLeakage("pipe", { pipeRegion: [0.4, 0.4, 0.02, 0.02] });
  assert.strictEqual(res.detected, false);
  assert.strictEqual(res.status, "NO_PIPE_DETECTED");
});

test("missing source element handled by detectPipeContext", () => {
  const ctx = detectPipeContext(null);
  assert.strictEqual(ctx.pipeVisible, false);
  assert.ok(ctx.reason, "reason required");
});

console.log(`\n===== PIPE LEAKAGE REGRESSION: ${passed} passed, ${failed} failed =====`);
if (failures.length) {
  console.log("failures:");
  for (const [n, m] of failures) console.log(`  - ${n}: ${m}`);
}
process.exit(failed ? 1 : 0);