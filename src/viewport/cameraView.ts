export const DEFAULT_CAMERA_STORAGE_KEY = "component-viewport-camera-v1";

export type CameraView = {
  version: 1;
  position: [number, number, number];
  target: [number, number, number];
  zoom: number;
};

// Reference framing for the 10,000 mm ground grid; this is a radial distance,
// independent of member parameters or the distance before the reset.
export const RESET_CAMERA_DISTANCE_MM = 16000;
const REFERENCE_ASPECT = 16 / 9;

export function createResetCameraView(position: CameraView["position"], target: CameraView["target"], aspect: number): CameraView {
  const offset = position.map((coordinate, index) => coordinate - target[index]);
  let length = Math.hypot(...offset);
  if (!Number.isFinite(length) || length < .001) {
    offset.splice(0, 3, 1, -1, 1);
    length = Math.sqrt(3);
  }
  const safeAspect = Number.isFinite(aspect) && aspect > 0 ? aspect : REFERENCE_ASPECT;
  const distance = RESET_CAMERA_DISTANCE_MM * Math.max(1, REFERENCE_ASPECT / safeAspect);
  return {
    version: 1,
    position: offset.map(coordinate => coordinate / length * distance) as CameraView["position"],
    target: [0, 0, 0],
    zoom: 1,
  };
}

// Keep corrupted storage from producing unstable projection matrices. These
// limits are far outside the platform's normal millimetre-scale assemblies.
const MAX_COORDINATE = 1e12;
const MIN_DISTANCE = 1e-9;
const MIN_ZOOM = 1e-6;
const MAX_ZOOM = 1e6;

function isVector(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3
    && value.every(coordinate => typeof coordinate === "number"
      && Number.isFinite(coordinate) && Math.abs(coordinate) <= MAX_COORDINATE);
}

function validatedView(value: unknown): CameraView | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const view = value as Partial<CameraView>;
  if (view.version !== 1 || !isVector(view.position) || !isVector(view.target)
    || typeof view.zoom !== "number" || !Number.isFinite(view.zoom)
    || view.zoom < MIN_ZOOM || view.zoom > MAX_ZOOM) return null;
  const distance = Math.hypot(...view.position.map((coordinate, index) => coordinate - view.target![index]));
  if (!Number.isFinite(distance) || distance <= MIN_DISTANCE) return null;
  return { version: 1, position: [...view.position], target: [...view.target], zoom: view.zoom };
}

export function decodeCameraView(raw: string | null): CameraView | null {
  if (raw === null) return null;
  try {
    return validatedView(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function readCameraView(storageKey = DEFAULT_CAMERA_STORAGE_KEY): CameraView | null {
  try {
    return decodeCameraView(globalThis.localStorage.getItem(storageKey));
  } catch {
    // Storage is optional (for example in private browsing or server rendering).
    return null;
  }
}

export function writeCameraView(view: CameraView, storageKey = DEFAULT_CAMERA_STORAGE_KEY): void {
  const validated = validatedView(view);
  if (!validated) return;
  try {
    globalThis.localStorage.setItem(storageKey, JSON.stringify(validated));
  } catch {
    // A disabled or full store must not interrupt camera interaction.
  }
}
