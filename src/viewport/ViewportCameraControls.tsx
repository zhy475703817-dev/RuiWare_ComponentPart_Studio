import { useCallback, useEffect, useRef } from "react";
import { useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { MOUSE } from "three";
import type { PerspectiveCamera } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { readCameraView, writeCameraView } from "./cameraView";
import type { CameraView } from "./cameraView";

type Props = {
  enabled: boolean;
  radius: number;
  hasContent: boolean;
  resetToken: number;
  storageKey: string;
};

// Camera state belongs to the viewport, independently of member selection/transforms.
export function ViewportCameraControls({ enabled, radius, hasContent, resetToken, storageKey }: Props) {
  const camera = useThree(state => state.camera) as PerspectiveCamera;
  const controls = useRef<OrbitControlsImpl>(null);
  const initialized = useRef(false);
  const waitingForContent = useRef(false);
  const applyingView = useRef(false);
  const lastResetToken = useRef(resetToken);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestView = useRef<CameraView | null>(null);

  const captureView = useCallback(() => {
    if (controls.current) latestView.current = {
      version: 1,
      position: camera.position.toArray() as CameraView["position"],
      target: controls.current.target.toArray() as CameraView["target"],
      zoom: camera.zoom,
    };
    return latestView.current;
  }, [camera]);

  const saveView = useCallback(() => {
    if (saveTimer.current !== null) clearTimeout(saveTimer.current);
    saveTimer.current = null;
    if (!initialized.current || waitingForContent.current || applyingView.current) return;
    // React may have detached the controls ref before the unmount cleanup runs.
    const view = captureView();
    if (view) writeCameraView(view, storageKey);
  }, [captureView, storageKey]);

  const updateClipping = useCallback(() => {
    const far = Math.max(100000, camera.position.length() + radius * 100);
    if (camera.near !== .001 || camera.far !== far) {
      camera.near = .001;
      camera.far = far;
      camera.updateProjectionMatrix();
    }
  }, [camera, radius]);

  const scheduleSave = useCallback(() => {
    if (applyingView.current) return;
    updateClipping();
    captureView();
    if (saveTimer.current !== null) clearTimeout(saveTimer.current);
    // Include the final damping frames after the pointer is released.
    saveTimer.current = setTimeout(saveView, 120);
  }, [captureView, saveView, updateClipping]);

  const applyView = useCallback((view: CameraView) => {
    const orbit = controls.current;
    if (!orbit) return;
    applyingView.current = true;
    const damping = orbit.enableDamping;
    // Drain old orbit deltas so a reset cannot continue a previous drag.
    orbit.enableDamping = false;
    orbit.update();
    camera.position.set(...view.position);
    camera.up.set(0, 0, 1);
    camera.zoom = view.zoom;
    orbit.target.set(...view.target);
    updateClipping();
    camera.updateProjectionMatrix();
    orbit.update();
    orbit.enableDamping = damping;
    applyingView.current = false;
  }, [camera, updateClipping]);

  useEffect(() => {
    if (!controls.current) return;
    const firstContent = waitingForContent.current && hasContent;
    if (!initialized.current || firstContent) {
      const stored = !initialized.current ? readCameraView(storageKey) : null;
      const distance = Math.max(100, radius * 1.1) / Math.sin(camera.fov * Math.PI / 360) * 1.8;
      waitingForContent.current = !stored && !hasContent;
      applyView(stored ?? { version: 1, position: [distance, -distance, distance], target: [0, 0, 0], zoom: 1 });
      initialized.current = true;
      saveView();
    }
    // Updating the scene bounds may extend the clipping range, never move the camera.
    updateClipping();
  }, [applyView, camera, hasContent, radius, saveView, storageKey, updateClipping]);

  useEffect(() => {
    if (lastResetToken.current === resetToken || !controls.current) return;
    lastResetToken.current = resetToken;
    waitingForContent.current = false;
    const position = camera.position.length() > .01
      ? camera.position.toArray() as CameraView["position"]
      : [500, -500, 500] as CameraView["position"];
    applyView({ version: 1, position, target: [0, 0, 0], zoom: camera.zoom });
    saveView();
  }, [applyView, camera, resetToken, saveView]);

  useEffect(() => {
    const onVisibilityChange = () => { if (document.hidden) saveView(); };
    window.addEventListener("pagehide", saveView);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      saveView();
      window.removeEventListener("pagehide", saveView);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [saveView]);

  return <OrbitControls ref={controls} enabled={enabled} makeDefault
    minDistance={.01} maxDistance={1e12} screenSpacePanning
    mouseButtons={{ LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.PAN, RIGHT: MOUSE.PAN }}
    onStart={() => { waitingForContent.current = false; }}
    onChange={scheduleSave} onEnd={saveView}/>;
}
