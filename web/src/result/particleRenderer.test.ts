import { expect, it, vi } from "vitest";
import { createParticleRenderer } from "./particleRenderer";

it("uploads every particle without a density cap and renders them in one GPU call", () => {
  const gl = {
    createShader: vi.fn(() => ({})), shaderSource: vi.fn(), compileShader: vi.fn(),
    getShaderParameter: vi.fn(() => true), deleteShader: vi.fn(),
    createProgram: vi.fn(() => ({})), attachShader: vi.fn(), linkProgram: vi.fn(),
    getProgramParameter: vi.fn(() => true), createBuffer: vi.fn(() => ({})),
    getUniformLocation: vi.fn(() => ({})), viewport: vi.fn(), clearColor: vi.fn(), clear: vi.fn(),
    useProgram: vi.fn(), uniform2f: vi.fn(), uniform1f: vi.fn(), bindBuffer: vi.fn(),
    bufferData: vi.fn(), getAttribLocation: vi.fn(() => 0), enableVertexAttribArray: vi.fn(),
    vertexAttribPointer: vi.fn(), enable: vi.fn(), blendFuncSeparate: vi.fn(), drawArrays: vi.fn(),
    deleteBuffer: vi.fn(), deleteProgram: vi.fn(), POINTS: 0, FLOAT: 5126,
  };
  const canvas = { getContext: vi.fn(() => gl), width: 1600, height: 1200 } as unknown as HTMLCanvasElement;
  const renderer = createParticleRenderer(canvas)!;
  renderer.begin(800, 600, 2);
  for (let index = 0; index < 2049; index++) renderer.point(index, 10, "#2DC4B2", 0.5, 6.4, 0.56);
  renderer.finish();
  expect(gl.drawArrays).toHaveBeenCalledExactlyOnceWith(0, 0, 2049);
  const uploaded = gl.bufferData.mock.calls[0][1] as unknown as Float32Array;
  expect(uploaded.length).toBe(2049 * 8);
  expect(uploaded[(2049 - 1) * 8]).toBe(2048);
  expect(uploaded[5]).toBe(0.5);
  renderer.dispose();
  expect(gl.deleteBuffer).toHaveBeenCalledTimes(1);
  expect(gl.deleteProgram).toHaveBeenCalledTimes(1);
});
