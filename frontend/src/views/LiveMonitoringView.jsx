import React, { useRef, useEffect, useState } from "react";
import {
  MonitorPlay,
  ShieldAlert,
  Eye,
  EyeOff,
  Camera,
  Users,
  Info,
  ShieldCheck,
  Smartphone,
  Volume2,
  VolumeX,
  Sliders,
  AlertOctagon,
  CheckCircle2,
  Bug,
  Activity,
  FlipHorizontal,
  UserPlus,
  UserCheck,
  X,
  Upload,
  AlertCircle,
  RefreshCw
} from "lucide-react";
import { useSafety } from "../context/SafetyContext";
import {
  captureCanvasSnapshot,
  detectObjectsAndMobilePhone,
  loadDetectionModel,
  getModelStatus
} from "../services/realVisionProcessor";
import { captureFaceDescriptor, FACE_ENROLLMENT_SAMPLE_COUNT } from "../services/faceRecognition";

export default function LiveMonitoringView() {
  const {
    activeStream,
    streamSource,
    personCount,
    setPersonCount,
    streamMetrics,
    restrictedZones,
    dispatchAlert,
    setActiveTab,
    recordObservation,
    registeredPersonnel,
    registerPerson,
    updatePersonRegistration,
    connectWebcam,
    connectImageFile,
    connectVideoFile,
    disconnectStream
  } = useSafety();

  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const imageRef = useRef(null);

  // Input Mode: "camera" (Live Camera) vs "image" (Upload Image)
  const [inputMode, setInputMode] = useState("camera");
  const [cameraError, setCameraError] = useState(null);
  const [isConnectingCam, setIsConnectingCam] = useState(false);

  // Overlay Toggles
  const [showBoxes, setShowBoxes] = useState(true);
  const [showZones, setShowZones] = useState(false);
  const [showDistances, setShowDistances] = useState(true);
  const [isMirrored, setIsMirrored] = useState(true); // Horizontal mirror flip toggle

  // Model & Detection State
  const [modelInfo, setModelInfo] = useState({ status: "UNINITIALIZED", errorMessage: "" });
  
  // Real-Time Detection Memory Ref
  const detectionsRef = useRef({
    personCount: 0,
    persons: [],
    phones: [],
    rawPredictionsCount: 0,
    rawDetectionsSummary: [],
    phoneCandidates: [],
    phoneMisuseEvent: null
  });

  const isDetectingRef = useRef(false);
  const lastInferenceTimeRef = useRef(0);
  const phoneAlertCooldownsRef = useRef(new Map());
  // Approximate candidate: backend cell-phone detection associated with a tracked person.
  const [phoneMetrics, setPhoneMetrics] = useState({
    phoneDetected: false,
    associatedTrackId: null,
    personConfidence: 0,
    phoneConfidence: 0,
    durationSec: 0,
    warningLevel: 0,
    sirenActive: false,
    warningMessage: "Initializing AI models..."
  });

  // Debug Inspector State for UI
  const [debugData, setDebugData] = useState({
    rawPredictionsCount: 0,
    rawDetectionsSummary: [],
    phoneCandidates: [],
    faceRecognitionStatus: { status: "NOT_LOADED", error: "" }
  });

  // Configurable Detection Thresholds
  const [config, setConfig] = useState({
    minConfidence: 0.35,
    phoneThreshold: 0.35,
    minDurationSec: 2.0,
    warningIntervalSec: 2.5,
    maxWarnings: 3
  });

  // Reactive Live Persons List & Registration Modal State
  const [livePersonsList, setLivePersonsList] = useState([]);
  const [isRegisterModalOpen, setIsRegisterModalOpen] = useState(false);
  const [registrationForm, setRegistrationForm] = useState({
    id: "",
    track_id: "",
    name: "",
    designation: "Registered Worker",
    department: "Refinery Operations"
  });
  const [regSuccessMsg, setRegSuccessMsg] = useState("");
  const [regErrorMsg, setRegErrorMsg] = useState("");
  const [registrationFaceDescriptors, setRegistrationFaceDescriptors] = useState([]);
  const [isCapturingFace, setIsCapturingFace] = useState(false);
  const [selectedExistingPersonnelId, setSelectedExistingPersonnelId] = useState("");
  const registrationTargetBoxRef = useRef(null);

  const handleOpenRegisterModal = (targetTrackId = null) => {
    const selectedPerson = livePersonsList.find((person) => person.trackId === targetTrackId)
      || livePersonsList[0];
    const trackId = targetTrackId || selectedPerson?.trackId || `TRK-P${Math.floor(Math.random() * 800 + 100)}`;
    const existing = (registeredPersonnel || []).find(
      (person) => person.id === selectedPerson?.matchedPersonnelId
        || person.track_id === trackId
        || person.id === trackId
    );
    registrationTargetBoxRef.current = selectedPerson?.box || null;

    if (existing) {
      setSelectedExistingPersonnelId(existing.id);
      setRegistrationForm({
        id: existing.id,
        track_id: trackId,
        name: existing.name,
        designation: existing.designation || "Registered Worker",
        department: existing.department || "Refinery Operations"
      });
      setRegistrationFaceDescriptors(existing.face_descriptors || []);
    } else {
      setSelectedExistingPersonnelId("");
      const suggestedId = trackId.startsWith("TRK-P")
        ? trackId.replace("TRK-P", "EMP-")
        : trackId.replace(/^TRK-/, "EMP-");
      setRegistrationForm({
        id: suggestedId,
        track_id: trackId,
        name: "",
        designation: "Registered Worker",
        department: "Refinery Operations"
      });
      setRegistrationFaceDescriptors([]);
    }
    setRegSuccessMsg("");
    setRegErrorMsg("");
    setIsRegisterModalOpen(true);
  };

  const handleSelectExistingPersonnel = (personId) => {
    setSelectedExistingPersonnelId(personId);
    if (!personId) {
      const trackId = registrationTargetBoxRef.current
        ? registrationForm.track_id
        : `TRK-P${Math.floor(Math.random() * 800 + 100)}`;
      setRegistrationFaceDescriptors([]);
      setRegistrationForm({
        id: trackId.startsWith("TRK-P")
          ? trackId.replace("TRK-P", "EMP-")
          : trackId.replace(/^TRK-/, "EMP-"),
        track_id: trackId,
        name: "",
        designation: "Registered Worker",
        department: "Refinery Operations"
      });
      return;
    }

    const person = (registeredPersonnel || []).find((entry) => entry.id === personId);
    if (!person) {
      setRegErrorMsg("Selected registered personnel record is no longer available. Refresh and try again.");
      return;
    }
    setRegistrationFaceDescriptors(person.face_descriptors || []);
    setRegistrationForm((current) => ({
      id: person.id,
      track_id: current.track_id || person.track_id || person.id,
      name: person.name,
      designation: person.designation || "Registered Worker",
      department: person.department || "Refinery Operations"
    }));
    setRegErrorMsg("");
  };

  const handleCaptureRegistrationFace = async () => {
    let selectedPerson = livePersonsList.find(
      (person) => person.trackId === registrationForm.track_id
    );
    if (!selectedPerson && registrationTargetBoxRef.current) {
      const [targetX, targetY, targetWidth, targetHeight] = registrationTargetBoxRef.current;
      const targetCenter = [targetX + targetWidth / 2, targetY + targetHeight / 2];
      selectedPerson = livePersonsList
        .map((person) => {
          const [x, y, width, height] = person.box;
          const center = [x + width / 2, y + height / 2];
          return {
            person,
            distance: Math.hypot(center[0] - targetCenter[0], center[1] - targetCenter[1])
          };
        })
        .filter(({ distance }) => distance <= 0.35)
        .sort((a, b) => a.distance - b.distance)[0]?.person;
    }
    const source = videoRef.current?.readyState >= 2
      ? videoRef.current
      : imageRef.current?.complete
        ? imageRef.current
        : null;
    if (!selectedPerson || !source) {
      setRegErrorMsg("Keep the person in the live frame, then capture the face template.");
      return;
    }

    setIsCapturingFace(true);
    setRegErrorMsg("");
    try {
      const descriptor = await captureFaceDescriptor(source, selectedPerson.box);
      setRegistrationFaceDescriptors((current) => [...current, descriptor].slice(-5));
      registrationTargetBoxRef.current = selectedPerson.box;
    } catch (error) {
      setRegErrorMsg(error.message);
    } finally {
      setIsCapturingFace(false);
    }
  };

  const handleSaveRegistration = async (e) => {
    e.preventDefault();
    if (!registrationForm.id || !registrationForm.name) return;
    const hasCompleteFaceEnrollment =
      registrationFaceDescriptors.length >= FACE_ENROLLMENT_SAMPLE_COUNT;
    const hasPartialFaceEnrollment =
      registrationFaceDescriptors.length > 0 && !hasCompleteFaceEnrollment;
    try {
      await registerPerson({
        id: registrationForm.id,
        track_id: registrationForm.track_id || registrationForm.id,
        name: registrationForm.name,
        designation: registrationForm.designation,
        department: registrationForm.department,
        face_descriptors: hasCompleteFaceEnrollment ? registrationFaceDescriptors : []
      });

      setRegErrorMsg("");
      setRegSuccessMsg(hasCompleteFaceEnrollment
        ? `Face identity and personnel profile saved for ${registrationForm.name} (${registrationForm.id}).`
        : hasPartialFaceEnrollment
          ? `Personnel profile saved for ${registrationForm.name} (${registrationForm.id}). Face samples were not saved because all ${FACE_ENROLLMENT_SAMPLE_COUNT} are required.`
          : `Personnel profile saved for ${registrationForm.name} (${registrationForm.id}). Face recognition is pending enrollment.`);
      setTimeout(() => {
        setIsRegisterModalOpen(false);
        setRegSuccessMsg("");
      }, 1800);
    } catch (error) {
      setRegErrorMsg(error.message || "Registration failed. The profile was not saved.");
    }
  };

  // Handle Mode Switch to Live Camera
  const handleSelectLiveCamera = async () => {
    setInputMode("camera");
    setCameraError(null);
    if (!activeStream) {
      setIsConnectingCam(true);
      const res = await connectWebcam();
      setIsConnectingCam(false);
      if (!res.success) {
        setCameraError(res.error || "Camera access denied or device missing. Please click 'Enable Camera'.");
      }
    }
  };

  // Re-try / Re-request Camera Permission
  const handleEnableCameraClick = async () => {
    setCameraError(null);
    setIsConnectingCam(true);
    const res = await connectWebcam();
    setIsConnectingCam(false);
    if (!res.success) {
      setCameraError(res.error || "Camera access denied. Please allow permissions in browser settings.");
    }
  };

  // Handle local image/video uploads without keeping duplicate blob URLs.
  const handleImageUpload = async (e) => {
    const file = e.target.files[0];
    if (file) {
      setCameraError(null);
      try {
        if (file.type.startsWith("image/")) {
          setInputMode("image");
          await connectImageFile(file);
        } else if (file.type.startsWith("video/")) {
          setInputMode("camera");
          await connectVideoFile(file);
        } else {
          setCameraError("Choose a supported JPG, PNG, MP4 or WebM file.");
        }
      } catch (error) {
        setCameraError(error.message || "The selected media could not be loaded.");
      } finally {
        e.target.value = "";
      }
    }
  };

  // Initialize TensorFlow COCO-SSD Model on Mount
  useEffect(() => {
    loadDetectionModel().then(() => {
      setModelInfo(getModelStatus());
    });
  }, []);

  // Attach whichever selected source was configured in Camera Setup.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    if (activeStream instanceof MediaStream) {
      if (video.srcObject !== activeStream) {
        video.pause();
        video.removeAttribute("src");
        video.srcObject = activeStream;
      }
      if (video.paused) {
        video.play().catch((error) => {
          if (error.name !== "AbortError") setCameraError(`Live camera playback failed: ${error.message}`);
        });
      }
    } else if (streamSource?.type === "file" && streamSource.url) {
      if (video.getAttribute("src") !== streamSource.url) {
        video.pause();
        video.srcObject = null;
        video.src = streamSource.url;
        video.load();
      }
      if (video.paused) {
        video.play().catch((error) => {
          if (error.name !== "AbortError") setCameraError(`Uploaded video playback failed: ${error.message}`);
        });
      }
    } else {
      video.pause();
      video.srcObject = null;
      video.removeAttribute("src");
    }
  }, [activeStream, streamSource]);

  useEffect(() => {
    if (streamSource?.type === "image") setInputMode("image");
    else if (streamSource) setInputMode("camera");
  }, [streamSource]);

  // Main Render Loop (60 FPS) + Throttled Concurrency-Safe AI Inference (~150ms)
  useEffect(() => {
    let animId;

    const renderLoop = async () => {
      const video = videoRef.current;
      const canvas = canvasRef.current;
      const img = imageRef.current;

      const isImageSource = streamSource?.type === "image" && img && (img.complete || img.naturalWidth > 0);
      const isVideoType = streamSource?.type === "webcam" || streamSource?.type === "file";
      const isVideoSource = isVideoType && video && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA;
      const sourceElem = isImageSource ? img : isVideoSource ? video : null;

      // Verify source & canvas availability
      if (sourceElem && canvas) {
        if (canvas.width !== canvas.clientWidth || canvas.height !== canvas.clientHeight) {
          canvas.width = canvas.clientWidth || 640;
          canvas.height = canvas.clientHeight || 360;
        }

        const ctx = canvas.getContext("2d");
        const width = canvas.width;
        const height = canvas.height;

        // Draw current frame onto canvas (with optional horizontal mirror transform for live webcam)
        if (isMirrored && !isImageSource) {
          ctx.save();
          ctx.translate(width, 0);
          ctx.scale(-1, 1);
          ctx.drawImage(sourceElem, 0, 0, width, height);
          ctx.restore();
        } else {
          ctx.drawImage(sourceElem, 0, 0, width, height);
        }

        // 1. Draw Restricted Zones (Polygons)
        if (showZones && restrictedZones.length > 0) {
          restrictedZones.forEach((zone) => {
            if (zone.polygon_coords && zone.polygon_coords.length > 2) {
              ctx.beginPath();
              const startX = isMirrored && !isImageSource
                ? (1 - zone.polygon_coords[0][0]) * width
                : zone.polygon_coords[0][0] * width;
              ctx.moveTo(startX, zone.polygon_coords[0][1] * height);

              for (let i = 1; i < zone.polygon_coords.length; i++) {
                const zx = isMirrored && !isImageSource
                  ? (1 - zone.polygon_coords[i][0]) * width
                  : zone.polygon_coords[i][0] * width;
                ctx.lineTo(zx, zone.polygon_coords[i][1] * height);
              }
              ctx.closePath();
              ctx.fillStyle = "rgba(239, 68, 68, 0.18)";
              ctx.fill();
              ctx.strokeStyle = "#ef4444";
              ctx.lineWidth = 2;
              ctx.setLineDash([6, 6]);
              ctx.stroke();
              ctx.setLineDash([]);

              ctx.fillStyle = "#ef4444";
              ctx.font = "bold 12px sans-serif";
              ctx.fillText(
                `RESTRICTED AREA: ${zone.zone_name}`,
                startX + 5,
                zone.polygon_coords[0][1] * height + 15
              );
            }
          });
        }

        // 2. Trigger Safe Async TensorFlow Object Detection (Every ~150ms)
        const now = Date.now();
        if (!isDetectingRef.current && now - lastInferenceTimeRef.current >= 150) {
          isDetectingRef.current = true;
          lastInferenceTimeRef.current = now;

          detectObjectsAndMobilePhone(sourceElem, { ...config, registeredPersonnel })
            .then((result) => {
              detectionsRef.current = result;

              // Synchronize debug state
              setDebugData({
                rawPredictionsCount: result.rawPredictionsCount || 0,
                rawDetectionsSummary: result.rawDetectionsSummary || [],
                phoneCandidates: result.phoneCandidates || [],
                faceRecognitionStatus: result.faceRecognitionStatus || { status: "NOT_LOADED", error: "" }
              });
              setModelInfo(getModelStatus());

              // Synchronize live persons list for quick registration cards
              setLivePersonsList(result.persons || []);

              // Synchronize person count (Strictly 0 when AI detects 0 persons)
              if (result.personCount !== personCount) {
                setPersonCount(result.personCount);
              }

              const detectedPhones = result.phones || [];
              const nearestPhone = detectedPhones[0];
              const potentialUse = result.phoneMisuseEvent;
              setPhoneMetrics({
                phoneDetected: detectedPhones.length > 0,
                associatedTrackId: potentialUse?.associatedTrackId || nearestPhone?.associatedTrackId || null,
                personConfidence: 0,
                phoneConfidence: potentialUse?.phoneConfidence || nearestPhone?.confidence || 0,
                durationSec: 0,
                warningLevel: potentialUse ? 1 : 0,
                sirenActive: false,
                warningMessage:
                  potentialUse?.warningMessage ||
                  (detectedPhones.length > 0
                    ? "Cell phone object detected, but no tracked person is near it."
                    : "No cell phone object detected.")
              });

              if (potentialUse?.associatedTrackId) {
                const lastAlertAt = phoneAlertCooldownsRef.current.get(potentialUse.associatedTrackId) || 0;
                if (now - lastAlertAt >= 30_000) {
                  phoneAlertCooldownsRef.current.set(potentialUse.associatedTrackId, now);
                  for (const [trackId, alertAt] of phoneAlertCooldownsRef.current) {
                    if (now - alertAt > 10 * 60_000) phoneAlertCooldownsRef.current.delete(trackId);
                  }
                  dispatchAlert({
                    event_type: "POTENTIAL_PHONE_USE",
                    zone_name: streamSource?.name || "Plant Monitoring Zone",
                    severity: "MEDIUM",
                    confidence: potentialUse.phoneConfidence,
                    evidence_image_base64: captureCanvasSnapshot(canvas, "POTENTIAL PHONE USE — VERIFY"),
                    metadata: {
                      track_id: potentialUse.associatedTrackId,
                      detection_basis: "COCO cell-phone object detected near tracked person",
                      verified_use: false,
                      operator_action: "Verify manually; phone-in-hand or active call is not confirmed by this model."
                    }
                  });
                }
              }
            })
            .catch((err) => {
              console.error("AI Inference Error:", err);
            })
            .finally(() => {
              isDetectingRef.current = false;
            });
        }

        // 3. Draw Real AI Detection Bounding Boxes from TensorFlow Results
        if (showBoxes) {
          const currentDetections = detectionsRef.current;
          const activePersons = currentDetections.persons || [];
          const activePhones = currentDetections.phones || [];
          const drawnPersonBoxes = [];

          // Draw Person Bounding Boxes
          for (let i = 0; i < activePersons.length; i++) {
            const pObj = activePersons[i];
            const p = pObj.box;
            const px = isMirrored ? width - (p[0] + p[2]) * width : p[0] * width;
            const py = p[1] * height, pw = p[2] * width, ph = p[3] * height;

            // Lookup registered identity by track_id or id
            const regPerson = (registeredPersonnel || []).find(
              (r) => r.id === pObj.matchedPersonnelId
                || r.track_id === pObj.trackId
                || r.id === pObj.trackId
            );

            const boxColor = pObj.isUsingPhone
              ? "#f59e0b"
              : regPerson
              ? "#0284c7" // sky blue for registered personnel
              : pObj.color;

            ctx.strokeStyle = boxColor;
            ctx.lineWidth = pObj.isUsingPhone ? 3.5 : 2.5;
            ctx.strokeRect(px, py, pw, ph);

            const identityText = regPerson ? `${regPerson.id} | ${regPerson.name}` : pObj.trackId;
            const statusText = pObj.isUsingPhone
              ? "[PHONE NEAR PERSON — VERIFY]"
              : pObj.hasHelmet === null
              ? "[HELMET STATUS UNAVAILABLE]"
              : pObj.hasHelmet
                ? "[HELMET OK]"
                : "[NO HELMET]";

            const tagText = `${identityText} ${statusText}`;

            ctx.fillStyle = pObj.isUsingPhone
              ? "rgba(245, 158, 11, 0.95)"
              : regPerson
              ? "rgba(2, 132, 199, 0.95)"
              : pObj.hasHelmet === null
              ? "rgba(100, 116, 139, 0.95)"
              : pObj.hasHelmet
              ? "rgba(16, 185, 129, 0.9)"
              : "rgba(239, 68, 68, 0.95)";

            const tagWidth = Math.max(170, tagText.length * 8.2);
            ctx.fillRect(px, py - 24, tagWidth, 24);

            ctx.fillStyle = "#ffffff";
            ctx.font = "bold 11px sans-serif";
            ctx.fillText(tagText, px + 6, py - 7);

            drawnPersonBoxes.push({ trackId: pObj.trackId, px, py, pw, ph, center: [px + pw / 2, py + ph / 2] });
          }

          // Draw phone bounding boxes returned by backend inference.
          for (let j = 0; j < activePhones.length; j++) {
            const phObj = activePhones[j];
            const phBox = phObj.box;
            const phx = isMirrored ? width - (phBox[0] + phBox[2]) * width : phBox[0] * width;
            const phy = phBox[1] * height, phw = phBox[2] * width, phh = phBox[3] * height;

            ctx.strokeStyle = "#06b6d4";
            ctx.lineWidth = 2.5;
            ctx.setLineDash([3, 3]);
            ctx.strokeRect(phx, phy, phw, phh);
            ctx.setLineDash([]);

            const phoneTag = `${phObj.class || "Cell phone"} (${Math.round((phObj.confidence || 0) * 100)}%)`;
            const tagW = Math.max(130, phoneTag.length * 7.5);

            ctx.fillStyle = "rgba(6, 182, 212, 0.95)";
            ctx.fillRect(phx, phy - 20, tagW, 20);
            ctx.fillStyle = "#000000";
            ctx.font = "bold 10px sans-serif";
            ctx.fillText(phoneTag, phx + 4, phy - 6);
          }

          // Draw Proximity Distance Lines between Multiple Detected Persons
          if (showDistances && drawnPersonBoxes.length > 1) {
            for (let i = 0; i < drawnPersonBoxes.length - 1; i++) {
              const b1 = drawnPersonBoxes[i];
              const b2 = drawnPersonBoxes[i + 1];

              ctx.beginPath();
              ctx.moveTo(b1.center[0], b1.center[1]);
              ctx.lineTo(b2.center[0], b2.center[1]);
              ctx.strokeStyle = "#06b6d4";
              ctx.lineWidth = 1.5;
              ctx.setLineDash([4, 4]);
              ctx.stroke();
              ctx.setLineDash([]);
            }
          }
        }
      }

      animId = requestAnimationFrame(renderLoop);
    };

    if (streamSource) {
      animId = requestAnimationFrame(renderLoop);
    }

    return () => cancelAnimationFrame(animId);
  }, [streamSource, showBoxes, showZones, showDistances, isMirrored, restrictedZones, personCount, config, registeredPersonnel]);

  // Capture evidence for manual review without asserting an unverified PPE violation.
  const handleCaptureEvidenceAlert = () => {
    const canvas = canvasRef.current;
    const base64Snap = captureCanvasSnapshot(canvas, "MANUAL SAFETY REVIEW");

    dispatchAlert({
      event_type: "MANUAL_REVIEW",
      zone_name: streamSource?.name || "Refinery Area",
      severity: "MEDIUM",
      confidence: 0,
      evidence_image_base64: base64Snap,
      metadata: { source: "operator_submitted", review_required: true }
    });
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto font-sans text-slate-800">
      {/* Title Header & Mode Selector */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-slate-100 flex items-center gap-2">
            <MonitorPlay className="w-5 h-5 text-cyan-400" />
            Live AI Inspection & Object Detection
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Live person tracking with backend YOLOv8 COCO cell-phone detection.
          </p>
        </div>

        {/* Dual Input Mode Controls: [ Live Camera ] [ Upload Media ] */}
        <div className="flex flex-wrap items-center gap-2 bg-slate-900 p-1.5 rounded-2xl border border-slate-800 text-xs">
          <button
            onClick={handleSelectLiveCamera}
            disabled={isConnectingCam}
            className={`px-4 py-2 rounded-xl text-xs font-bold transition flex items-center gap-2 ${
              inputMode === "camera"
                ? "bg-cyan-600 text-white shadow-lg shadow-cyan-600/30"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {isConnectingCam ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Camera className="w-4 h-4" />}
            <span>Live Camera</span>
          </button>

          <label
            className={`px-4 py-2 rounded-xl text-xs font-bold transition flex items-center gap-2 cursor-pointer ${
              inputMode === "image"
                ? "bg-emerald-600 text-white shadow-lg shadow-emerald-600/30"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            <Upload className="w-4 h-4" />
            <span>Upload Image / Video</span>
            <input
              type="file"
              accept="image/jpeg,image/png,image/jpg,video/mp4,video/webm"
              onChange={handleImageUpload}
              className="hidden"
            />
          </label>
        </div>
      </div>

      {/* Clearly show when live inference has no camera frames to process. */}
      {inputMode === "camera" && (!streamSource || cameraError) && (
        <div className={`p-4 rounded-2xl text-xs flex items-center justify-between gap-4 shadow-lg ${
          cameraError
            ? "bg-red-500/10 border border-red-500/30 text-red-400"
            : "bg-amber-500/10 border border-amber-500/30 text-amber-300"
        }`}>
          <div className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <div>
              <strong className="block font-bold">
                {cameraError ? "Camera Permission Required:" : "No live camera connected — detection is paused."}
              </strong>
              <span>{cameraError || "Start the camera or upload an image/video to run detection."}</span>
            </div>
          </div>
          <button
            onClick={handleEnableCameraClick}
            disabled={isConnectingCam}
            className="px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs transition shadow-lg shrink-0 flex items-center gap-1.5"
          >
            {isConnectingCam ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Camera className="w-4 h-4" />}
            <span>{cameraError ? "Retry Camera" : "Enable Camera"}</span>
          </button>
        </div>
      )}

      {/* Overlay Toggle Buttons */}
      <div className="flex flex-wrap items-center gap-2 bg-slate-900/80 p-2 rounded-xl border border-slate-800 text-xs">
        <button
          onClick={() => setShowBoxes(!showBoxes)}
          className={`px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition ${
            showBoxes ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30" : "text-slate-500"
          }`}
        >
          {showBoxes ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
          <span>Show Bounding Boxes</span>
        </button>

        <button
          onClick={() => setShowZones(!showZones)}
          className={`px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition ${
            showZones ? "bg-red-500/20 text-red-400 border border-red-500/30" : "text-slate-500"
          }`}
        >
          {showZones ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
          <span>Show Restricted Zones</span>
        </button>

        <button
          onClick={() => setShowDistances(!showDistances)}
          className={`px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition ${
            showDistances ? "bg-amber-500/20 text-amber-400 border border-amber-500/30" : "text-slate-500"
          }`}
        >
          {showDistances ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
          <span>Show Distance Lines</span>
        </button>

        <button
          onClick={() => setIsMirrored(!isMirrored)}
          className={`px-3 py-1.5 rounded-lg flex items-center gap-1.5 transition ${
            isMirrored ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30" : "text-slate-500"
          }`}
        >
          <FlipHorizontal className="w-3.5 h-3.5" />
          <span>Mirror View ({isMirrored ? "ON" : "OFF"})</span>
        </button>
      </div>

      {/* Real Automatic AI Person Counter Status Bar */}
      {streamSource && (
        <div className="p-3.5 rounded-xl bg-white border border-emerald-200 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs shadow-2xs">
          <div className="flex items-center gap-2.5 font-bold text-slate-800">
            <div
              className={`w-2.5 h-2.5 rounded-full ${
                personCount > 0 ? "bg-emerald-500 animate-pulse" : "bg-slate-400"
              } shrink-0`}
            />
            <span>AI Real-Time Person Counter:</span>
            <span
              className={`px-2.5 py-0.5 rounded-full font-mono text-[11px] border ${
                personCount > 0
                  ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                  : "bg-slate-100 text-slate-600 border-slate-200"
              }`}
            >
              {personCount === 0
                ? "0 Persons (No Human in Frame)"
                : `${personCount} ${personCount === 1 ? "Person Detected" : "People Detected"}`}
            </span>
          </div>

          <div className="text-[11px] text-slate-500 font-medium space-y-1">
            <div className="flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5 text-emerald-600" />
              <span>Person tracking • Backend YOLOv8 phone detection • Approximate warnings only</span>
            </div>
            <div className={`text-[10px] ${
              modelInfo.status === "ERROR" || debugData.faceRecognitionStatus.status === "ERROR"
                ? "text-red-600"
                : "text-sky-700"
            }`}>
              {modelInfo.status === "ERROR"
                ? modelInfo.errorMessage || "Person detection model failed to load."
                : debugData.faceRecognitionStatus.status === "ERROR"
                ? debugData.faceRecognitionStatus.error
                : registeredPersonnel.some((person) => person.face_descriptors?.length)
                  ? `Registered face matching: ${debugData.faceRecognitionStatus.status}`
                  : "Registered face matching: enroll a face template during registration"}
            </div>
          </div>
        </div>
      )}

      {/* Cell-phone object detection status panel */}
      <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 text-slate-100 space-y-4 shadow-xl">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
          <div className="flex items-center gap-2.5">
            <div
              className={`p-2 rounded-xl border ${
                phoneMetrics.phoneDetected
                  ? "bg-amber-500/20 text-amber-400 border-amber-500/30 animate-pulse"
                  : "bg-slate-800 text-slate-400 border-slate-700"
              }`}
            >
              <Smartphone className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-100">
                Potential Phone Use (Approximate)
              </h2>
              <p className="text-[11px] text-slate-400">
                Real cell-phone detections near people create a potential-use warning for manual verification.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            {/* Show backend inference status without implying a custom trained phone model. */}
            <span
              className={`px-3 py-1 rounded-full text-xs font-bold border flex items-center gap-1.5 ${
                detectionsRef.current?.phoneDetectorIsActive === true
                  ? "bg-cyan-500/10 text-cyan-400 border-cyan-500/30"
                  : "bg-red-500/20 text-red-400 border-red-500/40"
              }`}
            >
              <Smartphone className="w-3.5 h-3.5" />
              <span>{detectionsRef.current?.phoneDetectorStatus || "Phone detector: waiting for backend inference"}</span>
            </span>

            <span
              className={`px-3 py-1 rounded-full text-xs font-bold border flex items-center gap-1.5 ${
                phoneMetrics.sirenActive
                  ? "bg-red-500/20 text-red-400 border-red-500/40 animate-pulse"
                  : phoneMetrics.phoneDetected
                  ? "bg-amber-500/20 text-amber-400 border-amber-500/30"
                  : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
              }`}
            >
              {phoneMetrics.phoneDetected ? (
                <>
                  <AlertOctagon className="w-3.5 h-3.5 text-amber-400" />
                  <span>POTENTIAL PHONE USE — VERIFY</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>NO CELL PHONE OBJECT DETECTED</span>
                </>
              )}
            </span>
          </div>
        </div>

        {/* Live Metrics Grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 text-xs">
          <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
            <span className="text-[10px] text-slate-400 uppercase font-semibold">Phone Detected</span>
            <div className={`mt-1 text-sm font-bold ${phoneMetrics.phoneDetected ? "text-amber-400" : "text-slate-300"}`}>
              {phoneMetrics.phoneDetected ? "YES" : "NO"}
            </div>
          </div>

          <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
            <span className="text-[10px] text-slate-400 uppercase font-semibold">Nearby Person</span>
            <div className="mt-1 text-sm font-bold font-mono text-cyan-400">
              {phoneMetrics.associatedTrackId || "None"}
            </div>
          </div>

          <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
            <span className="text-[10px] text-slate-400 uppercase font-semibold">Object Confidence</span>
            <div className="mt-1 text-sm font-bold text-slate-200">
              {phoneMetrics.phoneConfidence > 0 ? `${Math.round(phoneMetrics.phoneConfidence * 100)}%` : "N/A"}
            </div>
          </div>

          <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
            <span className="text-[10px] text-slate-400 uppercase font-semibold">Potential Use</span>
            <div className="mt-1 text-sm font-bold text-amber-300">
              {phoneMetrics.associatedTrackId ? "VERIFY" : "NO MATCH"}
            </div>
          </div>
        </div>

        <p className="text-xs text-amber-300">
          Warning basis: a detected cell phone near a tracked person. It may be a false alarm; hand-held use or an active call is not confirmed. Verify before acting.
        </p>
      </div>

      {/* AI Model Inspector / Debug Overlay UI Card */}
      <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800 text-xs font-mono space-y-3">
        <div className="flex items-center justify-between pb-2 border-b border-slate-800 text-slate-300">
          <div className="flex items-center gap-2 font-bold">
            <Bug className="w-4 h-4 text-amber-400" />
            <span>AI Model Real-Time Debug Inspector</span>
          </div>
          <span className="text-[10px] text-slate-500">Live Console & Frame Inspector</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-[11px]">
          {/* Col 1: Raw Frame Predictions */}
          <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
            <span className="text-slate-400 font-semibold flex items-center gap-1">
              <Activity className="w-3 h-3 text-cyan-400" />
              Raw AI Predictions:
            </span>
            <p className="text-slate-200 font-bold text-sm">
              {debugData.rawPredictionsCount} Objects In Frame
            </p>
            <div className="text-[10px] text-slate-400 max-h-20 overflow-y-auto space-y-0.5 pt-1">
              {debugData.rawDetectionsSummary.length > 0 ? (
                debugData.rawDetectionsSummary.map((d, i) => (
                  <div key={i} className="flex justify-between">
                    <span>{d.class}</span>
                    <span className="text-emerald-400">{Math.round(d.score * 100)}%</span>
                  </div>
                ))
              ) : (
                <span className="text-slate-500 italic">No objects detected</span>
              )}
            </div>
          </div>

          {/* Col 2: Raw Cell Phone Candidates */}
          <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
            <span className="text-slate-400 font-semibold flex items-center gap-1">
              <Smartphone className="w-3 h-3 text-amber-400" />
              Phone Candidates (`cell phone`):
            </span>
            <p className={`font-bold text-sm ${debugData.phoneCandidates.length > 0 ? "text-amber-400" : "text-slate-400"}`}>
              {debugData.phoneCandidates.length > 0 ? `${debugData.phoneCandidates.length} Candidate(s)` : "None detected"}
            </p>
            <div className="text-[10px] text-slate-400 max-h-20 overflow-y-auto space-y-0.5 pt-1">
              {debugData.phoneCandidates.length > 0 ? (
                debugData.phoneCandidates.map((p, i) => (
                  <div key={i} className="flex justify-between text-amber-300">
                    <span>{p.class}</span>
                    <span>{Math.round(p.score * 100)}%</span>
                  </div>
                ))
              ) : (
                <span className="text-slate-500 italic">No cell phone class detected</span>
              )}
            </div>
          </div>

          {/* Col 3: Phone-use classification status */}
          <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800 space-y-1">
            <span className="text-slate-400 font-semibold flex items-center gap-1">
              <ShieldCheck className="w-3 h-3 text-emerald-400" />
              Potential phone-use warning:
            </span>
            <p className={`font-bold text-sm ${phoneMetrics.phoneDetected ? "text-amber-400" : "text-slate-300"}`}>
              {phoneMetrics.associatedTrackId
                ? "Phone detected near person — verify manually"
                : phoneMetrics.phoneDetected
                  ? "Phone detected — no nearby person matched"
                  : "No cell phone object detected"}
            </p>
            <div className="text-[10px] text-slate-400 space-y-0.5 pt-1">
              <div>Assoc. Person: <span className="text-cyan-400">{phoneMetrics.associatedTrackId || "None"}</span></div>
              <div>Duration: <span className="text-emerald-400">{phoneMetrics.durationSec}s</span></div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Video Stream Canvas Container */}
      <div className="p-4 rounded-2xl glass-panel space-y-4">
        <div className="relative rounded-2xl overflow-hidden bg-slate-950 border border-slate-800 aspect-video flex items-center justify-center">
          {/* Hidden HTML5 Video & Image Sources */}
          <video ref={videoRef} autoPlay playsInline muted className="hidden" />
          {streamSource?.type === "image" && (
            <img
              ref={imageRef}
              src={streamSource.url}
              alt={streamSource.name}
              className="hidden"
              crossOrigin="anonymous"
            />
          )}

          {/* Main Inference Canvas */}
          <canvas ref={canvasRef} className="w-full h-full object-contain" />

          {!streamSource && (
            <div className="absolute inset-0 flex flex-col items-center justify-center p-8 text-center bg-slate-950/95">
              <Camera className="w-16 h-16 text-slate-700 mb-3" />
              <h3 className="text-base font-bold text-slate-300">NO LIVE INPUT AVAILABLE</h3>
              <p className="text-xs text-slate-500 mt-1 max-w-md">
                Connect your camera feed under <strong>"Camera Setup"</strong> to start live AI scanning.
              </p>
              <button
                onClick={() => setActiveTab("camera-input")}
                className="mt-4 px-4 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-semibold text-xs transition shadow-lg shadow-cyan-500/20"
              >
                Go to Camera Setup
              </button>
            </div>
          )}

          {/* NOT ASSESSABLE Overlay */}
          {streamSource && !streamMetrics.isAssessable && (
            <div className="absolute inset-0 bg-slate-950/90 flex flex-col items-center justify-center p-6 text-center">
              <div className="w-12 h-12 rounded-xl bg-amber-500/20 border border-amber-500/40 flex items-center justify-center text-amber-400 mb-3">
                <EyeOff className="w-6 h-6" />
              </div>
              <h3 className="text-base font-bold text-amber-300">CAMERA NOT ASSESSABLE</h3>
              <p className="text-xs text-amber-400/90 mt-1 max-w-md">
                {streamMetrics.notAssessableReason}
              </p>
              <span className="mt-3 px-3 py-1 rounded bg-slate-900 text-slate-400 text-[10px] border border-slate-800">
                AI scanning paused to prevent false detections.
              </span>
            </div>
          )}
        </div>

        {/* Snapshot & Send Manual Alert Button */}
        {streamSource && (
          <div className="flex items-center justify-between text-xs pt-2">
            <div className="flex items-center gap-4 text-slate-400">
              <span>
                Source: <strong className="text-emerald-400">{streamSource.name}</strong>
              </span>
            </div>

            <button
              onClick={handleCaptureEvidenceAlert}
              className="px-4 py-2 rounded-xl bg-red-600 hover:bg-red-500 text-white font-semibold text-xs transition shadow-lg shadow-red-500/20 flex items-center gap-2"
            >
              <ShieldAlert className="w-4 h-4" />
              <span>Capture Photo for Manual Review</span>
            </button>
          </div>
        )}
      </div>

      {/* Live Detected Persons & Quick Registration Panel */}
      <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 text-slate-100 space-y-4 shadow-xl">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-sky-500/10 border border-sky-500/20 text-sky-400">
              <UserCheck className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-bold text-slate-100">
                  Live Detected Persons & Quick Registration
                </h2>
                <span className="px-2 py-0.5 text-[10px] font-bold rounded-full bg-sky-500/20 text-sky-300 border border-sky-500/30 font-mono">
                  {livePersonsList.length} In Frame
                </span>
              </div>
              <p className="text-[11px] text-slate-400 mt-0.5">
                Assign live IDs, register names, and sync employee profiles with the SQLite database.
              </p>
            </div>
          </div>

          <button
            onClick={() => handleOpenRegisterModal()}
            className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs transition shadow-lg shadow-emerald-600/20 flex items-center gap-2 cursor-pointer shrink-0"
          >
            <UserPlus className="w-4 h-4" />
            <span>+ Register Custom Personnel ID</span>
          </button>
        </div>

        {/* Cards Grid */}
        {livePersonsList.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {livePersonsList.map((p) => {
              const reg = (registeredPersonnel || []).find(
                (r) => r.id === p.matchedPersonnelId
                  || r.track_id === p.trackId
                  || r.id === p.trackId
              );
              return (
                <div
                  key={p.trackId}
                  className={`p-4 rounded-xl border transition-all space-y-2.5 ${
                    reg
                      ? "bg-slate-950/90 border-emerald-500/40 shadow-emerald-950/20"
                      : "bg-slate-950/70 border-slate-800"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-bold text-xs text-sky-300">
                      {reg ? `${reg.id}` : p.trackId}
                    </span>
                    <span
                      className={`px-2 py-0.5 rounded text-[9px] font-extrabold uppercase tracking-wider ${
                        reg
                          ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                          : "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                      }`}
                    >
                      {reg ? "REGISTERED WORKER" : "UNREGISTERED WORKER"}
                    </span>
                  </div>

                  {reg && (
                    <div className="text-xs font-bold text-slate-100 flex items-center gap-1.5">
                      <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                      <span>{reg.name}</span>
                      <span className="text-[10px] text-slate-400 font-normal">({reg.designation})</span>
                    </div>
                  )}

                  <div className="text-xs space-y-1 text-slate-300">
                    <div className="flex justify-between text-[11px]">
                      <span className="text-slate-400">Confidence:</span>
                      <span className="font-mono font-bold text-slate-200">
                        {Math.round((p.confidence || 0.88) * 100)}%
                      </span>
                    </div>
                    <div className="flex justify-between text-[11px]">
                      <span className="text-slate-400">Helmet Status:</span>
                      <span
                        className={`font-semibold ${
                          p.hasHelmet === null
                            ? "text-slate-400"
                            : p.hasHelmet
                              ? "text-emerald-400"
                              : "text-red-400"
                        }`}
                      >
                        {p.hasHelmet === null
                          ? "Helmet status unavailable"
                          : p.hasHelmet
                            ? "✅ Helmet OK"
                            : "❌ No Helmet"}
                      </span>
                    </div>
                  </div>

                  <button
                    onClick={() => handleOpenRegisterModal(p.trackId)}
                    className={`w-full py-2 px-3 rounded-lg text-xs font-bold transition flex items-center justify-center gap-2 cursor-pointer ${
                      reg
                        ? "bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-slate-700"
                        : "bg-cyan-600 hover:bg-cyan-500 text-white shadow-md shadow-cyan-600/20"
                    }`}
                  >
                    <UserPlus className="w-3.5 h-3.5" />
                    <span>{reg ? "Edit Registration" : "Register This Live Person"}</span>
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="p-6 text-center text-slate-400 text-xs border border-dashed border-slate-800 rounded-xl space-y-2">
            <Users className="w-8 h-8 text-slate-600 mx-auto" />
            <p className="font-semibold text-slate-300">No active persons detected in camera frame currently</p>
            <p className="text-[11px] text-slate-500">
              When a worker appears in front of the camera, their live card will display here with direct registration controls.
            </p>
          </div>
        )}
      </div>

      {/* AI Technical Criteria Description */}
      <div className="p-5 rounded-2xl bg-white border border-slate-200 shadow-2xs space-y-4 text-xs text-slate-700">
        <div className="flex items-center gap-2 font-bold text-sm text-slate-900">
          <Info className="w-5 h-5 text-sky-600" />
          <span>Technical Architecture — Cell Phone Object Detection</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1.5">
            <div className="font-bold text-slate-900 flex items-center gap-1.5">
              <ShieldCheck className="w-4 h-4 text-emerald-600" />
              <span>1. Live Camera Object Detection:</span>
            </div>
            <ul className="list-disc pl-5 space-y-1 text-slate-600 font-medium">
              <li>
                <strong>Person tracking:</strong> Browser-based person detection from the active camera feed.
              </li>
              <li>
                <strong>Phone detection:</strong> Backend YOLOv8 COCO model reports the <code>cell phone</code> class.
              </li>
              <li>
                <strong>Phone Model:</strong> COCO class <code>cell phone</code> with normalized spatial bboxes.
              </li>
            </ul>
          </div>

          <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1.5">
            <div className="font-bold text-slate-900 flex items-center gap-1.5">
              <Smartphone className="w-4 h-4 text-cyan-600" />
              <span>2. Person-Phone Association & Safe Concurrency:</span>
            </div>
            <ul className="list-disc pl-5 space-y-1 text-slate-600 font-medium">
              <li>
                <strong>Camera requirement:</strong> Detection runs only while a live camera frame is available.
              </li>
              <li>
                <strong>Spatial association:</strong> Nearby phone/person detections trigger a potential-use warning for review.
              </li>
              <li>
                <strong>Verification:</strong> Proximity does not confirm hand-held use or an active call; verify before acting.
              </li>
            </ul>
          </div>
        </div>
      </div>

      {/* Quick Registration Modal */}
      {isRegisterModalOpen && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 text-slate-100 w-full max-w-md p-6 rounded-2xl shadow-2xl space-y-5 animate-fadeIn">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-xl bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                  <UserCheck className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-slate-100">Register Personnel Identity</h3>
                  <p className="text-[11px] text-slate-400">Enroll face templates locally for later recognition</p>
                </div>
              </div>
              <button
                onClick={() => setIsRegisterModalOpen(false)}
                className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {regSuccessMsg && (
              <div className="p-3 bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 rounded-xl text-xs flex items-center gap-2 font-semibold">
                <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                <span>{regSuccessMsg}</span>
              </div>
            )}

            {regErrorMsg && (
              <div className="p-3 bg-red-500/10 border border-red-500/30 text-red-300 rounded-xl text-xs flex items-center gap-2 font-semibold">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{regErrorMsg}</span>
              </div>
            )}

            <div className="p-3 bg-sky-500/10 border border-sky-500/20 rounded-xl space-y-2">
              <p className="text-[11px] text-slate-300">
                Save the personnel profile now, or capture {FACE_ENROLLMENT_SAMPLE_COUNT} clear samples of the same person's face
                (front, slight left, slight right) to enable recognition. Face templates and profile details are saved in local SQLite;
                camera images are not stored for recognition.
              </p>
              <div className="flex items-center justify-between gap-2">
                <span className={`text-xs font-semibold ${
                  registrationFaceDescriptors.length >= FACE_ENROLLMENT_SAMPLE_COUNT
                    ? "text-emerald-300"
                    : "text-amber-300"
                }`}>
                  {registrationFaceDescriptors.length}/{FACE_ENROLLMENT_SAMPLE_COUNT} face samples
                </span>
                <button
                  type="button"
                  onClick={handleCaptureRegistrationFace}
                  disabled={isCapturingFace || registrationFaceDescriptors.length >= 5}
                  className="px-3 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white font-bold"
                >
                  {isCapturingFace ? "Capturing..." : "Capture Face Sample"}
                </button>
              </div>
            </div>

            <form onSubmit={handleSaveRegistration} className="space-y-4 text-xs">
              {registeredPersonnel.length > 0 && (
                <div>
                  <label className="block text-slate-300 font-semibold mb-1">Personnel record</label>
                  <select
                    value={selectedExistingPersonnelId}
                    onChange={(event) => handleSelectExistingPersonnel(event.target.value)}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-100 focus:outline-none focus:border-sky-500"
                  >
                    <option value="">Create a new personnel record</option>
                    {registeredPersonnel.map((person) => (
                      <option key={person.id} value={person.id}>
                        {person.id} — {person.name} {person.face_descriptors?.length ? "(face enrolled)" : "(face not enrolled)"}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div>
                <label className="block text-slate-300 font-semibold mb-1">Live Track ID</label>
                <input
                  type="text"
                  value={registrationForm.track_id}
                  onChange={(e) => setRegistrationForm({ ...registrationForm, track_id: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-sky-400 font-mono font-bold focus:outline-none focus:border-sky-500"
                  placeholder="e.g. TRK-P109"
                  required
                />
              </div>

              <div>
                <label className="block text-slate-300 font-semibold mb-1">Unique Personnel / Employee ID</label>
                <input
                  type="text"
                  value={registrationForm.id}
                  onChange={(e) => setRegistrationForm({ ...registrationForm, id: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-100 font-mono font-bold focus:outline-none focus:border-sky-500"
                  placeholder="e.g. EMP-109"
                  required
                />
              </div>

              <div>
                <label className="block text-slate-300 font-semibold mb-1">Full Name</label>
                <input
                  type="text"
                  value={registrationForm.name}
                  onChange={(e) => setRegistrationForm({ ...registrationForm, name: e.target.value })}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-100 font-medium focus:outline-none focus:border-sky-500"
                  placeholder="e.g. Shiv Kumar"
                  required
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-300 font-semibold mb-1">Role / Designation</label>
                  <input
                    type="text"
                    value={registrationForm.designation}
                    onChange={(e) => setRegistrationForm({ ...registrationForm, designation: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-100 font-medium focus:outline-none focus:border-sky-500"
                    placeholder="e.g. Senior Field Engineer"
                  />
                </div>

                <div>
                  <label className="block text-slate-300 font-semibold mb-1">Department</label>
                  <input
                    type="text"
                    value={registrationForm.department}
                    onChange={(e) => setRegistrationForm({ ...registrationForm, department: e.target.value })}
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-slate-100 font-medium focus:outline-none focus:border-sky-500"
                    placeholder="e.g. Refinery Operations"
                  />
                </div>
              </div>

              <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
                <button
                  type="button"
                  onClick={() => setIsRegisterModalOpen(false)}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold shadow-lg shadow-emerald-600/30 flex items-center gap-2 cursor-pointer"
                >
                  <CheckCircle2 className="w-4 h-4" />
                  <span>Save & Register Profile</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
