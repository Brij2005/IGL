import { detectYoloObjectsApi } from "./api";

let detectorStatus = "NOT_CHECKED";
let detectorErrorMessage = "";

export const MODEL_NAME = "YOLOv8 COCO (backend)";

export function getPhoneDetectionBackendStatus() {
  const isActive = detectorStatus === "ACTIVE";
  return {
    status: detectorStatus,
    isActive,
    statusText: isActive
      ? `Phone detector: ${MODEL_NAME} | ACTIVE`
      : detectorStatus === "OFFLINE"
        ? "Phone detector: backend offline"
        : "Phone detector: waiting for backend inference",
    shortStatusText: isActive ? "ACTIVE" : detectorStatus,
    modelName: MODEL_NAME,
    errorMessage: detectorErrorMessage
  };
}

export async function detectPhoneWithBackend(sourceElem, config = {}) {
  const startTime = Date.now();
  const threshold = config.phoneThreshold ?? 0.10;

  if (!sourceElem) {
    return {
      success: false,
      status: detectorStatus,
      phones: [],
      inferenceTimeMs: 0
    };
  }

  const srcW = sourceElem.videoWidth || sourceElem.naturalWidth || sourceElem.width;
  const srcH = sourceElem.videoHeight || sourceElem.naturalHeight || sourceElem.height;
  if (!srcW || !srcH) {
    return {
      success: false,
      status: detectorStatus,
      phones: [],
      inferenceTimeMs: Date.now() - startTime
    };
  }

  try {
    const canvas = document.createElement("canvas");
    canvas.width = srcW;
    canvas.height = srcH;
    const context = canvas.getContext("2d");
    if (!context) {
      throw new Error("Unable to create canvas context for phone detection.");
    }
    context.drawImage(sourceElem, 0, 0, srcW, srcH);

    const response = await detectYoloObjectsApi(canvas.toDataURL("image/jpeg", 0.9), threshold, 1280);
    if (!response || response.status !== "SUCCESS" || !Array.isArray(response.detections)) {
      detectorStatus = "OFFLINE";
      detectorErrorMessage = response?.message || "Backend phone detector did not return a successful inference.";
      return {
        success: false,
        status: detectorStatus,
        phones: [],
        inferenceTimeMs: Date.now() - startTime
      };
    }

    detectorStatus = "ACTIVE";
    detectorErrorMessage = "";

    const phones = response.detections
      .filter((detection) => {
        const className = String(detection.class_name || "").toLowerCase();
        return (
          (className === "cell phone" || className.includes("phone") || className.includes("mobile")) &&
          Number.isFinite(detection.confidence) &&
          detection.confidence >= threshold &&
          Array.isArray(detection.bbox) &&
          detection.bbox.length === 4 &&
          detection.bbox.every(Number.isFinite)
        );
      })
      .map((detection) => ({
        bbox: detection.bbox,
        pixelBbox: detection.pixel_bbox || [
          detection.bbox[0] * srcW,
          detection.bbox[1] * srcH,
          detection.bbox[2] * srcW,
          detection.bbox[3] * srcH
        ],
        score: detection.confidence,
        confidence: detection.confidence,
        class: "Cell phone",
        situation: "unknown",
        rawClassName: detection.class_name,
        rawClassId: detection.class_id
      }));

    return {
      success: true,
      status: detectorStatus,
      modelName: response.model_name || MODEL_NAME,
      phones,
      inferenceTimeMs: Date.now() - startTime
    };
  } catch (error) {
    detectorStatus = "OFFLINE";
    detectorErrorMessage = error instanceof Error ? error.message : String(error);
    console.error("Phone detector backend inference failed:", error);
    return {
      success: false,
      status: detectorStatus,
      phones: [],
      inferenceTimeMs: Date.now() - startTime
    };
  }
}
