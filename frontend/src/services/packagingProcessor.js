/**
 * Modular Industrial Packaging Quality Inspection Engine
 * 
 * Demo-only packaging inspection simulator. It has no camera, trained inspection model,
 * physical conveyor, or reject hardware connected.
 */

export const CAP_STATUS = {
  PASS: "PASS",
  MISSING: "MISSING",
  MISALIGNED: "MISALIGNED",
  DAMAGED: "DAMAGED"
};

export const FILL_LEVEL_STATUS = {
  PASS: "PASS",
  LOW: "LOW FILL",
  HIGH: "HIGH FILL"
};

export const BOTTLE_BODY_STATUS = {
  PASS: "PASS",
  SURFACE_DEFECT: "SURFACE DEFECT",
  SHAPE_ANOMALY: "SHAPE ANOMALY"
};

export const OVERALL_RESULT = {
  PASS: "PASS",
  REJECT: "REJECT"
};

let bottleCounter = 1;

/**
 * Generate a demo-only sample result; this is not a measurement from a real bottle.
 */
export function inspectBottle(forceDefectType = null) {
  const bottleId = `DEMO-${String(bottleCounter++).padStart(4, "0")}`;
  const time = new Date().toLocaleTimeString();

  let fillPercentage = Math.floor(Math.random() * 8) + 88; // 88% - 95% (Normal range)
  let fillStatus = FILL_LEVEL_STATUS.PASS;
  let capStatus = CAP_STATUS.PASS;
  let bottleStatus = BOTTLE_BODY_STATUS.PASS;

  // Decide if this bottle has a defect (~15% baseline defect rate for realistic testing)
  const isDefective = forceDefectType || Math.random() < 0.15;

  if (isDefective) {
    const defectChoice = forceDefectType || (Math.random() < 0.4 ? "LOW_FILL" : Math.random() < 0.7 ? "CAP" : "BODY");

    if (defectChoice === "LOW_FILL") {
      fillPercentage = Math.floor(Math.random() * 15) + 62; // 62% - 77% (Low fill)
      fillStatus = FILL_LEVEL_STATUS.LOW;
    } else if (defectChoice === "HIGH_FILL") {
      fillPercentage = Math.floor(Math.random() * 4) + 98; // 98% - 100% (High fill)
      fillStatus = FILL_LEVEL_STATUS.HIGH;
    } else if (defectChoice === "CAP") {
      const capSubChoices = [CAP_STATUS.MISSING, CAP_STATUS.MISALIGNED, CAP_STATUS.DAMAGED];
      capStatus = capSubChoices[Math.floor(Math.random() * capSubChoices.length)];
    } else if (defectChoice === "BODY") {
      bottleStatus = Math.random() < 0.6 ? BOTTLE_BODY_STATUS.SURFACE_DEFECT : BOTTLE_BODY_STATUS.SHAPE_ANOMALY;
    }
  }

  // OVERALL QUALITY DECISION
  const overallResult =
    fillStatus === FILL_LEVEL_STATUS.PASS &&
    capStatus === CAP_STATUS.PASS &&
    bottleStatus === BOTTLE_BODY_STATUS.PASS
      ? OVERALL_RESULT.PASS
      : OVERALL_RESULT.REJECT;

  return {
    id: bottleId,
    time,
    fillPercentage,
    fillStatus,
    capStatus,
    bottleStatus,
    overallResult,
    confidence: null,
    simulatedReject: overallResult === OVERALL_RESULT.REJECT
  };
}

/**
 * Get initial state for Packaging Dashboard
 */
export function getInitialPackagingState() {
  return {
    conveyorStatus: "SIMULATION",
    conveyorSpeedHz: null,
    cameraStatus: "NOT CONNECTED",
    aiModelStatus: "NO INSPECTION MODEL",
    isHardwareConnected: false,
    totalInspected: 0,
    totalPassed: 0,
    totalDefective: 0,
    defectBreakdown: {
      capDefects: 0,
      lowFillDefects: 0,
      bottleDefects: 0,
      otherDefects: 0
    },
    recentLogs: [],
    activeBottle: null
  };
}
