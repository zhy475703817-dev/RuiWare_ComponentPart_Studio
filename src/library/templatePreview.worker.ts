import { Vector3 } from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

type ParseRequest = { id: number; data: ArrayBuffer };

self.onmessage = (event: MessageEvent<ParseRequest>) => {
  const { id, data } = event.data;
  let geometry: ReturnType<STLLoader["parse"]> | undefined;
  try {
    if (!data.byteLength || data.byteLength > 64 * 1024 * 1024) throw new Error();
    const binary = data.byteLength >= 84 && 84 + new DataView(data).getUint32(80, true) * 50 === data.byteLength;
    let input: ArrayBuffer | string = data;
    if (!binary) {
      input = new TextDecoder().decode(data).trimStart();
      if (!input.startsWith("solid")) throw new Error();
    }
    geometry = new STLLoader().parse(input);
    const positions = geometry.getAttribute("position");
    if (!positions || positions.count < 3 || positions.count % 3) throw new Error();
    for (let index = 0; index < positions.array.length; index += 1) if (!Number.isFinite(positions.array[index])) throw new Error();
    const a = new Vector3(), b = new Vector3(), c = new Vector3();
    let hasTriangle = false;
    for (let index = 0; index < positions.count; index += 3) {
      a.fromBufferAttribute(positions, index);
      b.fromBufferAttribute(positions, index + 1);
      c.fromBufferAttribute(positions, index + 2);
      if (b.sub(a).cross(c.sub(a)).lengthSq() > 0) { hasTriangle = true; break; }
    }
    if (!hasTriangle) throw new Error();
    geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    const position = geometry.getAttribute("position").array as Float32Array;
    const normal = geometry.getAttribute("normal").array as Float32Array;
    const box = geometry.boundingBox!;
    const sphere = geometry.boundingSphere!;
    const bounds = { min: box.min.toArray(), max: box.max.toArray(), center: sphere.center.toArray(), radius: sphere.radius };
    (self as unknown as { postMessage: (message: unknown, transfer: Transferable[]) => void }).postMessage({ id, positions: position.buffer, normals: normal.buffer, bounds }, [position.buffer, normal.buffer]);
  } catch {
    (self as unknown as { postMessage: (message: unknown) => void }).postMessage({ id, error: "模型文件无法解析，或没有有效的三角形，请检查发布版本。" });
  } finally {
    geometry?.dispose();
  }
};
