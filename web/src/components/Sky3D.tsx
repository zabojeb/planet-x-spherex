/**
 * 3D views of a sequence (three.js, loaded only when a 3D view is opened). Relief: the frame on screen as a
 * height field, same colours and stretch as the flat sky. Time cube / λ cube: the frames of every epoch (by
 * date) or detector (by wavelength) stacked as glowing layers whose sky is transparent, so a still star is a
 * vertical column and a mover leans along its predicted track. Orientation is the sky's: north is the far
 * side, east is left. Drag to orbit, right-drag to pan, scroll to zoom, double-click to reset; click a
 * layer's label to show that epoch or detector.
 */
import { RotateCcw } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Progress } from "../api";
import { SERIES } from "../colors";
import type { Display } from "../display";
import { heightField, paintDisplay, sliceAlpha, type Cmap, type Display3D, type Stretch } from "../render";
import { loaderText } from "./SkyView";

export interface Layer {
  key: string;
  label: string;
  z: number;
  current: boolean;
  display: Display | null;
}

export interface TrackPoint3D {
  x: number;
  y: number;
  z: number | null; // 0..1 in the time cube, null elsewhere
  current: boolean;
}

export interface Sky3DProps {
  kind: "relief" | "time" | "bands";
  n: number;
  display: Display | null;
  layers: Layer[];
  stretch: Stretch;
  cmap: Cmap;
  smooth: boolean;
  height: number;
  spin: boolean;
  track: TrackPoint3D[];
  targetPx: [number, number] | null;
  showTarget: boolean;
  showTrack: boolean;
  showCompass: boolean;
  axisLabel: string;
  dateLabel: string;
  subLabel?: string;
  progress: Progress | null;
  error: string | null;
  onRetry: () => void;
  lockKey: string;
  onPick: (key: string) => void;
  onInteract: () => void;
  shiftX?: number;
}

// Theme colours (styles.css): text, hairlines and the one accent for the current layer.
const TEXT_1 = "#ece8df";
const TEXT_3 = "#858074";
const ACCENT = "#ffc845";
const TRACK = SERIES.orange;

const SRGB_TO_LINEAR = (() => {
  const t = new Float32Array(256);
  for (let i = 0; i < 256; i++) {
    const c = i / 255;
    t[i] = c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }
  return t;
})();

const hasPixels = (d: Display | null): d is Display & Display3D => !!d && d.kind !== "empty";

function textSprite(text: string, color: string, size: number, anchor: "left" | "right" | "center" = "left"): THREE.Sprite {
  const px = 44;
  const font = `500 ${px}px "Geist Mono Variable", ui-monospace, Menlo, monospace`;
  const cv = document.createElement("canvas");
  const ctx = cv.getContext("2d")!;
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + 16;
  cv.width = w;
  cv.height = px + 16;
  ctx.font = font;
  ctx.textBaseline = "middle";
  ctx.shadowColor = "rgba(0,0,0,0.9)";
  ctx.shadowBlur = 8;
  ctx.fillStyle = color;
  ctx.fillText(text, 8, cv.height / 2);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false });
  const s = new THREE.Sprite(mat);
  s.center.set(anchor === "left" ? 0 : anchor === "right" ? 1 : 0.5, 0.5);
  s.scale.set((size * cv.width) / cv.height, size, 1);
  s.renderOrder = 20;
  return s;
}

function disposeTree(o: THREE.Object3D) {
  o.traverse((c) => {
    const m = c as THREE.Mesh;
    m.geometry?.dispose();
    const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
    for (const mat of mats) {
      (mat as THREE.MeshBasicMaterial).map?.dispose();
      mat.dispose();
    }
  });
}

function square(n: number, y: number, color: string, opacity: number): THREE.LineLoop {
  const h = n / 2;
  const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-h, y, -h), new THREE.Vector3(h, y, -h), new THREE.Vector3(h, y, h), new THREE.Vector3(-h, y, h)]);
  return new THREE.LineLoop(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
}

interface LayerObj {
  group: THREE.Group;
  canvas: HTMLCanvasElement;
  tex: THREE.CanvasTexture;
  mat: THREE.MeshBasicMaterial;
  label: THREE.Sprite | null;
  labelText: string;
  current: boolean;
}

/** Everything three.js: renderer, camera, controls and the scene graph; React only feeds it props. */
class Stage {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(34, 1, 0.5, 20000);
  readonly controls: OrbitControls;
  private relief: { mesh: THREE.Mesh; n: number } | null = null;
  private layers = new Map<string, LayerObj>();
  private layerGeo: { geo: THREE.PlaneGeometry; n: number } | null = null;
  private decor = new THREE.Group();
  private marks = new THREE.Group();
  private pending = false;
  private w = 1;
  private h = 1;
  shiftX = 0;
  home = { pos: new THREE.Vector3(), target: new THREE.Vector3() };

  constructor(host: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0x000000, 1);
    host.prepend(this.renderer.domElement);
    this.renderer.domElement.className = "sky3d-canvas";
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.rotateSpeed = 0.7;
    this.controls.autoRotateSpeed = 0.8;
    this.controls.maxPolarAngle = Math.PI * 0.62;
    this.controls.addEventListener("change", this.request);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x30302c, 1.25));
    const sun = new THREE.DirectionalLight(0xffffff, 1.9);
    sun.position.set(-0.55, 1, 0.65);
    this.scene.add(sun);
    this.scene.add(this.decor, this.marks);
  }

  request = () => {
    if (this.pending) return;
    this.pending = true;
    requestAnimationFrame(this.tick);
  };

  private tick = () => {
    this.pending = false;
    const moved = this.controls.update();
    this.renderer.render(this.scene, this.camera);
    if (moved || this.controls.autoRotate) this.request();
  };

  resize(w: number, h: number) {
    this.w = Math.max(1, w);
    this.h = Math.max(1, h);
    this.renderer.setSize(this.w, this.h, false);
    this.camera.aspect = this.w / this.h;
    this.applyShift();
  }

  applyShift() {
    if (this.shiftX) this.camera.setViewOffset(this.w, this.h, -this.shiftX, 0, this.w, this.h);
    else this.camera.clearViewOffset();
    this.camera.updateProjectionMatrix();
    this.request();
  }

  /** Camera looking from the south-east-ish, high enough to read the floor, far enough to fit the scene. */
  frame(n: number, top: number, cube: boolean) {
    const target = new THREE.Vector3(0, top * (cube ? 0.45 : 0.2), 0);
    const r = 0.5 * Math.hypot(n * Math.SQRT2, top);
    const fov = (this.camera.fov * Math.PI) / 180;
    const fit = Math.min(fov, 2 * Math.atan(Math.tan(fov / 2) * Math.min(1, this.camera.aspect)));
    const dist = (r / Math.sin(fit / 2)) * (cube ? 1.22 : 0.9);
    const el = ((cube ? 24 : 40) * Math.PI) / 180;
    const az = ((cube ? -32 : -18) * Math.PI) / 180;
    const pos = new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), Math.cos(az) * Math.cos(el)).multiplyScalar(dist).add(target);
    this.home = { pos, target };
    this.controls.minDistance = n * 0.25;
    this.controls.maxDistance = dist * 4;
    this.reset();
  }

  reset() {
    this.camera.position.copy(this.home.pos);
    this.controls.target.copy(this.home.target);
    this.controls.update();
    this.request();
  }

  // ---------------------------------------------------------------- relief
  setRelief(d: Display3D | null, n: number, stretch: Stretch, cmap: Cmap, top: number, smooth: boolean) {
    if (!d) {
      if (this.relief) this.relief.mesh.visible = false;
      this.request();
      return;
    }
    if (!this.relief || this.relief.n !== n) {
      if (this.relief) {
        this.scene.remove(this.relief.mesh);
        disposeTree(this.relief.mesh);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * n * 3), 3));
      geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(n * n * 3), 3));
      const idx = new Uint32Array((n - 1) * (n - 1) * 6);
      let k = 0;
      for (let y = 0; y < n - 1; y++)
        for (let x = 0; x < n - 1; x++) {
          const a = y * n + x;
          idx.set([a, a + 1, a + n, a + 1, a + n + 1, a + n], k);
          k += 6;
        }
      geo.setIndex(new THREE.BufferAttribute(idx, 1));
      const mat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.82, metalness: 0, side: THREE.DoubleSide });
      this.relief = { mesh: new THREE.Mesh(geo, mat), n };
      this.scene.add(this.relief.mesh);
    }
    const { mesh } = this.relief;
    mesh.visible = true;
    const mat = mesh.material as THREE.MeshStandardMaterial;
    if (mat.flatShading !== !smooth) {
      mat.flatShading = !smooth;
      mat.needsUpdate = true;
    }
    const geo = mesh.geometry;
    const pos = geo.getAttribute("position") as THREE.BufferAttribute;
    const col = geo.getAttribute("color") as THREE.BufferAttribute;
    const P = pos.array as Float32Array;
    const C = col.array as Float32Array;
    const h = heightField(d);
    const img = new ImageData(n, n);
    paintDisplay(d, n, stretch, cmap, img);
    const c = (n - 1) / 2;
    for (let y = 0; y < n; y++)
      for (let x = 0; x < n; x++) {
        const i = y * n + x;
        const o = ((n - 1 - y) * n + x) * 4; // painted rows are flipped (north up)
        const v = h[i];
        P[i * 3] = x - c;
        P[i * 3 + 1] = Number.isFinite(v) ? v * top : 0;
        P[i * 3 + 2] = -(y - c);
        C[i * 3] = SRGB_TO_LINEAR[img.data[o]];
        C[i * 3 + 1] = SRGB_TO_LINEAR[img.data[o + 1]];
        C[i * 3 + 2] = SRGB_TO_LINEAR[img.data[o + 2]];
      }
    pos.needsUpdate = true;
    col.needsUpdate = true;
    geo.computeVertexNormals();
    geo.computeBoundingSphere();
    this.request();
  }

  // ---------------------------------------------------------------- cubes
  setLayers(layers: Layer[], n: number, stretch: Stretch, cmap: Cmap, smooth: boolean, top: number, onScene: boolean) {
    if (!this.layerGeo || this.layerGeo.n !== n) {
      for (const l of this.layers.values()) this.dropLayer(l);
      this.layers.clear();
      this.layerGeo?.geo.dispose();
      this.layerGeo = { geo: new THREE.PlaneGeometry(n, n), n };
    }
    const want = new Set(onScene ? layers.map((l) => l.key) : []);
    for (const [k, l] of this.layers)
      if (!want.has(k)) {
        this.dropLayer(l);
        this.layers.delete(k);
      }
    if (!onScene) return this.request();
    const label = n * 0.03;
    // Layers close in z (exposures taken minutes apart) would overprint their labels: label the current
    // layer, then the others bottom-up wherever there is room. Many layers also add up to white under
    // additive blending, so the others fade with their number.
    const labelled = new Set<string>();
    const kept: number[] = [];
    for (const L of [...layers].sort((a, b) => Number(b.current) - Number(a.current) || a.z - b.z))
      if (kept.every((z) => Math.abs(z - L.z) * top > label * 1.2)) (labelled.add(L.key), kept.push(L.z));
    const fade = Math.min(0.8, Math.max(0.12, 2.4 / layers.length));
    const frameAlpha = 0.16 * Math.min(1, 6 / layers.length);
    for (const L of layers) {
      let o = this.layers.get(L.key);
      if (!o) {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = n;
        const tex = new THREE.CanvasTexture(canvas);
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.generateMipmaps = false;
        tex.minFilter = THREE.LinearFilter;
        const mat = new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending });
        const plane = new THREE.Mesh(this.layerGeo.geo, mat);
        plane.rotation.x = -Math.PI / 2; // texture top (north) to the far side
        const group = new THREE.Group();
        group.add(plane);
        o = { group, canvas, tex, mat, label: null, labelText: "", current: L.current };
        this.layers.set(L.key, o);
        this.scene.add(group);
      }
      o.group.position.y = L.z * top;
      o.tex.magFilter = smooth ? THREE.LinearFilter : THREE.NearestFilter;
      if (hasPixels(L.display)) {
        const ctx = o.canvas.getContext("2d")!;
        const img = ctx.createImageData(n, n);
        paintDisplay(L.display, n, stretch, cmap, img);
        sliceAlpha(heightField(L.display), n, img);
        ctx.putImageData(img, 0, 0);
        o.tex.needsUpdate = true;
      }
      o.mat.opacity = L.current ? 1 : fade;
      if (o.labelText !== L.label || o.current !== L.current) {
        // frame + label are rebuilt only when the current layer changes
        for (const c of o.group.children.slice(1)) (o.group.remove(c), disposeTree(c));
        o.group.add(square(n, 0, L.current ? ACCENT : TEXT_1, L.current ? 0.9 : frameAlpha));
        o.label = textSprite(L.label, L.current ? ACCENT : TEXT_3, label, "right");
        o.label.position.set(-n / 2 - n * 0.03, 0, n / 2);
        o.label.userData.key = L.key;
        o.group.add(o.label);
        o.labelText = L.label;
        o.current = L.current;
      }
      if (o.label) o.label.visible = labelled.has(L.key);
    }
    this.request();
  }

  private dropLayer(l: LayerObj) {
    this.scene.remove(l.group);
    l.tex.dispose();
    l.mat.dispose();
    for (const c of l.group.children.slice(1)) disposeTree(c);
  }

  /** Layer label under a screen point, if any. */
  pick(x: number, y: number): string | null {
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2((x / this.w) * 2 - 1, -(y / this.h) * 2 + 1), this.camera);
    const labels = [...this.layers.values()].flatMap((l) => (l.label?.visible ? [l.label] : []));
    const hit = ray.intersectObjects(labels, false)[0];
    return (hit?.object.userData.key as string | undefined) ?? null;
  }

  // ---------------------------------------------------------------- frame, compass, target, track
  setDecor(p: { n: number; top: number; cube: boolean; axisLabel: string; compass: boolean }) {
    for (const c of [...this.decor.children]) (this.decor.remove(c), disposeTree(c));
    const { n, top } = p;
    const h = n / 2;
    if (p.cube) {
      const box = new THREE.LineSegments(
        new THREE.EdgesGeometry(new THREE.BoxGeometry(n, top, n)),
        new THREE.LineBasicMaterial({ color: TEXT_1, transparent: true, opacity: 0.1, depthWrite: false }),
      );
      box.position.y = top / 2;
      this.decor.add(box);
      const ax = textSprite(`${p.axisLabel} ↑`, TEXT_3, n * 0.032, "left");
      ax.position.set(h + n * 0.03, top, -h);
      this.decor.add(ax);
    } else {
      this.decor.add(square(n, 0, TEXT_1, 0.24));
    }
    if (p.compass) {
      const N = textSprite("N", TEXT_1, n * 0.04, "center");
      N.position.set(0, 0, -h - n * 0.06);
      const E = textSprite("E", TEXT_1, n * 0.04, "center");
      E.position.set(-h - n * 0.06, 0, 0);
      this.decor.add(N, E);
    }
    this.request();
  }

  setMarks(p: { n: number; top: number; track: TrackPoint3D[]; target: [number, number] | null; targetZ: number; showTarget: boolean; showTrack: boolean; kind: Sky3DProps["kind"] }) {
    for (const c of [...this.marks.children]) (this.marks.remove(c), disposeTree(c));
    const c = (p.n - 1) / 2;
    const at = (x: number, y: number, z: number) => new THREE.Vector3(x - c, z, -(y - c));
    const overlay = { transparent: true, depthTest: false, depthWrite: false };
    if (p.showTrack && p.track.length > 1) {
      // points without a time lie on the ground in the relief and on the current layer in the λ cube, with the crosshair
      const flat = p.kind === "relief" ? 0 : p.targetZ;
      const pts = p.track.map((t) => at(t.x, t.y, t.z == null ? flat : t.z * p.top));
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: TRACK, opacity: 0.85, ...overlay }));
      line.renderOrder = 10;
      this.marks.add(line);
      const dot = new THREE.SphereGeometry(1, 12, 8);
      p.track.forEach((t, i) => {
        const m = new THREE.Mesh(dot, new THREE.MeshBasicMaterial({ color: TRACK, opacity: t.current ? 1 : 0.7, ...overlay }));
        m.position.copy(pts[i]);
        m.scale.setScalar(p.n * (t.current ? 0.011 : 0.007));
        m.renderOrder = 11;
        this.marks.add(m);
      });
    }
    if (p.showTarget && p.target) {
      // the 2D crosshair (four arms with a gap), lying on the current layer, and a stalk in the relief
      const [x, y] = p.target;
      const z = p.targetZ;
      const a = p.n * 0.035;
      const b = p.n * 0.08;
      const segs = [[-b, 0, -a, 0], [a, 0, b, 0], [0, -b, 0, -a], [0, a, 0, b]].flatMap(([x0, y0, x1, y1]) => [at(x + x0, y + y0, z), at(x + x1, y + y1, z)]);
      if (p.kind === "relief") segs.push(at(x, y, 0), at(x, y, p.top * 1.12));
      const cross = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(segs), new THREE.LineBasicMaterial({ color: TEXT_1, opacity: 0.95, ...overlay }));
      cross.renderOrder = 12;
      this.marks.add(cross);
    }
    this.request();
  }

  dispose() {
    this.controls.dispose();
    disposeTree(this.scene);
    this.layerGeo?.geo.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}

export default function Sky3D(p: Sky3DProps) {
  const host = useRef<HTMLDivElement>(null);
  const stage = useRef<Stage | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const cube = p.kind !== "relief";
  const top = Math.max(0.05, p.height) * p.n * (cube ? 0.85 : 0.3);

  useLayoutEffect(() => {
    if (!host.current) return;
    let s: Stage;
    try {
      s = new Stage(host.current);
    } catch (e) {
      setFailed("3D needs WebGL, which this browser does not provide.");
      return;
    }
    stage.current = s;
    const ro = new ResizeObserver(([e]) => s.resize(e.contentRect.width, e.contentRect.height));
    ro.observe(host.current);
    const b = host.current.getBoundingClientRect();
    s.resize(b.width, b.height);
    return () => {
      ro.disconnect();
      s.dispose();
      stage.current = null;
    };
  }, []);

  // Reframe on a new sequence or a different 3D view; redraws and height changes keep the camera.
  useEffect(() => {
    if (p.n) stage.current?.frame(p.n, top, cube);
  }, [p.lockKey, p.kind, p.n]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const s = stage.current;
    if (!s) return;
    s.shiftX = p.shiftX ?? 0;
    s.applyShift();
  }, [p.shiftX]);

  useEffect(() => {
    const s = stage.current;
    if (!s) return;
    s.controls.autoRotate = p.spin;
    s.request();
  }, [p.spin]);

  useEffect(() => {
    stage.current?.setRelief(!cube && hasPixels(p.display) ? p.display : null, p.n, p.stretch, p.cmap, top, p.smooth);
  }, [cube, p.display, p.n, p.stretch, p.cmap, top, p.smooth]);

  useEffect(() => {
    stage.current?.setLayers(p.layers, p.n, p.stretch, p.cmap, p.smooth, top, cube);
  }, [cube, p.layers, p.n, p.stretch, p.cmap, p.smooth, top]);

  useEffect(() => {
    if (p.n) stage.current?.setDecor({ n: p.n, top, cube, axisLabel: p.axisLabel, compass: p.showCompass });
  }, [p.n, top, cube, p.axisLabel, p.showCompass]);

  const current = p.layers.find((l) => l.current);
  const targetZ = cube ? (current?.z ?? p.track.find((t) => t.current)?.z ?? 0) * top : top * 0.02;
  const trackKey = p.track.map((t) => `${t.x.toFixed(2)},${t.y.toFixed(2)},${t.z ?? ""},${t.current ? 1 : 0}`).join(";");
  useEffect(() => {
    if (p.n) stage.current?.setMarks({ n: p.n, top, track: p.track, target: p.targetPx, targetZ, showTarget: p.showTarget, showTrack: p.showTrack, kind: p.kind });
  }, [p.n, top, trackKey, p.targetPx?.[0], p.targetPx?.[1], targetZ, p.showTarget, p.showTrack, p.kind]); // eslint-disable-line react-hooks/exhaustive-deps

  // click (no drag) on a layer label picks that epoch or detector; double-click resets the camera
  const down = useRef<{ x: number; y: number } | null>(null);
  const local = (e: React.PointerEvent | React.MouseEvent): [number, number] => {
    const b = host.current!.getBoundingClientRect();
    return [e.clientX - b.left, e.clientY - b.top];
  };
  const [hover, setHover] = useState(false);

  const loader = p.progress ? loaderText(p.progress) : null;
  const empty = cube ? (!p.layers.length ? "No layers to stack for this selection" : null) : p.display?.kind === "empty" ? p.display.message : null;

  return (
    <div
      className={`sky sky3d${hover ? " pointing" : ""}${p.progress ? " dim" : ""}`}
      ref={host}
      onPointerDown={(e) => {
        down.current = { x: e.clientX, y: e.clientY };
        p.onInteract();
      }}
      onPointerMove={(e) => {
        if (!cube || e.buttons) return;
        const [x, y] = local(e);
        setHover(!!stage.current?.pick(x, y));
      }}
      onPointerUp={(e) => {
        const d = down.current;
        down.current = null;
        if (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) > 4 || !cube) return;
        const [x, y] = local(e);
        const key = stage.current?.pick(x, y);
        if (key) p.onPick(key);
      }}
      onDoubleClick={() => stage.current?.reset()}
      onWheel={() => p.onInteract()}
    >
      <div className="hud-min" aria-hidden>
        <div className="hud-line">
          <span>{p.dateLabel}</span>
          {p.subLabel && <span className="muted">{p.subLabel}</span>}
          <span className="muted hud-hint">Drag to orbit · right-drag to pan · scroll to zoom · double-click to reset{cube ? " · click a label to show it" : ""}</span>
        </div>
      </div>
      {loader && (
        <div className="hud-loader" role="status" aria-live="polite">
          <div className="hud-loader-text">
            {loader.text}
            {loader.pct != null && <span> — {loader.pct}%</span>}
          </div>
          <div className={`hud-loader-bar${loader.pct == null ? " indeterminate" : ""}`}>
            <span style={loader.pct == null ? undefined : { width: `${Math.max(2, loader.pct)}%` }} />
          </div>
        </div>
      )}
      {(failed || (empty && !p.progress)) && <div className="sky-note glass">{failed ?? empty}</div>}
      {p.error && !p.progress && (
        <div className="sky-note glass error" role="alert" onPointerDown={(e) => e.stopPropagation()}>
          <span>{p.error}</span>
          <button className="btn" onClick={p.onRetry}>
            <RotateCcw size={14} /> Try again
          </button>
        </div>
      )}
    </div>
  );
}
