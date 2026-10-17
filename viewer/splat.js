// GridTwin plant splat: assets/splats/plant.splat drawn with Spark (three.js), shared by the inspection wall
// (interactive) and viewer/splat-render.html (one frame per viewpoint, used by scripts/render_splat_views.py).
// scene.json carries asset anchors and one camera pose per viewpoint; it holds no hidden state.
import * as THREE from 'three';
import { SplatMesh } from '@sparkjsdev/spark';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export async function loadScene() {
  const res = await fetch('/assets/splats/scene.json', { cache: 'no-store' });
  if (!res.ok) throw new Error(`scene.json: HTTP ${res.status}`);
  return res.json();
}

export class PlantView {
  constructor(container, { interactive = true, width, height } = {}) {
    this.container = container;
    const w = width || container.clientWidth || 640, h = height || container.clientHeight || 400;
    this.renderer = new THREE.WebGLRenderer({ antialias: false, preserveDrawingBuffer: !interactive });
    this.renderer.setPixelRatio(interactive ? Math.min(devicePixelRatio, 2) : 1);
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xb9cfe0);
    this.scene.fog = null;
    this.camera = new THREE.PerspectiveCamera(50, w / h, 0.1, 2000);
    this.markers = new THREE.Group();
    this.scene.add(this.markers);
    this.overlays = new Map();
    if (interactive) {
      this.controls = new OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.maxPolarAngle = Math.PI * 0.49;
      new ResizeObserver(() => this.resize()).observe(container);
    }
  }

  async load(url = '/assets/splats/plant.splat', sky) {
    if (sky) this.scene.background = new THREE.Color().setRGB(...sky, THREE.SRGBColorSpace);
    this.plant = new SplatMesh({ url });
    this.scene.add(this.plant);
    await this.plant.initialized;
    return this;
  }

  async setOverlay(key, url) {
    if (this.overlays.has(key)) return;
    const m = new SplatMesh({ url });
    this.overlays.set(key, m);
    this.scene.add(m);
    await m.initialized;
  }

  look(position, target, fov) {
    this.camera.position.set(...position);
    if (fov) { this.camera.fov = fov; this.camera.updateProjectionMatrix(); }
    this.camera.lookAt(...target);
    if (this.controls) { this.controls.target.set(...target); this.controls.update(); }
    this.touch();
  }

  overview() { this.look([160, 150, 330], [160, 0, 115], 45); }

  // Station markers: a camera post with a coloured head and a short sightline toward its target.
  setStations(stations, colorOf) {
    this.markers.clear();
    for (const s of stations) {
      if (!s.pose) continue;
      const color = new THREE.Color(colorOf(s));
      const head = new THREE.Mesh(new THREE.SphereGeometry(2.2, 12, 8), new THREE.MeshBasicMaterial({ color }));
      head.position.set(s.pose.position[0], s.pose.position[1] + 1.5, s.pose.position[2]);
      head.userData.vp = s.id;
      const pts = [head.position.clone(), new THREE.Vector3(...s.pose.target)];
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.8 }));
      this.markers.add(head, line);
    }
    this.touch();
  }

  pick(clientX, clientY) {
    const r = this.renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1), this.camera);
    const hit = ray.intersectObjects(this.markers.children.filter((m) => m.userData.vp), false)[0];
    return hit?.object.userData.vp;
  }

  project(p) {
    const v = new THREE.Vector3(...p).project(this.camera);
    const c = this.renderer.domElement;
    return [((v.x + 1) / 2) * c.width, ((1 - v.y) / 2) * c.height, v.z];
  }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  // Thermal view: the splat drawn into a texture, then mapped to an ironbow palette. Ambient scene stays cool;
  // `hot` world points (assets with a failed check) glow, pinned to the asset from any angle.
  setThermal(on, hot = []) {
    this.thermal = on;
    this.hot = hot.slice(0, 8);
    this.touch();
    if (on && !this.post) {
      const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
      this.rt = new THREE.WebGLRenderTarget(size.x, size.y);
      this.post = new THREE.ShaderMaterial({
        uniforms: { tex: { value: this.rt.texture }, hot: { value: Array.from({ length: 8 }, () => new THREE.Vector3()) }, nHot: { value: 0 },
          aspect: { value: 1 } },
        vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }',
        fragmentShader: `uniform sampler2D tex; uniform vec3 hot[8]; uniform int nHot; uniform float aspect; varying vec2 vUv;
          vec3 ironbow(float t) {
            vec3 c0 = vec3(0.0, 0.0, 0.05), c1 = vec3(0.25, 0.0, 0.55), c2 = vec3(0.75, 0.1, 0.45), c3 = vec3(0.95, 0.45, 0.05), c4 = vec3(1.0, 0.85, 0.2), c5 = vec3(1.0, 1.0, 0.9);
            t = clamp(t, 0.0, 1.0) * 5.0;
            if (t < 1.0) return mix(c0, c1, t); if (t < 2.0) return mix(c1, c2, t - 1.0); if (t < 3.0) return mix(c2, c3, t - 2.0);
            if (t < 4.0) return mix(c3, c4, t - 3.0); return mix(c4, c5, t - 4.0);
          }
          void main() {
            vec3 c = texture2D(tex, vUv).rgb;
            float t = 0.16 + 0.3 * dot(c, vec3(0.3, 0.55, 0.15));
            for (int i = 0; i < 8; i++) {
              if (i >= nHot) break;
              vec2 d = (vUv - hot[i].xy) * vec2(aspect, 1.0);
              t = max(t, 0.98 * exp(-dot(d, d) / (2.0 * hot[i].z * hot[i].z)));
            }
            gl_FragColor = vec4(ironbow(t), 1.0);
          }`,
        depthTest: false, depthWrite: false,
      });
      this.postScene = new THREE.Scene();
      this.postScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.post));
      this.postCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    }
  }

  draw() {
    if (!this.thermal) { this.renderer.render(this.scene, this.camera); return; }
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    if (this.rt.width !== size.x || this.rt.height !== size.y) this.rt.setSize(size.x, size.y);
    this.markers.visible = false;
    this.renderer.setRenderTarget(this.rt);
    this.renderer.render(this.scene, this.camera);
    this.renderer.setRenderTarget(null);
    this.markers.visible = true;
    const u = this.post.uniforms;
    let n = 0;
    for (const p of this.hot) {
      const v = new THREE.Vector3(...p).project(this.camera);
      if (v.z > 1 || v.z < -1) continue;
      const dist = this.camera.position.distanceTo(new THREE.Vector3(...p));
      u.hot.value[n++].set((v.x + 1) / 2, (v.y + 1) / 2, Math.min(0.08, Math.max(0.006, 1.6 / dist / (this.camera.fov / 50))));
    }
    u.nHot.value = n;
    u.aspect.value = size.x / size.y;
    this.renderer.render(this.postScene, this.postCam);
    this.renderer.autoClear = false;
    this.renderer.render(this.markers, this.camera);  // camera stations stay in their status colours on top
    this.renderer.autoClear = true;
  }

  // Draws only while something changes (orbit, new markers, thermal, a fly-to), plus a few frames for Spark's sort.
  touch(frames = 12) { this.dirty = Math.max(this.dirty || 0, frames); }

  start() {
    const loop = () => {
      if (this.controls?.update() || this.animate?.()) this.touch();
      if (this.dirty > 0) { this.dirty--; this.draw(); }
      requestAnimationFrame(loop);
    };
    this.touch(30);
    loop();
  }

  async renderFrames(n = 3) {  // Spark sorts splats asynchronously; give it a few frames to settle
    for (let i = 0; i < n; i++) {
      this.renderer.render(this.scene, this.camera);
      await new Promise((r) => setTimeout(r, 150));
    }
    this.renderer.render(this.scene, this.camera);
  }
}
