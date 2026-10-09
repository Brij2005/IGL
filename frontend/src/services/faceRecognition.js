const FACE_MODEL_URL = "/models/face-api";
const FACE_MATCH_THRESHOLD = 0.6;
const MIN_FACE_SAMPLES = 3;

let faceApiPromise;
let faceRecognitionStatus = "NOT_LOADED";
let faceRecognitionError = "";

async function loadFaceApi() {
  if (!faceApiPromise) {
    faceRecognitionStatus = "LOADING";
    faceApiPromise = import("@vladmandic/face-api")
      .then(async (faceapi) => {
        await Promise.all([
          faceapi.nets.tinyFaceDetector.loadFromUri(FACE_MODEL_URL),
          faceapi.nets.faceLandmark68Net.loadFromUri(FACE_MODEL_URL),
          faceapi.nets.faceRecognitionNet.loadFromUri(FACE_MODEL_URL)
        ]);
        faceRecognitionStatus = "READY";
        return faceapi;
      })
      .catch((error) => {
        faceRecognitionStatus = "ERROR";
        faceRecognitionError = `Face recognition models could not be loaded: ${error.message}`;
        throw new Error(faceRecognitionError);
      });
  }
  return faceApiPromise;
}

function getSourceDimensions(source) {
  return {
    width: source.videoWidth || source.naturalWidth || source.width || 0,
    height: source.videoHeight || source.naturalHeight || source.height || 0
  };
}

export async function captureFaceDescriptor(source, normalizedPersonBox) {
  if (faceRecognitionStatus === "ERROR") {
    throw new Error(faceRecognitionError);
  }
  if (!source || !normalizedPersonBox) {
    throw new Error("Keep the person visible in the live camera before capturing a face sample.");
  }

  const { width, height } = getSourceDimensions(source);
  if (!width || !height) {
    throw new Error("The live camera frame is not ready. Wait for the video and try again.");
  }

  const [x, y, boxWidth, boxHeight] = normalizedPersonBox;
  const cropX = Math.max(0, Math.floor(x * width));
  const cropY = Math.max(0, Math.floor(y * height));
  const cropWidth = Math.min(width - cropX, Math.ceil(boxWidth * width));
  const cropHeight = Math.min(height - cropY, Math.ceil(boxHeight * height));
  if (cropWidth < 32 || cropHeight < 48) {
    throw new Error("The detected person is too small for a reliable face sample.");
  }

  const canvas = document.createElement("canvas");
  canvas.width = cropWidth;
  canvas.height = cropHeight;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) {
    throw new Error("Could not prepare the camera crop for face recognition.");
  }
  context.drawImage(source, cropX, cropY, cropWidth, cropHeight, 0, 0, cropWidth, cropHeight);

  const faceapi = await loadFaceApi();
  const results = await faceapi
    .detectAllFaces(canvas, new faceapi.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.4 }))
    .withFaceLandmarks()
    .withFaceDescriptors();

  const face = results
    .filter(({ detection }) => {
      const centerY = detection.box.y + detection.box.height / 2;
      return centerY <= cropHeight * 0.72 && detection.box.width >= 24 && detection.box.height >= 24;
    })
    .sort((a, b) => b.detection.score - a.detection.score)[0];

  if (!face) {
    throw new Error("No clear face was detected. Face the camera, move closer, and capture again.");
  }

  const descriptor = Array.from(face.descriptor);
  if (descriptor.length !== 128 || descriptor.some((value) => !Number.isFinite(value))) {
    throw new Error("Face recognition returned an invalid template. Please capture again.");
  }
  return descriptor;
}

export function getFaceRecognitionStatus() {
  return {
    status: faceRecognitionStatus,
    error: faceRecognitionError
  };
}

export function matchRegisteredFace(descriptor, registeredPersonnel = []) {
  if (!Array.isArray(descriptor) || descriptor.length !== 128) return null;

  const candidates = [];
  for (const person of registeredPersonnel) {
    const templates = Array.isArray(person.face_descriptors) ? person.face_descriptors : [];
    for (const template of templates) {
      if (!Array.isArray(template) || template.length !== descriptor.length) continue;
      const distance = Math.sqrt(
        descriptor.reduce((sum, value, index) => sum + (value - template[index]) ** 2, 0)
      );
      if (Number.isFinite(distance)) candidates.push({ person, distance });
    }
  }

  candidates.sort((a, b) => a.distance - b.distance);
  const best = candidates[0];
  const second = candidates.find(({ person }) => person.id !== best?.person.id);
  if (!best || best.distance > FACE_MATCH_THRESHOLD) return null;
  if (second && second.distance - best.distance < 0.06) return null;

  return {
    personnelId: best.person.id,
    distance: best.distance
  };
}

export const FACE_ENROLLMENT_SAMPLE_COUNT = MIN_FACE_SAMPLES;
