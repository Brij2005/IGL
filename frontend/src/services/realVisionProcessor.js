/**
 * Real-Time Canvas & Computer Vision Frame Processing Engine
 * Powered by TensorFlow.js & COCO-SSD for Production-Grade AI Object Detection.
 */

import * as tf from "@tensorflow/tfjs";
import * as cocoSsd from "@tensorflow-models/coco-ssd";
import {
  detectPhoneWithBackend,
  getPhoneDetectionBackendStatus,
  MODEL_NAME as PHONE_DETECTOR_MODEL_NAME
} from "./phoneDetectionProcessor";
import { captureFaceDescriptor, matchRegisteredFace, getFaceRecognitionStatus } from "./faceRecognition";

// ============================================================================
// PART 1: Global AI Model & State Handles (COCO-SSD & Dedicated Helmet ML Model)
// ============================================================================

// COCO-SSD Model Handles
let cocoModel = null;
let modelLoadingPromise = null;
let modelStatus = "UNINITIALIZED"; // UNINITIALIZED, LOADING, READY, ERROR
let modelErrorMessage = "";

// PART 7: Configuration Values & Thresholds
export const HELMET_MODEL_URL = "/models/helmet/model.json"; // Path to custom trained TFJS helmet model files
export const HELMET_CONFIDENCE_THRESHOLD = 0.65;
export const HELMET_PERSISTENCE_FRAMES = 3;
export const HELMET_MISS_GRACE_FRAMES = 3;

// PART 1: Dedicated Helmet ML Model Handles
let helmetModel = null;
let helmetModelLoadingPromise = null;
let helmetModelStatus = "UNINITIALIZED"; // UNINITIALIZED, LOADING, READY, ERROR
let helmetModelErrorMessage = "";

// Track ID Generator & Track Memory Across Frames
let nextTrackId = 101;
const TRACK_SESSION_ID = Math.random().toString(36).slice(2, 8).toUpperCase();
let activeTracks = []; // [{ trackId, box: [x, y, w, h], center: [cx, cy], lastSeen: timestamp }]
const TRACK_RETENTION_MS = 8000;

// PART 4: Per-Person Helmet Verification Tracking State Map
// key: trackId -> {
//   hasHelmet: boolean,
//   confidence: number,
//   persistenceCount: number,
//   missGraceCount: number,
//   lastSeen: timestamp,
//   reason: string
// }
const helmetVerificationState = new Map();
let faceRecognitionFailureReported = false;

// Siren Web Audio API Synthesizer Handle
let sirenAudioContext = null;
let sirenOscillator = null;
let sirenGain = null;
let sirenInterval = null;

// Reusable Offscreen Canvas for Two-Pass ROI Phone Detection
let reusableRoiCanvas = null;
let reusableRoiCtx = null;

function getRoiCanvas(targetWidth, targetHeight) {
  if (typeof document === "undefined") return { canvas: null, ctx: null };
  if (!reusableRoiCanvas) {
    reusableRoiCanvas = document.createElement("canvas");
    reusableRoiCtx = reusableRoiCanvas.getContext("2d", { willReadFrequently: true });
  }
  reusableRoiCanvas.width = targetWidth;
  reusableRoiCanvas.height = targetHeight;
  return { canvas: reusableRoiCanvas, ctx: reusableRoiCtx };
}

/**
 * Calculate Intersection over Union (IoU) between two bounding boxes [x, y, w, h]
 */
function calculateIoU(boxA, boxB) {
  const xA = Math.max(boxA[0], boxB[0]);
  const yA = Math.max(boxA[1], boxB[1]);
  const xB = Math.min(boxA[0] + boxA[2], boxB[0] + boxB[2]);
  const yB = Math.min(boxA[1] + boxA[3], boxB[1] + boxB[3]);

  const interWidth = Math.max(0, xB - xA);
  const interHeight = Math.max(0, yB - yA);
  const interArea = interWidth * interHeight;

  if (interArea === 0) return 0;

  const boxAArea = boxA[2] * boxA[3];
  const boxBArea = boxB[2] * boxB[3];

  return interArea / (boxAArea + boxBArea - interArea);
}

// ============================================================================
// PART 2: TensorFlow.js Model Loaders (COCO-SSD & Custom Helmet ML Model)
// ============================================================================

/**
 * Load TensorFlow COCO-SSD Model asynchronously
 * Prefers 'mobilenet_v2' for higher resolution feature extraction on small objects like cell phones.
 */
export async function loadDetectionModel() {
  if (modelStatus === "READY" && cocoModel) {
    return { success: true, model: cocoModel };
  }

  if (modelStatus === "LOADING" && modelLoadingPromise) {
    return modelLoadingPromise;
  }

  modelStatus = "LOADING";
  modelErrorMessage = "";

  modelLoadingPromise = (async () => {
    try {
      await tf.ready();
      try {
        cocoModel = await cocoSsd.load({
          base: "mobilenet_v2"
        });
      } catch (e) {
        console.warn("Falling back to lite_mobilenet_v2:", e);
        cocoModel = await cocoSsd.load({
          base: "lite_mobilenet_v2"
        });
      }

      modelStatus = "READY";
      console.log("✅ TensorFlow COCO-SSD Object Detection Model Loaded Successfully.");
      return { success: true, model: cocoModel };
    } catch (err) {
      modelStatus = "ERROR";
      modelErrorMessage = `AI MODEL UNAVAILABLE: ${err.message || "Failed to load TensorFlow model"}`;
      console.error("❌ COCO-SSD Model Load Error:", err);
      return { success: false, error: modelErrorMessage };
    }
  })();

  return modelLoadingPromise;
}

/**
 * Returns current status of the COCO-SSD AI Model
 */
export function getModelStatus() {
  return {
    status: modelStatus,
    errorMessage: modelErrorMessage,
    isReady: modelStatus === "READY"
  };
}

/**
 * PART 2: Load Custom TensorFlow.js Dedicated Helmet/PPE ML Model asynchronously.
 * Can be loaded from local static path (/models/helmet/model.json) or remote URL.
 */
export async function loadHelmetDetectionModel(modelUrl = HELMET_MODEL_URL) {
  if (helmetModelStatus === "READY" && helmetModel) {
    return { success: true, model: helmetModel };
  }

  if (helmetModelStatus === "LOADING" && helmetModelLoadingPromise) {
    return helmetModelLoadingPromise;
  }

  helmetModelStatus = "LOADING";
  helmetModelErrorMessage = "";

  helmetModelLoadingPromise = (async () => {
    try {
      await tf.ready();

      // Try loading custom TFJS GraphModel or LayersModel
      try {
        helmetModel = await tf.loadGraphModel(modelUrl);
      } catch (e1) {
        try {
          helmetModel = await tf.loadLayersModel(modelUrl);
        } catch (e2) {
          throw new Error(`Model file not found at ${modelUrl}`);
        }
      }

      helmetModelStatus = "READY";
      console.log(`✅ Dedicated Helmet ML Model Loaded Successfully from ${modelUrl}.`);
      return { success: true, model: helmetModel };
    } catch (err) {
      helmetModelStatus = "ERROR";
      helmetModelErrorMessage = `HELMET MODEL UNAVAILABLE: Trained ML helmet model files not found at ${modelUrl}. Helmet status cannot be verified.`;
      return { success: false, error: helmetModelErrorMessage };
    }
  })();

  return helmetModelLoadingPromise;
}

/**
 * Returns current status of the Dedicated Helmet ML Model
 */
export function getHelmetModelStatus() {
  return {
    status: helmetModelStatus,
    errorMessage: helmetModelErrorMessage,
    isReady: helmetModelStatus === "READY",
    modelUrl: HELMET_MODEL_URL
  };
}

// ============================================================================
// PART 3: Dedicated Helmet ML Model Inference on Head Crop (`detectHelmetForPerson`)
// ============================================================================

/**
 * PART 3: Runs Dedicated ML Inference on Person's Head/Upper-Body Crop.
 * 
 * STRICT CONSERVATIVE SAFETY RULE:
 * Returns null for hasHelmet when inference cannot verify the PPE state.
 * A confirmed missing helmet is reported only when an available model scores below threshold.
 */
export async function detectHelmetForPerson(videoOrCanvas, personNormBox, config = {}) {
  const threshold = config.helmetThreshold || HELMET_CONFIDENCE_THRESHOLD;

  if (!videoOrCanvas || !personNormBox || personNormBox.length < 4) {
    return {
      hasHelmet: null,
      confidence: 0.0,
      reason: "Helmet verification unavailable (Invalid video/canvas or bounding box)",
      modelStatus: helmetModelStatus,
      diagnostics: { rawScore: 0 }
    };
  }

  // Ensure helmet model is loaded
  if (helmetModelStatus === "ERROR") {
    return {
      hasHelmet: null,
      confidence: 0.0,
      reason: helmetModelErrorMessage || "Helmet model unavailable; status cannot be verified.",
      modelStatus: helmetModelStatus,
      diagnostics: { rawScore: 0 }
    };
  }

  if (helmetModelStatus !== "READY") {
    const loadRes = await loadHelmetDetectionModel();
    if (!loadRes.success || !helmetModel) {
      return {
        hasHelmet: null,
        confidence: 0.0,
        reason: "Helmet verification unavailable (Trained ML helmet model files not supplied at /models/helmet/model.json)",
        modelStatus: helmetModelStatus,
        diagnostics: { rawScore: 0 }
      };
    }
  }

  const tensorToDispose = [];

  try {
    const width = videoOrCanvas.width || videoOrCanvas.videoWidth || 640;
    const height = videoOrCanvas.height || videoOrCanvas.videoHeight || 360;

    if (width === 0 || height === 0) {
      return {
        hasHelmet: null,
        confidence: 0.0,
        reason: "Helmet verification unavailable (Zero video dimensions)",
        modelStatus: helmetModelStatus,
        diagnostics: { rawScore: 0 }
      };
    }

    // Crop top 25% (Head & Shoulder ROI) of person bounding box
    const bx = Math.max(0, Math.min(width - 1, Math.floor(personNormBox[0] * width)));
    const by = Math.max(0, Math.min(height - 1, Math.floor(personNormBox[1] * height)));
    const bw = Math.max(10, Math.min(width - bx, Math.floor(personNormBox[2] * width)));
    const bh = Math.max(10, Math.min(height - by, Math.floor(personNormBox[3] * height * 0.25)));

    if (bw < 10 || bh < 10) {
      return {
        hasHelmet: null,
        confidence: 0.0,
        reason: "Helmet verification unavailable (Head crop too small)",
        modelStatus: helmetModelStatus,
        diagnostics: { rawScore: 0 }
      };
    }

    // Extract head crop onto an offscreen canvas
    const cropCanvas = document.createElement("canvas");
    cropCanvas.width = bw;
    cropCanvas.height = bh;
    const cropCtx = cropCanvas.getContext("2d");
    cropCtx.drawImage(videoOrCanvas, bx, by, bw, bh, 0, 0, bw, bh);

    // Convert crop canvas to TensorFlow Tensor (224x224 input tensor)
    const imgTensor = tf.browser.fromPixels(cropCanvas);
    tensorToDispose.push(imgTensor);

    const resizedTensor = tf.image.resizeBilinear(imgTensor, [224, 224]);
    tensorToDispose.push(resizedTensor);

    const normalizedTensor = tf.div(resizedTensor, 255.0).expandDims(0);
    tensorToDispose.push(normalizedTensor);

    // Run ML Model Inference
    const predictionTensor = helmetModel.predict(normalizedTensor);
    tensorToDispose.push(predictionTensor);

    const scores = await predictionTensor.data();

    // Interpret output score (class 0: helmet, class 1: no_helmet or single sigmoid output)
    let rawScore = 0.0;
    if (scores.length >= 2) {
      rawScore = scores[0]; // Helmet class probability
    } else if (scores.length === 1) {
      rawScore = scores[0]; // Sigmoid helmet output
    }

    const hasHelmet = rawScore >= threshold;
    const confidence = Math.round(rawScore * 100) / 100;

    return {
      hasHelmet,
      confidence,
      reason: hasHelmet
        ? `HARDHAT VERIFIED: Dedicated ML Model output (${Math.round(confidence * 100)}% >= ${Math.round(threshold * 100)}%)`
        : `MISSING HELMET: Dedicated ML Model output (${Math.round(confidence * 100)}% below ${Math.round(threshold * 100)}% threshold)`,
      modelStatus: helmetModelStatus,
      diagnostics: { rawScore }
    };

  } catch (err) {
    console.error("Helmet ML Inference Error:", err);
    return {
      hasHelmet: null,
      confidence: 0.0,
      reason: `Helmet verification unavailable (${err.message})`,
      modelStatus: helmetModelStatus,
      diagnostics: { rawScore: 0 }
    };
  } finally {
    // Explicitly dispose intermediate tensors to prevent memory leaks
    tensorToDispose.forEach((t) => {
      try {
        if (t && t.dispose) t.dispose();
      } catch (e) { }
    });
  }
}

// ============================================================================
// PART 6: Legacy `inspectHelmetAndPersonsInCanvas()` Compatibility Wrapper
// ============================================================================

/**
 * PART 6: Legacy Compatibility Function.
 * Deprecated HSV color heuristic is removed as primary decision maker.
 * Delegates directly to ML-based `detectHelmetForPerson()`.
 */
export function inspectHelmetAndPersonsInCanvas(canvas, box) {
  // This compatibility API has no model inference, so PPE status is unknown.
  return {
    hasHelmet: null,
    confidence: 0.0,
    reason: "Helmet verification requires a trained ML model.",
    diagnostics: { colorRatio: 0, upperRegionRatio: 0, spatialScore: 0 }
  };
}

// ============================================================================
// General Utilities (Frame Quality, Canvas Snapshot, Audio Siren)
// ============================================================================

export function analyzeFrameQuality(canvas, ctx) {
  if (!canvas || !ctx) {
    return { isAssessable: false, brightness: 0, blurScore: 0, reason: "Canvas context uninitialized" };
  }

  const width = canvas.width;
  const height = canvas.height;
  if (width === 0 || height === 0) {
    return { isAssessable: false, brightness: 0, blurScore: 0, reason: "Zero width/height video stream" };
  }

  try {
    const sampleWidth = Math.min(width, 160);
    const sampleHeight = Math.min(height, 120);

    const offCanvas = document.createElement("canvas");
    offCanvas.width = sampleWidth;
    offCanvas.height = sampleHeight;
    const offCtx = offCanvas.getContext("2d");
    offCtx.drawImage(canvas, 0, 0, sampleWidth, sampleHeight);

    const imgData = offCtx.getImageData(0, 0, sampleWidth, sampleHeight);
    const data = imgData.data;

    let totalLum = 0;
    const gray = new Float32Array(sampleWidth * sampleHeight);

    for (let i = 0, j = 0; i < data.length; i += 4, j++) {
      const r = data[i];
      const g = data[i + 1];
      const b = data[i + 2];
      const lum = 0.299 * r + 0.587 * g + 0.114 * b;
      gray[j] = lum;
      totalLum += lum;
    }

    const avgBrightness = totalLum / (sampleWidth * sampleHeight);

    let sumGrad = 0;
    let sumGradSq = 0;
    const count = (sampleWidth - 2) * (sampleHeight - 2);

    for (let y = 1; y < sampleHeight - 1; y++) {
      for (let x = 1; x < sampleWidth - 1; x++) {
        const idx = y * sampleWidth + x;
        const val =
          gray[idx - sampleWidth] +
          gray[idx + sampleWidth] +
          gray[idx - 1] +
          gray[idx + 1] -
          4 * gray[idx];

        sumGrad += val;
        sumGradSq += val * val;
      }
    }

    const meanGrad = sumGrad / count;
    const blurScore = (sumGradSq / count) - (meanGrad * meanGrad);

    if (avgBrightness < 25.0) {
      return {
        isAssessable: false,
        brightness: Math.round(avgBrightness),
        blurScore: Math.round(blurScore),
        reason: `NOT ASSESSABLE: Environment pitch dark (Brightness: ${Math.round(avgBrightness)}/255)`
      };
    }

    if (blurScore < 12.0) {
      return {
        isAssessable: false,
        brightness: Math.round(avgBrightness),
        blurScore: Math.round(blurScore),
        reason: `NOT ASSESSABLE: Camera lens obstructed / severe blur (Blur Score: ${Math.round(blurScore)})`
      };
    }

    return {
      isAssessable: true,
      brightness: Math.round(avgBrightness),
      blurScore: Math.round(blurScore),
      reason: "OK"
    };

  } catch (err) {
    return { isAssessable: false, brightness: 0, blurScore: 0, reason: `Frame processing error: ${err.message}` };
  }
}

export function isPointInPolygon(point, polygon) {
  if (!polygon || polygon.length < 3) return false;
  const [x, y] = point;
  let inside = false;

  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i][0], yi = polygon[i][1];
    const xj = polygon[j][0], yj = polygon[j][1];

    const intersect = ((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }

  return inside;
}

export function calculateBoxDistance(box1, box2) {
  const cx1 = box1[0] + box1[2] / 2;
  const cy1 = box1[1] + box1[3] / 2;

  const cx2 = box2[0] + box2[2] / 2;
  const cy2 = box2[1] + box2[3] / 2;

  const dist = Math.sqrt(Math.pow(cx1 - cx2, 2) + Math.pow(cy1 - cy2, 2));
  return Math.round(dist * 100) / 100;
}

export function extractBoxFeatureVector(canvas, box) {
  if (!canvas) return { r_avg: 128, g_avg: 128, b_avg: 128 };
  try {
    const ctx = canvas.getContext("2d");
    const bx = Math.max(0, Math.floor(box[0] * canvas.width));
    const by = Math.max(0, Math.floor(box[1] * canvas.height));
    const bw = Math.max(10, Math.floor(box[2] * canvas.width));
    const bh = Math.max(10, Math.floor(box[3] * canvas.height));

    const imgData = ctx.getImageData(bx, by, bw, bh);
    const data = imgData.data;

    let rSum = 0, gSum = 0, bSum = 0;
    const pixels = data.length / 4;

    for (let i = 0; i < data.length; i += 4) {
      rSum += data[i];
      gSum += data[i + 1];
      bSum += data[i + 2];
    }

    return {
      r_avg: Math.round(rSum / pixels),
      g_avg: Math.round(gSum / pixels),
      b_avg: Math.round(bSum / pixels)
    };
  } catch (err) {
    return { r_avg: 120, g_avg: 120, b_avg: 120 };
  }
}

export function captureCanvasSnapshot(canvas, overlayText = "") {
  if (!canvas) return "";
  try {
    const snapCanvas = document.createElement("canvas");
    snapCanvas.width = canvas.width || 640;
    snapCanvas.height = canvas.height || 360;
    const snapCtx = snapCanvas.getContext("2d");

    snapCtx.drawImage(canvas, 0, 0, snapCanvas.width, snapCanvas.height);

    snapCtx.fillStyle = "rgba(15, 23, 42, 0.88)";
    snapCtx.fillRect(10, snapCanvas.height - 42, snapCanvas.width - 20, 32);
    snapCtx.fillStyle = "#ef4444";
    snapCtx.font = "bold 13px 'JetBrains Mono', monospace";
    const timestamp = new Date().toISOString().replace("T", " ").substring(0, 19);
    snapCtx.fillText(`[EVIDENCE SNAPSHOT] ${timestamp} | ${overlayText}`, 20, snapCanvas.height - 20);

    return snapCanvas.toDataURL("image/jpeg", 0.85);
  } catch (e) {
    return "";
  }
}

export function startSirenAlarm() {
  if (sirenOscillator) return;

  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;

    if (!sirenAudioContext || sirenAudioContext.state === "closed") {
      sirenAudioContext = new AudioCtx();
    }
    if (sirenAudioContext.state === "suspended") {
      sirenAudioContext.resume();
    }

    sirenOscillator = sirenAudioContext.createOscillator();
    sirenGain = sirenAudioContext.createGain();

    sirenOscillator.type = "sawtooth";
    sirenOscillator.frequency.setValueAtTime(600, sirenAudioContext.currentTime);

    sirenGain.gain.setValueAtTime(0.3, sirenAudioContext.currentTime);

    sirenOscillator.connect(sirenGain);
    sirenGain.connect(sirenAudioContext.destination);
    sirenOscillator.start();

    let highPitch = true;
    sirenInterval = setInterval(() => {
      if (sirenOscillator && sirenAudioContext && sirenAudioContext.state === "running") {
        const now = sirenAudioContext.currentTime;
        sirenOscillator.frequency.cancelScheduledValues(now);
        sirenOscillator.frequency.exponentialRampToValueAtTime(highPitch ? 1200 : 600, now + 0.35);
        highPitch = !highPitch;
      }
    }, 400);

  } catch (e) {
    console.error("Siren audio trigger failed:", e);
  }
}

export function stopSirenAlarm() {
  if (sirenInterval) {
    clearInterval(sirenInterval);
    sirenInterval = null;
  }
  if (sirenOscillator) {
    try {
      sirenOscillator.stop();
      sirenOscillator.disconnect();
    } catch (e) { }
    sirenOscillator = null;
  }
  if (sirenGain) {
    try {
      sirenGain.disconnect();
    } catch (e) { }
    sirenGain = null;
  }
}

// Temporal persistence tracker for live camera (requires 5 consecutive frames before triggering)
let liveCameraLeakageConsecutiveFrames = 0;

// ============================================================================
// PART 4B: Dedicated Pipe Leakage Detection Module (`detectPipeLeakage`)
// ============================================================================

/**
 * Dedicated Pipe Leakage Detection Engine (Computer Vision & Fluid Spray Pattern Analysis)
 * 
 * Analyzes image/canvas for visual water jet spray patterns, fluid misting around pipe joints,
 * high-contrast outward directional gradient vectors, and high luminance spray textures.
 * 
 * Returns clean LeakageDetectionResult:
 * {
 *   detected: boolean,
 *   confidence: number,
 *   type: "VISIBLE_PIPE_LEAKAGE",
 *   evidence_region: [normX, normY, normW, normH] | null,
 *   source: "UPLOADED_IMAGE" | "LIVE_CAMERA",
 *   status: "REVIEW_REQUIRED" | "UNABLE_TO_INSPECT" | "NO_LEAKAGE",
 *   qualityCheck: { passed: boolean, reason: string, brightness: number, blurScore: number },
 *   details: string
 * }
 */
/**
 * Detect whether an actual pipe-like structure is visible in the frame.
 *
 * IMPORTANT: this is a deterministic pixel-structure heuristic, NOT a trained
 * model and NOT a classifier. It looks for the characteristic signature of a
 * cylindrical pipe under directional light: two long, roughly parallel
 * high-gradient edges with a brighter band between them. When no such structure
 * exists it reports `pipeVisible: false`, which lets callers refuse to make any
 * pipe-specific claim instead of guessing.
 *
 * @param {CanvasImageSource} sourceElem
 * @param {{region?: [number,number,number,number]}} [options]
 *        `region` is an optional operator-configured normalized ROI [x,y,w,h].
 *        When supplied, only that part of the frame is searched.
 * @returns {{pipeVisible:boolean, confidence:number, region:number[]|null,
 *            axis:string|null, reason:string, signals:object}}
 */
export function detectPipeContext(sourceElem, options = {}) {
  const empty = {
    pipeVisible: false,
    confidence: 0,
    region: null,
    axis: null,
    reason: "No pipe-like structure found",
    signals: { edgeSupport: 0, parallelSupport: 0, highlightStrength: 0 }
  };

  if (!sourceElem) return { ...empty, reason: "No source element" };

  try {
    const srcW = sourceElem.videoWidth || sourceElem.naturalWidth || sourceElem.width || 0;
    const srcH = sourceElem.videoHeight || sourceElem.naturalHeight || sourceElem.height || 0;
    if (!srcW || !srcH) return { ...empty, reason: "Invalid source dimensions" };

    const W = 320;
    const H = Math.max(80, Math.round((srcH / srcW) * W) || 180);
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(sourceElem, 0, 0, W, H);
    const { data } = ctx.getImageData(0, 0, W, H);

    // Optional operator-configured ROI, in source-normalized coordinates.
    let rx0 = 0, ry0 = 0, rx1 = W, ry1 = H;
    const region = options.region;
    if (Array.isArray(region) && region.length === 4) {
      rx0 = Math.max(0, Math.floor(region[0] * W));
      ry0 = Math.max(0, Math.floor(region[1] * H));
      rx1 = Math.min(W, Math.ceil((region[0] + region[2]) * W));
      ry1 = Math.min(H, Math.ceil((region[1] + region[3]) * H));
      if (rx1 - rx0 < 24 || ry1 - ry0 < 24) {
        return { ...empty, reason: "Configured inspection region is too small" };
      }
    }

    // Luminance + gradient magnitude
    const lum = new Float32Array(W * H);
    for (let i = 0, p = 0; i < data.length; i += 4, p++) {
      lum[p] = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    }
    const grad = new Float32Array(W * H);
    for (let y = ry0 + 1; y < ry1 - 1; y++) {
      for (let x = rx0 + 1; x < rx1 - 1; x++) {
        const p = y * W + x;
        const gx = Math.abs(lum[p + 1] - lum[p - 1]);
        const gy = Math.abs(lum[p + W] - lum[p - W]);
        grad[p] = gx + gy;
      }
    }

    const EDGE = 26;
    // Per-row and per-column edge support inside the ROI
    const rowEdge = new Float32Array(H);
    const colEdge = new Float32Array(W);
    const rowLum = new Float32Array(H);
    const colLum = new Float32Array(W);
    for (let y = ry0; y < ry1; y++) {
      let rc = 0, rl = 0;
      for (let x = rx0; x < rx1; x++) {
        const p = y * W + x;
        if (grad[p] > EDGE) rc++;
        rl += lum[p];
      }
      rowEdge[y] = rc;
      rowLum[y] = rl / Math.max(1, rx1 - rx0);
    }
    for (let x = rx0; x < rx1; x++) {
      let cc = 0, cl = 0;
      for (let y = ry0; y < ry1; y++) {
        const p = y * W + x;
        if (grad[p] > EDGE) cc++;
        cl += lum[p];
      }
      colEdge[x] = cc;
      colLum[x] = cl / Math.max(1, ry1 - ry0);
    }

    const spanX = rx1 - rx0;
    const spanY = ry1 - ry0;
    const minGap = 8;
    const maxGapH = Math.min(110, Math.floor(spanY * 0.55));
    const maxGapV = Math.min(110, Math.floor(spanX * 0.55));

    // A pipe edge line must span most of the ROI (long, straight structure).
    const minRowSupport = spanX * 0.42;
    const minColSupport = spanY * 0.42;

    let best = { score: 0, axis: null, a: 0, b: 0, edgeSupport: 0, highlight: 0 };

    // Horizontal pipe: two long horizontal edges with a brighter band between
    for (let y1 = ry0; y1 < ry1 - minGap; y1++) {
      if (rowEdge[y1] < minRowSupport) continue;
      for (let y2 = y1 + minGap; y2 < Math.min(ry1, y1 + maxGapH); y2++) {
        if (rowEdge[y2] < minRowSupport) continue;
        let inside = 0, n = 0;
        for (let y = y1 + 1; y < y2; y++) { inside += rowLum[y]; n++; }
        const insideLum = n ? inside / n : 0;
        let outside = 0, m = 0;
        for (let y = ry0; y < ry1; y++) {
          if (y > y1 && y < y2) continue;
          outside += rowLum[y]; m++;
        }
        const outsideLum = m ? outside / m : 0;
        const highlight = outsideLum > 0 ? insideLum / outsideLum : 0;
        const edgeSupport = Math.min(rowEdge[y1], rowEdge[y2]) / spanX;
        const score = edgeSupport * Math.min(1.6, highlight);
        if (score > best.score) {
          best = { score, axis: "horizontal", a: y1, b: y2, edgeSupport, highlight };
        }
      }
    }

    // Vertical pipe: same test rotated
    for (let x1 = rx0; x1 < rx1 - minGap; x1++) {
      if (colEdge[x1] < minColSupport) continue;
      for (let x2 = x1 + minGap; x2 < Math.min(rx1, x1 + maxGapV); x2++) {
        if (colEdge[x2] < minColSupport) continue;
        let inside = 0, n = 0;
        for (let x = x1 + 1; x < x2; x++) { inside += colLum[x]; n++; }
        const insideLum = n ? inside / n : 0;
        let outside = 0, m = 0;
        for (let x = rx0; x < rx1; x++) {
          if (x > x1 && x < x2) continue;
          outside += colLum[x]; m++;
        }
        const outsideLum = m ? outside / m : 0;
        const highlight = outsideLum > 0 ? insideLum / outsideLum : 0;
        const edgeSupport = Math.min(colEdge[x1], colEdge[x2]) / spanY;
        const score = edgeSupport * Math.min(1.6, highlight);
        if (score > best.score) {
          best = { score, axis: "vertical", a: x1, b: x2, edgeSupport, highlight };
        }
      }
    }

    // Require both long parallel edges AND a brighter band between them.
    const MIN_SCORE = 0.86;
    if (best.score < MIN_SCORE || best.edgeSupport < 0.42 || best.highlight < 1.06) {
      return {
        ...empty,
        reason: best.edgeSupport >= 0.42
          ? "Parallel edges found but no cylindrical pipe profile"
          : "No long parallel pipe edges found",
        signals: {
          edgeSupport: Math.round(best.edgeSupport * 100) / 100,
          parallelSupport: 0,
          highlightStrength: Math.round(best.highlight * 100) / 100
        }
      };
    }

    // Pipe region = the band between the two edges, full span on the other axis
    let regionNorm;
    if (best.axis === "horizontal") {
      regionNorm = [rx0 / W, best.a / H, spanX / W, Math.max(6, best.b - best.a) / H];
    } else {
      regionNorm = [best.a / W, ry0 / H, Math.max(6, best.b - best.a) / W, spanY / H];
    }

    return {
      pipeVisible: true,
      confidence: Math.round(Math.min(0.99, best.score / 1.6) * 100) / 100,
      region: regionNorm,
      axis: best.axis,
      reason: "Pipe-like cylindrical structure detected",
      signals: {
        edgeSupport: Math.round(best.edgeSupport * 100) / 100,
        parallelSupport: Math.round(Math.min(1, best.highlight / 1.6) * 100) / 100,
        highlightStrength: Math.round(best.highlight * 100) / 100
      }
    };
  } catch (err) {
    return { ...empty, reason: `Pipe context analysis failed: ${err.message}` };
  }
}

export function detectPipeLeakage(sourceElem, options = {}) {
  const source = options.source || "UPLOADED_IMAGE";

  if (!sourceElem) {
    liveCameraLeakageConsecutiveFrames = 0;
    return {
      detected: false,
      confidence: 0,
      type: "VISIBLE_PIPE_LEAKAGE",
      evidence_region: null,
      source,
      status: "UNABLE_TO_INSPECT",
      qualityCheck: { passed: false, reason: "No image or video element provided" },
      details: "Unable to inspect: source element missing."
    };
  }

  try {
    const width = sourceElem.width || sourceElem.videoWidth || sourceElem.naturalWidth || 640;
    const height = sourceElem.height || sourceElem.videoHeight || sourceElem.naturalHeight || 360;

    if (width === 0 || height === 0) {
      liveCameraLeakageConsecutiveFrames = 0;
      return {
        detected: false,
        confidence: 0,
        type: "VISIBLE_PIPE_LEAKAGE",
        evidence_region: null,
        source,
        status: "UNABLE_TO_INSPECT",
        qualityCheck: { passed: false, reason: "Image width or height is 0" },
        details: "Unable to inspect: invalid image dimensions."
      };
    }

    // Prepare analysis canvas
    const sampleWidth = 320;
    const sampleHeight = Math.round((height / width) * 320) || 180;
    const analysisCanvas = document.createElement("canvas");
    analysisCanvas.width = sampleWidth;
    analysisCanvas.height = sampleHeight;
    const ctx = analysisCanvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(sourceElem, 0, 0, sampleWidth, sampleHeight);

    // 1. IMAGE QUALITY PRE-CHECK
    const quality = analyzeFrameQuality(analysisCanvas, ctx);
    if (!quality.isAssessable) {
      liveCameraLeakageConsecutiveFrames = 0;
      return {
        detected: false,
        confidence: 0,
        type: "VISIBLE_PIPE_LEAKAGE",
        evidence_region: null,
        source,
        status: "UNABLE_TO_INSPECT",
        qualityCheck: {
          passed: false,
          reason: quality.reason,
          brightness: quality.brightness,
          blurScore: quality.blurScore
        },
        details: `Unable to reliably inspect image (${quality.reason}).`
      };
    }

    // 2. PIPE CONTEXT VALIDATION
    // A pipe-related verdict is only admissible when an actual pipe-like
    // structure is visible. Without pipe context we must not claim a pipe leak,
    // no matter what the pixel statistics look like.
    const pipeCtx = detectPipeContext(sourceElem, { region: options.pipeRegion });

    if (!pipeCtx.pipeVisible) {
      liveCameraLeakageConsecutiveFrames = 0;
      return {
        detected: false,
        confidence: 0,
        type: "VISIBLE_PIPE_LEAKAGE",
        evidence_region: null,
        pipe_context: pipeCtx,
        pipe_detected: false,
        source,
        status: "NO_PIPE_DETECTED",
        qualityCheck: {
          passed: true,
          reason: "OK",
          brightness: quality.brightness,
          blurScore: quality.blurScore
        },
        details: "PIPE NOT VISIBLE – CHECK CAMERA POSITION. No pipe-like structure was found in this view, so no pipe leakage can be reported.",
        operator_message: "PIPE NOT VISIBLE – CHECK CAMERA POSITION"
      };
    }

    // 3. VISUAL WATER SPRAY / LIQUID JET PATTERN COMPUTER VISION ANALYSIS
    const imgData = ctx.getImageData(0, 0, sampleWidth, sampleHeight);
    const data = imgData.data;

    // Restrict spray analysis to the identified pipe region AND its immediate
    // surroundings. A leak originates at the pipe or just outside it, so a
    // margin is included to capture the escaping jet, while bright or textured
    // areas elsewhere in the scene (clothing, walls, furniture) can never be
    // attributed to the pipe.
    const pr = pipeCtx.region || [0, 0, 1, 1];
    const pipeLeft = pr[0] * sampleWidth;
    const pipeTop = pr[1] * sampleHeight;
    const pipeRight = (pr[0] + pr[2]) * sampleWidth;
    const pipeBottom = (pr[1] + pr[3]) * sampleHeight;
    const bandHeight = Math.max(1, pipeBottom - pipeTop);
    const bandWidth = Math.max(1, pipeRight - pipeLeft);
    // An escaping jet extends outwards from the pipe surface, so the search band
    // covers the pipe plus a generous margin around it. Distant scene regions
    // (clothing, walls, furniture) remain excluded.
    const marginY = Math.max(24, bandHeight * 1.5);
    const marginX = Math.max(16, bandWidth * 0.2);

    const px0 = Math.max(1, Math.floor(pipeLeft - marginX));
    const py0 = Math.max(1, Math.floor(pipeTop - marginY));
    const px1 = Math.min(sampleWidth - 1, Math.ceil(pipeRight + marginX));
    const py1 = Math.min(sampleHeight - 1, Math.ceil(pipeBottom + marginY));
    const regionPixelCount = Math.max(1, (px1 - px0) * (py1 - py0));

    let detectedSprayPixels = 0;
    const gridCols = 16;
    const gridRows = 12;
    const cellW = sampleWidth / gridCols;
    const cellH = sampleHeight / gridRows;
    const gridSprayDensity = new Float32Array(gridCols * gridRows);

    let minX = sampleWidth, minY = sampleHeight, maxX = 0, maxY = 0;

    // Pre-compute luminance for the whole sample so a local-contrast test can
    // distinguish small-scale spray droplets from a smooth specular highlight.
    // A clean glossy pipe has a large, contiguous bright band; genuine spray is
    // made of many small bright features surrounded by darker pixels.
    const lumMap = new Float32Array(sampleWidth * sampleHeight);
    for (let i = 0, p = 0; i < data.length; i += 4, p++) {
      lumMap[p] = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    }

    const NEIGHBOUR_RADIUS = 2;
    // A spray droplet is a small bright feature on a darker background. A value
    // of 0.6 still rejects smooth bright regions (a specular highlight or a lit
    // pipe body has a local bright ratio near 1.0) while accepting genuine
    // scattered droplets.
    const LOCAL_BRIGHT_LIMIT = 0.6;

    for (let y = py0; y < py1; y++) {
      for (let x = px0; x < px1; x++) {
        const p = y * sampleWidth + x;
        const idx = p * 4;
        const r = data[idx];
        const g = data[idx + 1];
        const b = data[idx + 2];

        const lum = lumMap[p];
        if (lum <= 135) continue;

        // 4-neighbor spatial luminance gradient
        const lumLeft = lumMap[p - 1];
        const lumRight = lumMap[p + 1];
        const lumUp = lumMap[p - sampleWidth];
        const lumDown = lumMap[p + sampleWidth];
        const gradMag = Math.abs(lumRight - lumLeft) + Math.abs(lumDown - lumUp);
        if (gradMag <= 20) continue;

        // Neutral colour only (rules out strongly coloured objects)
        if (Math.abs(r - g) >= 35 || Math.abs(g - b) >= 35) continue;

        // Local-contrast test: a spray droplet is a small bright feature on a
        // darker background. If most of the neighbourhood is also bright, this
        // is a smooth highlight / surface, not spray.
        let brightNeighbours = 0;
        let samples = 0;
        for (let dy = -NEIGHBOUR_RADIUS; dy <= NEIGHBOUR_RADIUS; dy++) {
          const yy = y + dy;
          if (yy < py0 || yy >= py1) continue;
          for (let dx = -NEIGHBOUR_RADIUS; dx <= NEIGHBOUR_RADIUS; dx++) {
            const xx = x + dx;
            if (xx < px0 || xx >= px1) continue;
            if (lumMap[yy * sampleWidth + xx] > 135) brightNeighbours++;
            samples++;
          }
        }
        const localBrightRatio = samples ? brightNeighbours / samples : 0;
        if (localBrightRatio >= LOCAL_BRIGHT_LIMIT) continue;

        // Accepted as a spray/mist candidate
        detectedSprayPixels++;
        minX = Math.min(minX, x);
        minY = Math.min(minY, y);
        maxX = Math.max(maxX, x);
        maxY = Math.max(maxY, y);

        const cCol = Math.floor(x / cellW);
        const cRow = Math.floor(y / cellH);
        if (cCol >= 0 && cCol < gridCols && cRow >= 0 && cRow < gridRows) {
          gridSprayDensity[cRow * gridCols + cCol]++;
        }
      }
    }

    // Evaluate spatial spray clustering and directional jet patterns
    let activeSprayCells = 0;
    let maxCellDensity = 0;
    for (let i = 0; i < gridCols * gridRows; i++) {
      if (gridSprayDensity[i] > 6) {
        activeSprayCells++;
        if (gridSprayDensity[i] > maxCellDensity) maxCellDensity = gridSprayDensity[i];
      }
    }

    // Density is measured against the pipe region plus margin, not the whole frame.
    const sprayRatio = detectedSprayPixels / regionPixelCount;

    // A spray/leak jet is a CONCENTRATED cluster of small bright features, so the
    // decision is driven by concentration (active cells and peak cell density)
    // rather than by the fraction of the whole search band covered. The ratio is
    // kept only as a sanity floor so a handful of stray pixels cannot trigger it.
    const MIN_SPRAY_RATIO = 0.002;
    const MIN_ACTIVE_CELLS = 3;
    const MIN_PEAK_DENSITY = 12;

    const frameHasSprayPattern = (
      sprayRatio >= MIN_SPRAY_RATIO &&
      activeSprayCells >= MIN_ACTIVE_CELLS &&
      maxCellDensity >= MIN_PEAK_DENSITY
    );

    if (source === "LIVE_CAMERA") {
      if (frameHasSprayPattern) {
        liveCameraLeakageConsecutiveFrames++;
      } else {
        liveCameraLeakageConsecutiveFrames = Math.max(0, liveCameraLeakageConsecutiveFrames - 1);
      }
    }

    const isConfirmedLeakage = source === "LIVE_CAMERA"
      ? (frameHasSprayPattern && liveCameraLeakageConsecutiveFrames >= 4)
      : frameHasSprayPattern;

    if (isConfirmedLeakage) {
      // Calculate normalized evidence region bounding box [normX, normY, normW, normH] (in 0..1 scale)
      const normX = Math.max(0, Math.min(1, (minX - 10) / sampleWidth));
      const normY = Math.max(0, Math.min(1, (minY - 10) / sampleHeight));
      const normW = Math.max(0.20, Math.min(1 - normX, (maxX - minX + 20) / sampleWidth));
      const normH = Math.max(0.20, Math.min(1 - normY, (maxY - minY + 20) / sampleHeight));

      // Confidence is COMPUTED from measurable evidence and deliberately capped.
      // This is a pixel heuristic, not a trained model, so it must never present
      // a trained-model style 0.85+ probability for a "confirmed" defect.
      const spraySignal = Math.min(1, sprayRatio / 0.06);
      const clusterSignal = Math.min(1, activeSprayCells / 8);
      const pipeSignal = Math.min(1, pipeCtx.confidence);
      const rawConfidence = 0.35 + 0.25 * spraySignal + 0.20 * clusterSignal + 0.15 * pipeSignal;
      const confidence = Math.round(Math.min(0.75, Math.max(0.35, rawConfidence)) * 100) / 100;

      return {
        detected: true,
        confidence,
        confidence_source: "PIXEL_HEURISTIC_NOT_A_TRAINED_MODEL",
        type: "VISIBLE_PIPE_LEAKAGE",
        evidence_region: [normX, normY, normW, normH],
        pipe_context: pipeCtx,
        pipe_detected: true,
        source,
        status: "REVIEW_REQUIRED",
        qualityCheck: {
          passed: true,
          reason: "OK",
          brightness: quality.brightness,
          blurScore: quality.blurScore
        },
        details: "Possible liquid spray pattern inside the detected pipe region. Unconfirmed heuristic result – human verification required.",
        operator_message: "POSSIBLE PIPE LEAKAGE – HUMAN REVIEW REQUIRED"
      };
    }

    // Pipe is visible but no spray pattern inside it.
    return {
      detected: false,
      confidence: 0,
      type: "VISIBLE_PIPE_LEAKAGE",
      evidence_region: null,
      pipe_context: pipeCtx,
      pipe_detected: true,
      source,
      status: "NO_LEAKAGE",
      qualityCheck: {
        passed: true,
        reason: "OK",
        brightness: quality.brightness,
        blurScore: quality.blurScore
      },
      details: "Pipe structure visible; no liquid spray pattern detected inside the pipe region.",
      operator_message: "PIPE VISIBLE – NO LEAKAGE DETECTED",
      diagnostics: {
        sprayPixels: detectedSprayPixels,
        sprayRatio: Math.round(sprayRatio * 10000) / 10000,
        activeSprayCells,
        maxCellDensity,
        regionPixels: regionPixelCount
      }
    };

    // No leakage pattern detected
    return {
      detected: false,
      confidence: 0,
      type: "VISIBLE_PIPE_LEAKAGE",
      evidence_region: null,
      source,
      status: "NO_LEAKAGE",
      qualityCheck: {
        passed: true,
        reason: "OK",
        brightness: quality.brightness,
        blurScore: quality.blurScore
      },
      details: "No visible pipe leakage detected in current frame."
    };

  } catch (err) {
    console.error("Leakage detection error:", err);
    liveCameraLeakageConsecutiveFrames = 0;
    return {
      detected: false,
      confidence: 0,
      type: "VISIBLE_PIPE_LEAKAGE",
      evidence_region: null,
      source,
      status: "UNABLE_TO_INSPECT",
      qualityCheck: { passed: false, reason: `Processing error: ${err.message}` },
      details: `Unable to inspect image: ${err.message}`
    };
  }
}

// ============================================================================
// PART 5: Main Real-Time Frame Inference & Tracking Engine (`detectObjectsAndMobilePhone`)
// ============================================================================

/**
 * PART 5: Main Real-Time Frame Inference & Tracking Engine
 * Runs TensorFlow COCO-SSD for person detection + backend YOLOv8 COCO phone detection + helmet inspection.
 * 
 * Final Flow:
 * Camera/Upload -> YOLOv8 COCO phone detection -> optional spatial association; phone use is not classified.
 */
export async function detectObjectsAndMobilePhone(videoOrCanvas, config = {}) {
  const minConfidence = config.minConfidence || 0.35;
  const phoneThreshold = config.phoneThreshold !== undefined ? config.phoneThreshold : 0.15;
  const phoneAssociationDistance = config.phoneAssociationDistance || 0.65;

  let yoloStatus = getPhoneDetectionBackendStatus();

  if (!videoOrCanvas) {
    return {
      isModelLoaded: modelStatus === "READY",
      modelStatus: modelStatus === "READY" ? "READY" : "NO LIVE INPUT AVAILABLE",
      phoneDetectorStatus: yoloStatus.statusText,
      phoneDetectorIsActive: yoloStatus.isActive,
      phoneDetectorModelName: PHONE_DETECTOR_MODEL_NAME,
      personCount: 0,
      persons: [],
      phones: [],
      rawPredictionsCount: 0,
      rawDetectionsSummary: [],
      phoneCandidates: [],
      phoneMisuseEvent: null
    };
  }

  // Ensure COCO-SSD model is loaded FOR PERSON DETECTION ONLY
  if (modelStatus !== "READY") {
    const loadRes = await loadDetectionModel();
    if (!loadRes.success) {
      return {
        isModelLoaded: false,
        modelStatus: "MODEL NOT AVAILABLE",
        modelErrorMessage,
        phoneDetectorStatus: yoloStatus.statusText,
        phoneDetectorIsActive: yoloStatus.isActive,
        phoneDetectorModelName: PHONE_DETECTOR_MODEL_NAME,
        personCount: 0,
        persons: [],
        phones: [],
        rawPredictionsCount: 0,
        rawDetectionsSummary: [],
        phoneCandidates: [],
        phoneMisuseEvent: null
      };
    }
  }

  try {
    const width = videoOrCanvas.width || videoOrCanvas.videoWidth || videoOrCanvas.naturalWidth || 640;
    const height = videoOrCanvas.height || videoOrCanvas.videoHeight || videoOrCanvas.naturalHeight || 360;

    if (width === 0 || height === 0) {
      return {
        isModelLoaded: true,
        modelStatus: "NOT ASSESSABLE",
        phoneDetectorStatus: yoloStatus.statusText,
        phoneDetectorIsActive: yoloStatus.isActive,
        phoneDetectorModelName: PHONE_DETECTOR_MODEL_NAME,
        personCount: 0,
        persons: [],
        phones: [],
        rawPredictionsCount: 0,
        rawDetectionsSummary: [],
        phoneCandidates: [],
        phoneMisuseEvent: null
      };
    }

    // 1. Run COCO-SSD ONLY for Person Detection (class === 'person')
    const rawPredictions = await cocoModel.detect(videoOrCanvas, 25, 0.15);
    const personDetections = rawPredictions.filter(
      (p) => p.class === "person" && p.score >= minConfidence
    );

    // COCO-SSD is used for person detection only; phone detections come from backend YOLOv8 COCO.
    const yoloPhoneRes = await detectPhoneWithBackend(videoOrCanvas, { phoneThreshold });
    yoloStatus = getPhoneDetectionBackendStatus();

    const yoloPhoneDetections = yoloPhoneRes.phones || [];

    const rawDetectionsSummary = [
      ...personDetections.map((p) => ({
        class: p.class,
        score: Math.round(p.score * 100) / 100,
        bbox: p.bbox
      })),
      ...yoloPhoneDetections.map((p) => ({
        class: p.class,
        score: p.confidence,
        bbox: p.pixelBbox || p.bbox
      }))
    ];

    // Do not report the phone detector as active unless the backend completed inference.
    if (!yoloPhoneRes.success || !yoloStatus.isActive) {
      console.warn("Phone detector backend is offline; no fallback detections will be generated.");
    }

    // STRICT RULE: If 0 persons detected by AI, count is strictly 0
    if (personDetections.length === 0) {
      const now = Date.now();
      activeTracks = activeTracks.filter((track) => now - track.lastSeen <= TRACK_RETENTION_MS);
      stopSirenAlarm();

      return {
        isModelLoaded: true,
        modelStatus: "READY",
        faceRecognitionStatus: getFaceRecognitionStatus(),
        phoneDetectorStatus: yoloStatus.statusText,
        phoneDetectorIsActive: yoloStatus.isActive,
        phoneDetectorModelName: PHONE_DETECTOR_MODEL_NAME,
        personCount: 0,
        persons: [],
        phones: yoloPhoneDetections.map((p) => ({
          box: p.bbox,
          confidence: p.confidence,
          class: p.class,
          situation: p.situation
        })),
        rawPredictionsCount: rawPredictions.length,
        rawPredictions: rawPredictions,
        rawDetectionsSummary,
        phoneCandidates: yoloPhoneDetections.map((p) => ({
          class: p.class,
          score: p.confidence,
          status: "UNASSOCIATED",
          reason: `Phone detected by backend YOLOv8 COCO (${p.class})`
        })),
        phoneMisuseEvent: null
      };
    }

    // 3. Multi-Person Tracking Across Frames
    const now = Date.now();
    const updatedPersons = [];
    const normalizedDetections = personDetections.map((det) => {
      const box = [
        Math.max(0, det.bbox[0] / width),
        Math.max(0, det.bbox[1] / height),
        Math.min(1, det.bbox[2] / width),
        Math.min(1, det.bbox[3] / height)
      ];
      return {
        det,
        box,
        center: [box[0] + box[2] / 2, box[1] + box[3] / 2]
      };
    });

    const availableTracks = activeTracks.filter(
      (track) => now - track.lastSeen <= TRACK_RETENTION_MS
    );
    const trackCandidates = [];
    normalizedDetections.forEach(({ box, center }, detectionIndex) => {
      availableTracks.forEach((track) => {
        const distance = Math.hypot(track.center[0] - center[0], track.center[1] - center[1]);
        const overlap = calculateIoU(track.box, box);
        const maxDistance = Math.min(0.24, Math.max(0.12, Math.sqrt(box[2] * box[3]) * 0.8));

        if (overlap >= 0.05 || distance <= maxDistance) {
          trackCandidates.push({
            detectionIndex,
            track,
            score: distance - overlap * 0.2 + (now - track.lastSeen) / TRACK_RETENTION_MS * 0.03
          });
        }
      });
    });

    trackCandidates.sort((a, b) => a.score - b.score);
    const trackByDetection = new Map();
    const assignedTrackIds = new Set();
    trackCandidates.forEach(({ detectionIndex, track }) => {
      if (!trackByDetection.has(detectionIndex) && !assignedTrackIds.has(track.trackId)) {
        trackByDetection.set(detectionIndex, track);
        assignedTrackIds.add(track.trackId);
      }
    });
    const currentTracks = [];

    for (const [detectionIndex, { det, box: normBox, center }] of normalizedDetections.entries()) {
      const matchedTrack = trackByDetection.get(detectionIndex);

      let trackId;
      if (matchedTrack) {
        trackId = matchedTrack.trackId;
        matchedTrack.box = normBox;
        matchedTrack.center = center;
        matchedTrack.lastSeen = now;
        currentTracks.push(matchedTrack);
      } else {
        trackId = `TRK-${TRACK_SESSION_ID}-${nextTrackId++}`;
        const newTrack = {
          trackId,
          box: normBox,
          center,
          lastSeen: now,
          faceCheckedAt: 0,
          matchedPersonnelId: null,
          faceMatchDistance: null
        };
        currentTracks.push(newTrack);
      }

      const registeredWithFaces = (config.registeredPersonnel || []).filter(
        (person) => Array.isArray(person.face_descriptors) && person.face_descriptors.length > 0
      );
      const currentTrack = currentTracks[currentTracks.length - 1];
      if (
        registeredWithFaces.length > 0
        && getFaceRecognitionStatus().status !== "ERROR"
        && now - (currentTrack.faceCheckedAt || 0) >= 1000
      ) {
        currentTrack.faceCheckedAt = now;
        try {
          const faceDescriptor = await captureFaceDescriptor(videoOrCanvas, normBox);
          const faceMatch = matchRegisteredFace(faceDescriptor, registeredWithFaces);
          currentTrack.matchedPersonnelId = faceMatch?.personnelId || null;
          currentTrack.faceMatchDistance = faceMatch?.distance ?? null;
        } catch (error) {
          const faceStatus = getFaceRecognitionStatus();
          if (faceStatus.status === "ERROR" && !faceRecognitionFailureReported) {
            console.error(faceStatus.error || error.message);
            faceRecognitionFailureReported = true;
          }
        }
      }

      // Helmet check
      if (!helmetVerificationState.has(trackId)) {
        helmetVerificationState.set(trackId, {
          hasHelmet: null,
          confidence: 0,
          persistenceCount: 0,
          missGraceCount: HELMET_MISS_GRACE_FRAMES,
          lastSeen: now,
          reason: "Initializing ML helmet verification"
        });
      }

      const hState = helmetVerificationState.get(trackId);
      const mlHelmetRes = await detectHelmetForPerson(videoOrCanvas, normBox, config);

      if (mlHelmetRes.hasHelmet === null) {
        hState.hasHelmet = null;
        hState.confidence = 0;
        hState.persistenceCount = 0;
        hState.reason = mlHelmetRes.reason;
      } else if (mlHelmetRes.hasHelmet) {
        hState.persistenceCount += 1;
        hState.missGraceCount = HELMET_MISS_GRACE_FRAMES;
        hState.lastSeen = now;
        hState.confidence = mlHelmetRes.confidence;
        hState.reason = mlHelmetRes.reason;
        hState.hasHelmet = true;
      } else {
        if (hState.persistenceCount >= HELMET_PERSISTENCE_FRAMES && hState.missGraceCount > 0) {
          hState.missGraceCount -= 1;
          hState.lastSeen = now;
        } else {
          hState.persistenceCount = 0;
          hState.confidence = mlHelmetRes.confidence;
          hState.reason = mlHelmetRes.reason;
          hState.hasHelmet = false;
        }
      }

      const verifiedHelmet = mlHelmetRes.hasHelmet === null
        ? null
        : hState.persistenceCount >= HELMET_PERSISTENCE_FRAMES && hState.missGraceCount > 0;
      hState.hasHelmet = verifiedHelmet;

      updatedPersons.push({
        trackId: trackId,
        id: trackId,
        matchedPersonnelId: currentTrack.matchedPersonnelId,
        faceMatchDistance: currentTrack.faceMatchDistance,
        box: normBox,
        confidence: Math.round(det.score * 100) / 100,
        hasHelmet: verifiedHelmet,
        helmetConfidence: hState.confidence,
        helmetReason: hState.reason,
        label: verifiedHelmet === null
          ? `${trackId} | Helmet status unavailable`
          : verifiedHelmet
            ? `${trackId} | Helmet OK (${Math.round(hState.confidence * 100)}%)`
            : `${trackId} | NO HELMET`,
        color: verifiedHelmet === null ? "#94a3b8" : verifiedHelmet ? "#10b981" : "#ef4444",
        isUsingPhone: false,
        associatedPhone: null
      });
    }

    const retainedTracks = availableTracks.filter((track) => !assignedTrackIds.has(track.trackId));
    activeTracks = [...currentTracks, ...retainedTracks];

    // 4. Associate detected phones with nearby people; this does not classify hand, ear, or desk use.
    const validPhoneCandidates = [];
    const normalizedPhones = [];
    const candidatePairings = [];

    yoloPhoneDetections.forEach((phoneDet, pIdx) => {
      const score = phoneDet.confidence;
      const pNormBox = phoneDet.bbox;
      const pCenter = [pNormBox[0] + pNormBox[2] / 2, pNormBox[1] + pNormBox[3] / 2];

      if (score < phoneThreshold) {
        validPhoneCandidates.push({
          class: phoneDet.class,
          score: Math.round(score * 100) / 100,
          status: "REJECTED",
          reason: `YOLOv8 confidence (${Math.round(score * 100)}%) below threshold (${Math.round(phoneThreshold * 100)}%)`
        });
        return;
      }

      let foundAssociation = false;
      updatedPersons.forEach((person, personIdx) => {
        const pBox = person.box;

        const expX1 = pBox[0] - pBox[2] * 1.0;
        const expY1 = pBox[1] - pBox[3] * 0.60;
        const expX2 = pBox[0] + pBox[2] * 2.2;
        const expY2 = pBox[1] + pBox[3] * 1.5;

        const isCenterInside =
          pCenter[0] >= expX1 &&
          pCenter[0] <= expX2 &&
          pCenter[1] >= expY1 &&
          pCenter[1] <= expY2;

        const upperBodyCenter = [pBox[0] + pBox[2] / 2, pBox[1] + pBox[3] * 0.35];
        const distToUpperBody = Math.sqrt(
          Math.pow(pCenter[0] - upperBodyCenter[0], 2) +
          Math.pow(pCenter[1] - upperBodyCenter[1], 2)
        );

        const maxAllowedDist = pBox[3] * Math.max(0.85, phoneAssociationDistance);

        if (isCenterInside || distToUpperBody <= maxAllowedDist) {
          foundAssociation = true;
          candidatePairings.push({
            phoneIdx: pIdx,
            personIdx: personIdx,
            distance: distToUpperBody,
            pNormBox,
            phoneScore: score,
            phoneClass: phoneDet.class,
            situation: phoneDet.situation
          });
        }
      });

      if (!foundAssociation) {
        validPhoneCandidates.push({
          class: phoneDet.class,
          score: Math.round(score * 100) / 100,
          status: "UNASSOCIATED",
          reason: "Phone detected, but no nearby person was identified; usage context is unknown."
        });
      }
    });

    candidatePairings.sort((a, b) => a.distance - b.distance);

    const assignedPhoneIndices = new Set();
    const assignedPersonIndices = new Set();

    candidatePairings.forEach((pair) => {
      if (!assignedPhoneIndices.has(pair.phoneIdx) && !assignedPersonIndices.has(pair.personIdx)) {
        assignedPhoneIndices.add(pair.phoneIdx);
        assignedPersonIndices.add(pair.personIdx);

        const targetPerson = updatedPersons[pair.personIdx];
        targetPerson.isUsingPhone = true;
        targetPerson.associatedPhone = {
          box: pair.pNormBox,
          confidence: Math.round(pair.phoneScore * 100) / 100,
          class: pair.phoneClass,
          situation: pair.situation
        };

        normalizedPhones.push({
          box: pair.pNormBox,
          confidence: Math.round(pair.phoneScore * 100) / 100,
          class: pair.phoneClass,
          situation: pair.situation,
          associatedTrackId: targetPerson.trackId
        });

        console.log(
          `[PHONE DETECTED NEAR PERSON]: Class '${pair.phoneClass}' (Conf: ${Math.round(pair.phoneScore * 100)}%) -> Associated with ${targetPerson.trackId}`
        );

        validPhoneCandidates.push({
          class: pair.phoneClass,
          score: Math.round(pair.phoneScore * 100) / 100,
          status: "PHONE_NEAR_PERSON",
          associatedTrackId: targetPerson.trackId,
          associationDistance: Math.round(pair.distance * 100) / 100,
          reason: `A cell phone was detected near ${targetPerson.trackId}; hand-held use is unverified.`
        });
      }
    });

    const potentialPhoneUse = normalizedPhones.find((phone) => phone.associatedTrackId);
    const currentActivePhoneEvent = potentialPhoneUse
      ? {
          phoneDetected: true,
          associatedTrackId: potentialPhoneUse.associatedTrackId,
          phoneConfidence: potentialPhoneUse.confidence,
          warningLevel: 1,
          sirenActive: false,
          verifiedUse: false,
          warningMessage: `Potential phone use: cell phone detected near ${potentialPhoneUse.associatedTrackId}. Verify manually; hand-held use or a call is not confirmed.`
        }
      : null;

    return {
      isModelLoaded: true,
      modelStatus: "READY",
      faceRecognitionStatus: getFaceRecognitionStatus(),
      phoneDetectorStatus: yoloStatus.statusText,
      phoneDetectorIsActive: yoloStatus.isActive,
      phoneDetectorModelName: PHONE_DETECTOR_MODEL_NAME,
      personCount: updatedPersons.length,
      persons: updatedPersons,
      phones: normalizedPhones,
      rawPredictionsCount: rawPredictions.length,
      rawPredictions: rawPredictions,
      rawDetectionsSummary,
      phoneCandidates: validPhoneCandidates,
      phoneMisuseEvent: currentActivePhoneEvent
    };

  } catch (err) {
    console.error("Inference Error:", err);
    return {
      isModelLoaded: true,
      modelStatus: `Inference Error: ${err.message}`,
      faceRecognitionStatus: getFaceRecognitionStatus(),
      phoneDetectorStatus: yoloStatus.statusText,
      phoneDetectorIsActive: yoloStatus.isActive,
      phoneDetectorModelName: PHONE_DETECTOR_MODEL_NAME,
      personCount: 0,
      persons: [],
      phones: [],
      rawPredictionsCount: 0,
      rawDetectionsSummary: [],
      phoneCandidates: [],
      phoneMisuseEvent: null
    };
  }
}
