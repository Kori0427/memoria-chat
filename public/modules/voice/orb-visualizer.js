/**
 * Fluid Blob Orb Visualizer — Canvas 2D
 * Multi-layer morphing splines driven by audio volume and sine math.
 */

// Define color palettes based on state. 
// Using RGB arrays to smoothly interpolate.
const COLORS = {
  idle:       { base: [59, 130, 246],  glow: [14, 165, 233] },   // Blue to Sky
  listening:  { base: [16, 185, 129],  glow: [52, 211, 153] },   // Emerald green
  processing: { base: [245, 158, 11],  glow: [251, 191, 36] },   // Amber
  speaking:   { base: [139, 92, 246],  glow: [168, 85, 247] },   // Violet/Purple
};

const BASE_RADIUS = 70;
const BREATH_SPEED = 0.0015;
const MORPH_SPEED = 0.002;
const LERP_SPEED = 0.08;

export class OrbVisualizer {
  constructor(canvas) {
    this._canvas = canvas;
    this._ctx = canvas.getContext("2d");
    this._state = "idle";
    this._analyser = null;
    this._analyserData = null;
    
    // Core parameters interpolated over time
    this._currentVolume = 0;
    this._targetVolume = 0;
    
    // Color interpolation
    this._colorBase = [...COLORS.idle.base];
    this._colorGlow = [...COLORS.idle.glow];
    this._targetColorBase = [...COLORS.idle.base];
    this._targetColorGlow = [...COLORS.idle.glow];
    
    this._rafId = null;
    this._startTime = performance.now();
    this._running = false;
    
    // Pre-calculate geometry arrays
    this._numPoints = 120; // Resolution of the curve
    
    // Setup for High DPI screens
    this._setupCanvas();
    this._onResize = this._setupCanvas.bind(this);
    window.addEventListener('resize', this._onResize);
  }

  _setupCanvas() {
    // Handling device pixel ratio for sharp rendering
    const dpr = window.devicePixelRatio || 1;
    // We expect the CSS to set the actual display size, so we pull from clientWidth
    const rect = this._canvas.getBoundingClientRect();
    // Fallback if not mounted yet
    const width = rect.width || this._canvas.width || 300;
    const height = rect.height || this._canvas.height || 300;
    
    this._canvas.width = width * dpr;
    this._canvas.height = height * dpr;
    this._ctx.scale(dpr, dpr);
    
    this._cx = width / 2;
    this._cy = height / 2;
    this._cssRadiusScale = Math.min(width, height) / 350; // Scale morphing based on container
  }

  setState(state) {
    this._state = state;
    const colorDef = COLORS[state] || COLORS.idle;
    this._targetColorBase = [...colorDef.base];
    this._targetColorGlow = [...colorDef.glow];
  }

  setAnalyser(analyser) {
    this._analyser = analyser;
    if (analyser) {
      this._analyserData = new Uint8Array(analyser.frequencyBinCount);
    } else {
      this._analyserData = null;
    }
  }

  start() {
    if (this._running) return;
    this._running = true;
    this._startTime = performance.now();
    // Re-ensure canvas setup in case CSS loaded late
    this._setupCanvas();
    this._tick();
  }

  stop() {
    this._running = false;
    if (this._rafId) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    window.removeEventListener('resize', this._onResize);
  }

  _lerp(a, b, t) {
    return a + (b - a) * t;
  }

  _lerpColor(current, target, t) {
    for (let i = 0; i < 3; i++) {
      current[i] = this._lerp(current[i], target[i], t);
    }
  }

  _tick() {
    if (!this._running) return;
    this._rafId = requestAnimationFrame(() => this._tick());
    this._render();
  }

  _getBlobRadius(angle, time, layerOffset, volume) {
    // Combine various sine waves based on angle and time to create organic morphing
    // Add variations
    let noise = (Math.sin(angle * 3 + time * MORPH_SPEED + layerOffset) + 
                 Math.cos(angle * 5 - time * MORPH_SPEED * 0.8) +
                 Math.sin(angle * 2 + time * MORPH_SPEED * 1.2)) / 3;

    // Amplitude scales with volume. Idle has a tiny baseline amplitude
    const baseAmplitude = this._state === 'idle' ? 4 : 8;
    const volAmplitude = volume * 50; 
    const amplitude = (baseAmplitude + volAmplitude) * this._cssRadiusScale;
    
    // Breathing effect
    const breath = Math.sin(time * BREATH_SPEED) * (5 * this._cssRadiusScale);
    
    // Base radius scaled
    const baseR = BASE_RADIUS * this._cssRadiusScale;
    const dynamicBase = baseR + breath + (volume * 15 * this._cssRadiusScale);

    return dynamicBase + noise * amplitude;
  }

  _drawBlobLayer(ctx, time, layerIndex, volume, baseColor, glowColor) {
    const layerOffset = layerIndex * Math.PI * 0.6;
    const phaseShift = time * 0.001 * (layerIndex % 2 === 0 ? 1 : -1);
    
    ctx.beginPath();
    
    for (let i = 0; i <= this._numPoints; i++) {
      const angle = (i / this._numPoints) * Math.PI * 2;
      const r = this._getBlobRadius(angle, time, layerOffset, volume);
      
      // Calculate x, y
      const x = this._cx + Math.cos(angle + phaseShift) * r;
      const y = this._cy + Math.sin(angle + phaseShift) * r;
      
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.closePath();
    
    // Fill with gradient
    // Gradient direction slightly shifting
    const gx1 = this._cx + Math.cos(time * 0.001 + layerOffset) * BASE_RADIUS;
    const gy1 = this._cy + Math.sin(time * 0.001 + layerOffset) * BASE_RADIUS;
    const gx2 = this._cx - Math.cos(time * 0.001 + layerOffset) * BASE_RADIUS;
    const gy2 = this._cy - Math.sin(time * 0.001 + layerOffset) * BASE_RADIUS;
    
    const grad = ctx.createLinearGradient(gx1, gy1, gx2, gy2);
    
    if (layerIndex === 0) {
      // Background large blurred blob
      grad.addColorStop(0, `rgba(${baseColor.join(',')}, 0.2)`);
      grad.addColorStop(1, `rgba(${glowColor.join(',')}, 0.1)`);
      ctx.filter = `blur(${20 * this._cssRadiusScale}px)`;
    } else if (layerIndex === 1) {
      // Medium blob
      grad.addColorStop(0, `rgba(${baseColor.join(',')}, 0.6)`);
      grad.addColorStop(1, `rgba(${glowColor.join(',')}, 0.4)`);
      ctx.filter = `blur(${8 * this._cssRadiusScale}px)`;
    } else {
      // Core sharp blob
      grad.addColorStop(0, `rgba(${baseColor.join(',')}, 0.9)`);
      grad.addColorStop(1, `rgba(${glowColor.join(',')}, 0.8)`);
      ctx.filter = `blur(${2 * this._cssRadiusScale}px)`;
    }

    ctx.fillStyle = grad;
    ctx.fill();
    ctx.filter = 'none';
  }

  _render() {
    const ctx = this._ctx;
    const w = this._canvas.width / (window.devicePixelRatio || 1);
    const h = this._canvas.height / (window.devicePixelRatio || 1);
    const now = performance.now();

    // Volume processing
    if (this._analyser && this._analyserData) {
      this._analyser.getByteFrequencyData(this._analyserData);
      let sum = 0;
      for (let i = 0; i < this._analyserData.length; i++) {
        sum += this._analyserData[i];
      }
      this._targetVolume = sum / (this._analyserData.length * 255);
    } else if (this._state === "listening" && !this._analyser) {
      // Fallback pulse for browser STT
      this._targetVolume = Math.max(0, Math.sin(now * 0.005) * 0.5);
    } else if (this._state === "processing") {
      // Small ripples while processing
      this._targetVolume = Math.max(0, Math.sin(now * 0.008) * 0.2);
    } else {
      this._targetVolume = 0;
    }

    // Smooth volumetric interpolation
    this._currentVolume = this._lerp(this._currentVolume, this._targetVolume, 0.2);

    // Color interpolation
    this._lerpColor(this._colorBase, this._targetColorBase, LERP_SPEED);
    this._lerpColor(this._colorGlow, this._targetColorGlow, LERP_SPEED);

    // Round colors for CSS
    const baseC = this._colorBase.map(Math.round);
    const glowC = this._colorGlow.map(Math.round);

    // Clear canvas completely
    ctx.clearRect(0, 0, w, h);

    // Enable additive blending for layers
    ctx.globalCompositeOperation = "screen";

    // Draw 3 layers for organic feel
    this._drawBlobLayer(ctx, now, 0, this._currentVolume, baseC, glowC);
    this._drawBlobLayer(ctx, now, 1, this._currentVolume, baseC, glowC);
    this._drawBlobLayer(ctx, now, 2, this._currentVolume, baseC, glowC);

    // Reset blending
    ctx.globalCompositeOperation = "source-over";
    
    // Optional: add a subtle inner highlight to the core
    ctx.beginPath();
    ctx.arc(this._cx - BASE_RADIUS*0.3, this._cy - BASE_RADIUS*0.3, BASE_RADIUS*0.4, 0, Math.PI*2);
    const highlight = ctx.createRadialGradient(
      this._cx - BASE_RADIUS*0.3, this._cy - BASE_RADIUS*0.3, 0,
      this._cx - BASE_RADIUS*0.3, this._cy - BASE_RADIUS*0.3, BASE_RADIUS*0.4
    );
    highlight.addColorStop(0, `rgba(255, 255, 255, ${0.1 + this._currentVolume*0.2})`);
    highlight.addColorStop(1, 'rgba(255, 255, 255, 0)');
    ctx.fillStyle = highlight;
    ctx.fill();
  }
}
