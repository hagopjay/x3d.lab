/* Deterministic WebGPU smoke test; no framework, model download, or third-party request. */
(() => {
  'use strict';
  const el = (id) => document.getElementById(id);
  const show = (id, value) => { el(id).textContent = value == null ? '—' : String(value); };
  const SOFTWARE_HINT = /swiftshader|lavapipe|llvmpipe|software|microsoft basic render/i;

  function webglProbe() {
    try {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl2', { failIfMajorPerformanceCaveat: false });
      if (!gl) return { available: false, renderer: null, vendor: null, maxTextureSize: null };
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return {
        available: true,
        renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'masked',
        vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : 'masked',
        maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
      };
    } catch (error) {
      return { available: false, error: String(error?.message || error) };
    }
  }

  async function run() {
    const startedAt = new Date().toISOString();
    const result = {
      schemaVersion: 1,
      test: 'webgpu-vector-transform-smoke',
      startedAt,
      context: { origin: location.origin, secureContext: Boolean(isSecureContext), userAgent: navigator.userAgent },
      webgl2: webglProbe(),
      webgpu: {
        apiAvailable: Boolean(navigator.gpu), status: 'unsupported',
        adapter: null, device: null, compute: null,
      },
      notes: [
        'A passing compute check validates WebGPU functionality, not a physical-GPU identity.',
        'submitReadbackMs includes queue submission and buffer readback; it is not a kernel-only benchmark.',
      ],
    };
    show('context', `${location.origin} · secure=${isSecureContext}`);
    show('webgl', result.webgl2.available ? `${result.webgl2.vendor} / ${result.webgl2.renderer}` : 'not available');
    show('api', result.webgpu.apiAvailable ? 'navigator.gpu present' : 'navigator.gpu missing');
    show('adapter', 'requesting adapter…');
    show('correctness', 'pending');
    show('timing', 'pending');
    el('status').className = 'status';
    el('status-text').textContent = 'running';

    if (!navigator.gpu) {
      result.webgpu.reason = isSecureContext ? 'navigator.gpu is not exposed in this browser/context' : 'WebGPU requires a secure context; this origin is not secure';
      result.finishedAt = new Date().toISOString();
      render(result);
      return result;
    }

    let adapter;
    try {
      adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
    } catch (error) {
      result.webgpu.status = 'error';
      result.webgpu.reason = `requestAdapter rejected: ${String(error?.message || error)}`;
      result.finishedAt = new Date().toISOString();
      render(result);
      return result;
    }
    if (!adapter) {
      result.webgpu.status = 'no-adapter';
      result.webgpu.reason = 'requestAdapter returned null; browser or host has no usable WebGPU adapter';
      result.finishedAt = new Date().toISOString();
      render(result);
      return result;
    }

    let adapterInfo = {};
    try {
      const info = adapter.info || (typeof adapter.requestAdapterInfo === 'function' ? await adapter.requestAdapterInfo() : null);
      if (info) adapterInfo = {
        vendor: info.vendor || '', architecture: info.architecture || '', device: info.device || '', description: info.description || '',
      };
    } catch (error) {
      adapterInfo = { metadataError: String(error?.message || error) };
    }
    const infoText = Object.values(adapterInfo).filter((v) => typeof v === 'string' && v).join(' · ');
    const softwareLikely = SOFTWARE_HINT.test(infoText);
    result.webgpu.adapter = {
      info: adapterInfo,
      classHint: softwareLikely ? 'software-likely (heuristic)' : 'unknown (browser metadata is not hardware attestation)',
      features: [...adapter.features].sort(),
      limits: {
        maxBufferSize: adapter.limits.maxBufferSize,
        maxStorageBufferBindingSize: adapter.limits.maxStorageBufferBindingSize,
        maxComputeWorkgroupSizeX: adapter.limits.maxComputeWorkgroupSizeX,
        maxComputeWorkgroupsPerDimension: adapter.limits.maxComputeWorkgroupsPerDimension,
      },
    };
    show('adapter', infoText || result.webgpu.adapter.classHint);

    let device;
    const buffers = [];
    try {
      device = await adapter.requestDevice();
      result.webgpu.device = { acquired: true, features: [...device.features].sort() };
      const elementCount = 1024;
      const inputData = Float32Array.from({ length: elementCount }, (_, i) => i / 8);
      const input = device.createBuffer({
        label: 'blackbox-smoke-input', size: inputData.byteLength,
        usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_DST,
      });
      const output = device.createBuffer({
        label: 'blackbox-smoke-output', size: inputData.byteLength,
        usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC,
      });
      const readback = device.createBuffer({
        label: 'blackbox-smoke-readback', size: inputData.byteLength,
        usage: GPUBufferUsage.MAP_READ | GPUBufferUsage.COPY_DST,
      });
      buffers.push(input, output, readback);
      device.queue.writeBuffer(input, 0, inputData);

      const shader = device.createShaderModule({
        label: 'blackbox-smoke-wgsl',
        code: `
          @group(0) @binding(0) var<storage, read> src: array<f32>;
          @group(0) @binding(1) var<storage, read_write> dst: array<f32>;
          @compute @workgroup_size(64)
          fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
            let i = gid.x;
            if (i < ${elementCount}u) { dst[i] = src[i] * 2.0 + 1.0; }
          }
        `,
      });
      let compilation = null;
      try {
        if (typeof shader.getCompilationInfo === 'function') {
          const info = await shader.getCompilationInfo();
          const errors = [...info.messages].filter((m) => m.type === 'error');
          if (errors.length) throw new Error(errors.map((m) => m.message).join('; '));
          compilation = { messages: info.messages.length, errors: 0 };
        }
      } catch (error) {
        if (String(error?.message || '').includes('WGSL')) throw error;
        compilation = { unavailable: String(error?.message || error) };
      }
      const pipeline = typeof device.createComputePipelineAsync === 'function'
        ? await device.createComputePipelineAsync({
            label: 'blackbox-smoke-pipeline', layout: 'auto', compute: { module: shader, entryPoint: 'main' },
          })
        : device.createComputePipeline({
            label: 'blackbox-smoke-pipeline', layout: 'auto', compute: { module: shader, entryPoint: 'main' },
          });
      const bindGroup = device.createBindGroup({
        layout: pipeline.getBindGroupLayout(0),
        entries: [{ binding: 0, resource: { buffer: input } }, { binding: 1, resource: { buffer: output } }],
      });
      const encoder = device.createCommandEncoder({ label: 'blackbox-smoke-encoder' });
      const pass = encoder.beginComputePass({ label: 'blackbox-smoke-pass' });
      pass.setPipeline(pipeline);
      pass.setBindGroup(0, bindGroup);
      pass.dispatchWorkgroups(Math.ceil(elementCount / 64));
      pass.end();
      encoder.copyBufferToBuffer(output, 0, readback, 0, inputData.byteLength);
      const t0 = performance.now();
      device.queue.submit([encoder.finish()]);
      await readback.mapAsync(GPUMapMode.READ);
      const actual = new Float32Array(readback.getMappedRange()).slice();
      readback.unmap();
      const submitReadbackMs = +(performance.now() - t0).toFixed(3);
      let mismatchCount = 0;
      let maxAbsError = 0;
      let checksum = 0;
      for (let i = 0; i < elementCount; i++) {
        const expected = inputData[i] * 2 + 1;
        const delta = Math.abs(actual[i] - expected);
        if (delta > 1e-4) mismatchCount++;
        maxAbsError = Math.max(maxAbsError, delta);
        checksum += actual[i];
      }
      result.webgpu.status = mismatchCount === 0 ? 'pass' : 'fail';
      result.webgpu.compute = {
        elementCount, operation: 'out[i] = in[i] * 2 + 1', workgroupSize: 64,
        dispatchCount: Math.ceil(elementCount / 64), mismatchCount,
        maxAbsError: +maxAbsError.toPrecision(5), checksum: +checksum.toFixed(3),
        submitReadbackMs, timingScope: 'queue submit + map/readback; not kernel-only',
        shaderCompilation: compilation,
      };
      result.webgpu.device.lost = false;
      show('correctness', `${mismatchCount === 0 ? 'PASS' : 'FAIL'} · ${elementCount} values · max error ${result.webgpu.compute.maxAbsError}`);
      show('timing', `${submitReadbackMs} ms · submit + readback`);
    } catch (error) {
      result.webgpu.status = 'error';
      result.webgpu.reason = String(error?.message || error);
      result.webgpu.compute = result.webgpu.compute || null;
      if (device) result.webgpu.device = { acquired: true, error: result.webgpu.reason };
      show('correctness', `ERROR · ${result.webgpu.reason}`);
      show('timing', 'not measured');
    } finally {
      for (const buffer of buffers) { try { buffer.destroy(); } catch {} }
      if (device) {
        device.lost.then((info) => {
          if (info.reason !== 'destroyed') {
            result.webgpu.deviceLost = { reason: info.reason, message: info.message };
            if (result.webgpu.status === 'pass') result.webgpu.status = 'error';
            result.finishedAt = new Date().toISOString();
            render(result);
          }
        }).catch(() => {});
        try { device.destroy(); } catch {}
      }
    }
    result.finishedAt = new Date().toISOString();
    render(result);
    return result;
  }

  function render(result) {
    window.__BB_GPU_SMOKE_RESULT__ = result;
    const status = result.webgpu?.status || 'unsupported';
    el('status').className = `status ${status}`;
    el('status-text').textContent = status;
    show('api', result.webgpu?.apiAvailable ? `present · ${status}` : 'not exposed');
    show('adapter', result.webgpu?.adapter?.info
      ? (Object.values(result.webgpu.adapter.info).filter((v) => typeof v === 'string' && v).join(' · ') || result.webgpu.adapter.classHint)
      : result.webgpu?.reason || 'not available');
    if (result.webgpu?.compute) {
      const c = result.webgpu.compute;
      show('correctness', `${c.mismatchCount === 0 ? 'PASS' : 'FAIL'} · ${c.elementCount} values · max error ${c.maxAbsError}`);
      show('timing', `${c.submitReadbackMs} ms · submit + readback`);
    } else {
      show('correctness', result.webgpu?.reason || status);
      show('timing', 'not measured');
    }
    show('webgl', result.webgl2?.available ? `${result.webgl2.vendor} / ${result.webgl2.renderer}` : 'not available');
    show('context', `${result.context?.origin || location.origin} · secure=${Boolean(result.context?.secureContext)}`);
    el('result').textContent = JSON.stringify(result, null, 2);
  }

  window.__BB_GPU_SMOKE__ = { run, get result() { return window.__BB_GPU_SMOKE_RESULT__ || null; } };
  el('rerun').addEventListener('click', async () => {
    el('rerun').disabled = true;
    try { await run(); } finally { el('rerun').disabled = false; }
  });
  window.__BB_GPU_SMOKE_PROMISE__ = run().catch((error) => {
    const result = { schemaVersion: 1, test: 'webgpu-vector-transform-smoke', webgpu: { status: 'error', reason: String(error?.message || error) }, context: { origin: location.origin, secureContext: isSecureContext } };
    render(result);
    return result;
  });
})();
