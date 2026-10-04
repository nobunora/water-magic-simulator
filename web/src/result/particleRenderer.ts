/** One GPU draw for particle heads and fading history; no per-particle Canvas strokes. */
export function createParticleRenderer(canvas: HTMLCanvasElement) {
  const gl = canvas.getContext("webgl", { alpha: true, preserveDrawingBuffer: true, antialias: false });
  if (!gl?.createShader) return null;
  const compile = (type: number, source: string) => {
    const shader = gl.createShader(type)!;
    gl.shaderSource(shader, source); gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const error = gl.getShaderInfoLog(shader); gl.deleteShader(shader);
      throw new Error(error ?? "Particle shader compilation failed");
    }
    return shader;
  };
  const vertex = compile(gl.VERTEX_SHADER, `
    attribute vec2 position; attribute vec4 color; attribute vec2 size;
    uniform vec2 viewport; uniform float ratio;
    varying vec4 pointColor; varying float inner;
    void main() {
      gl_Position = vec4(position.x / viewport.x * 2.0 - 1.0, 1.0 - position.y / viewport.y * 2.0, 0.0, 1.0);
      gl_PointSize = size.x * ratio; pointColor = color; inner = size.y;
    }`);
  const fragment = compile(gl.FRAGMENT_SHADER, `
    precision mediump float; varying vec4 pointColor; varying float inner;
    void main() {
      float radius = length(gl_PointCoord - vec2(0.5)) * 2.0;
      float edge = 1.0 - smoothstep(0.8, 1.0, radius);
      vec3 rgb = mix(pointColor.rgb, vec3(1.0), smoothstep(inner - 0.12, inner + 0.12, radius));
      gl_FragColor = vec4(rgb, pointColor.a * edge);
    }`);
  const program = gl.createProgram()!;
  gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
  gl.deleteShader(vertex); gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? "Particle shader linking failed");
  const buffer = gl.createBuffer()!;
  const viewport = gl.getUniformLocation(program, "viewport"), ratio = gl.getUniformLocation(program, "ratio");
  const attributes = [["position", 2, 0], ["color", 4, 8], ["size", 2, 24]] as const;
  let vertices = new Float32Array(8192), count = 0;
  const colors = new Map<string, number[]>();
  return {
    begin(width: number, height: number, pixelRatio: number) {
      count = 0; gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(program); gl.uniform2f(viewport, width, height); gl.uniform1f(ratio, pixelRatio);
    },
    point(x: number, y: number, hex: string, alpha: number, diameter: number, inner: number) {
      if (count + 8 > vertices.length) {
        const expanded = new Float32Array(vertices.length * 2); expanded.set(vertices); vertices = expanded;
      }
      let rgb = colors.get(hex);
      if (!rgb) { rgb = [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255); colors.set(hex, rgb); }
      vertices[count++] = x; vertices[count++] = y;
      vertices[count++] = rgb[0]; vertices[count++] = rgb[1]; vertices[count++] = rgb[2];
      vertices[count++] = alpha; vertices[count++] = diameter; vertices[count++] = inner;
    },
    finish() {
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, vertices.subarray(0, count), gl.DYNAMIC_DRAW);
      for (const [name, components, offset] of attributes) {
        const attribute = gl.getAttribLocation(program, name);
        gl.enableVertexAttribArray(attribute); gl.vertexAttribPointer(attribute, components, gl.FLOAT, false, 32, offset);
      }
      gl.enable(gl.BLEND);
      gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.drawArrays(gl.POINTS, 0, count / 8);
    },
    clear() { gl.clear(gl.COLOR_BUFFER_BIT); },
    dispose() { gl.clear(gl.COLOR_BUFFER_BIT); gl.deleteBuffer(buffer); gl.deleteProgram(program); },
  };
}
