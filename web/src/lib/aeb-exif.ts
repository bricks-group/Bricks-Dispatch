/**
 * Camera AEB auto-naming for GO stills upload.
 *
 * Canon/Sony/Nikon AEB bursts arrive as plain frame counters
 * (`IMG_4031.jpg` … `IMG_4035.jpg`, `untitled shoot-4031.jpg`) with the
 * exposure offset only in EXIF ExposureBiasValue. Since `42e1900` counters
 * are their own angle (correct — they are not +4031 EV), so an untouched AEB
 * shoot fails the bracket gate and the vendor has to hand-rename every file.
 *
 * This reads EXIF from JPEG bytes in the browser and, for consecutive frames
 * that are provably one burst (same camera, ≤ AEB_MAX_GAP_S apart, no repeated
 * bias, run ≥ 3 covering under / mid / over), renames them
 * `<firstFrame>_<±EV>.jpg` — the naming the gate, Mark complete API and
 * post-prod bracket merge already understand. Anything not provable is left
 * exactly as the vendor named it (the gate still rejects it honestly).
 */

export type AebExif = {
  bias: number | null;
  /** Seconds since epoch-ish (local camera clock) incl. sub-seconds. */
  time: number | null;
  model: string | null;
};

export const AEB_MAX_GAP_S = 3;
const MID_ABS = 0.5;

function readAscii(v: DataView, off: number, len: number): string {
  let s = '';
  for (let i = 0; i < len && off + i < v.byteLength; i++) {
    const c = v.getUint8(off + i);
    if (c === 0) break;
    s += String.fromCharCode(c);
  }
  return s.trim();
}

/** Read Model / ExposureBiasValue / DateTimeOriginal(+SubSec) from a TIFF block at `t`.
 *  `exifOnly`: the block's first IFD is itself the Exif IFD (Canon CR3 CMT2). */
function readTiffExif(v: DataView, t: number, out: AebExif, exifOnly = false): void {
  if (t + 8 > v.byteLength) return;
  const bo = v.getUint16(t);
  if (bo !== 0x4949 && bo !== 0x4d4d) return;
  const le = bo === 0x4949;
  const u16 = (o: number) => v.getUint16(t + o, le);
  const u32 = (o: number) => v.getUint32(t + o, le);
  const i32 = (o: number) => v.getInt32(t + o, le);
  const tags = (ifd: number) => {
    const map = new Map<number, { type: number; count: number; valOff: number }>();
    if (ifd <= 0 || t + ifd + 2 > v.byteLength) return map;
    const n = u16(ifd);
    for (let i = 0; i < n && i < 512; i++) {
      const e = ifd + 2 + i * 12;
      if (t + e + 12 > v.byteLength) break;
      const type = u16(e + 2);
      const count = u32(e + 4);
      const size = ({ 1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8 } as Record<number, number>)[type] || 1;
      const valOff = size * count <= 4 ? e + 8 : u32(e + 8);
      map.set(u16(e), { type, count, valOff });
    }
    return map;
  };
  let exif: Map<number, { type: number; count: number; valOff: number }>;
  if (exifOnly) {
    exif = tags(u32(4));
  } else {
    const ifd0 = tags(u32(4));
    const model = ifd0.get(0x0110);
    if (model && t + model.valOff < v.byteLength) out.model = readAscii(v, t + model.valOff, model.count) || out.model;
    const exifPtr = ifd0.get(0x8769);
    if (!exifPtr) return;
    exif = tags(u32(exifPtr.valOff));
  }
  const bias = exif.get(0x9204);
  if (bias && (bias.type === 10 || bias.type === 5) && t + bias.valOff + 8 <= v.byteLength) {
    const num = bias.type === 10 ? i32(bias.valOff) : u32(bias.valOff);
    const den = bias.type === 10 ? i32(bias.valOff + 4) : u32(bias.valOff + 4);
    if (den) out.bias = Math.round((num / den) * 100) / 100;
  }
  const dto = exif.get(0x9003);
  if (dto && t + dto.valOff < v.byteLength) {
    const s = readAscii(v, t + dto.valOff, dto.count);
    const m = s.match(/^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})/);
    if (m) {
      let secs = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) / 1000;
      const sub = exif.get(0x9291);
      if (sub && t + sub.valOff < v.byteLength) {
        const ss = readAscii(v, t + sub.valOff, sub.count).replace(/\D/g, '');
        if (ss) secs += Number(`0.${ss}`);
      }
      out.time = secs;
    }
  }
}

/** Parse ExposureBiasValue / DateTimeOriginal(+SubSec) / Model from JPEG bytes. */
export function parseJpegExif(buf: ArrayBuffer): AebExif {
  const out: AebExif = { bias: null, time: null, model: null };
  try {
    const v = new DataView(buf);
    if (v.byteLength < 4 || v.getUint16(0) !== 0xffd8) return out;
    let p = 2;
    while (p + 4 <= v.byteLength) {
      if (v.getUint8(p) !== 0xff) return out;
      const marker = v.getUint8(p + 1);
      if (marker === 0xda || marker === 0xd9) return out;
      const segLen = v.getUint16(p + 2);
      if (marker === 0xe1 && readAscii(v, p + 4, 4) === 'Exif') {
        readTiffExif(v, p + 10, out);
        return out;
      }
      p += 2 + segLen;
    }
  } catch {
    /* unreadable EXIF → leave untouched */
  }
  return out;
}

function findAscii(v: DataView, needle: string, from = 0, limit = v.byteLength): number {
  const n = needle.length;
  const end = Math.min(limit, v.byteLength) - n;
  outer: for (let i = from; i <= end; i++) {
    for (let k = 0; k < n; k++) if (v.getUint8(i + k) !== needle.charCodeAt(k)) continue outer;
    return i;
  }
  return -1;
}

/**
 * Same fields from a camera RAW head: TIFF-based (CR2/NEF/ARW/PEF/SRW/DNG,
 * ORF "IIRO", RW2 "IIU"), Canon CR3 (CMT1/CMT2 boxes) and Fuji RAF
 * (embedded JPEG). Parity with backend raw_decode.read_raw_exif.
 */
export function parseRawExif(buf: ArrayBuffer): AebExif {
  const out: AebExif = { bias: null, time: null, model: null };
  try {
    const v = new DataView(buf);
    if (v.byteLength < 16) return out;
    if (readAscii(v, 4, 4) === 'ftyp') {
      const c1 = findAscii(v, 'CMT1');
      if (c1 > 0) readTiffExif(v, c1 + 4, out);
      const c2 = findAscii(v, 'CMT2');
      if (c2 > 0) readTiffExif(v, c2 + 4, out, true);
      return out;
    }
    if (readAscii(v, 0, 15) === 'FUJIFILMCCD-RAW') {
      const off = v.getUint32(84);
      const len = v.getUint32(88);
      if (off > 0 && off < v.byteLength) return parseJpegExif(buf.slice(off, Math.min(v.byteLength, off + len)));
      return out;
    }
    readTiffExif(v, 0, out);
  } catch {
    /* unreadable → untouched */
  }
  return out;
}

export function formatEv(bias: number): string {
  const r = Math.round(bias * 10) / 10;
  if (Math.abs(r) < 0.05) return '0';
  const s = String(Math.abs(r));
  return `${r < 0 ? '-' : '+'}${s}`;
}

function stemOf(name: string): string {
  return name.includes('.') ? name.slice(0, name.lastIndexOf('.')) : name;
}

function extOf(name: string): string {
  return name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
}

function coversUnderMidOver(biases: number[]): boolean {
  return (
    biases.some((b) => b < -0.05) &&
    biases.some((b) => Math.abs(b) <= MID_ABS) &&
    biases.some((b) => b > 0.05)
  );
}

export type AebFrame = { name: string; exif: AebExif };

/**
 * Pure planner: given frames (any order), return name → new name for frames
 * that form a provable AEB burst. `needsName(name)` should return true only for
 * frames whose filename carries no ±EV tag already.
 */
export function planAebRenames(
  frames: AebFrame[],
  needsName: (name: string) => boolean,
): Map<string, string> {
  const renames = new Map<string, string>();
  const sorted = [...frames].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true }),
  );
  let run: AebFrame[] = [];
  const flush = () => {
    const biases = run.map((f) => f.exif.bias as number);
    if (run.length >= 3 && coversUnderMidOver(biases)) {
      const stem = stemOf(run[0].name);
      for (const f of run) {
        renames.set(f.name, `${stem}_${formatEv(f.exif.bias as number)}${extOf(f.name)}`);
      }
    }
    run = [];
  };
  for (const f of sorted) {
    const ok = needsName(f.name) && f.exif.bias != null && f.exif.time != null;
    if (!ok) {
      flush();
      continue;
    }
    const prev = run[run.length - 1];
    const joins =
      prev &&
      (prev.exif.model || '') === (f.exif.model || '') &&
      Math.abs((f.exif.time as number) - (prev.exif.time as number)) <= AEB_MAX_GAP_S &&
      !run.some((r) => Math.abs((r.exif.bias as number) - (f.exif.bias as number)) < 0.05);
    if (!joins) flush();
    run.push(f);
  }
  flush();
  return renames;
}

const JPEG_RE = /\.jpe?g$/i;
const RAW_RE =
  /\.(cr2|cr3|crw|dng|nef|nrw|arw|srf|sr2|raf|orf|rw2|pef|ptx|srw|x3f|raw|rwl|3fr|fff|iiq|mos|erf|kdc|mrw)$/i;

/** Browser wrapper: read the EXIF head of each JPEG or camera RAW and return renamed File objects. */
export async function autoNameAebFiles(
  files: File[],
  needsName: (name: string) => boolean,
): Promise<{ files: File[]; renamed: number }> {
  const frames: AebFrame[] = [];
  for (const f of files) {
    if (!needsName(f.name)) continue;
    if (JPEG_RE.test(f.name)) {
      const head = await f.slice(0, 256 * 1024).arrayBuffer();
      frames.push({ name: f.name, exif: parseJpegExif(head) });
    } else if (RAW_RE.test(f.name)) {
      // RAF puts its EXIF in an embedded JPEG past the header; 1 MB covers all.
      const head = await f.slice(0, 1024 * 1024).arrayBuffer();
      frames.push({ name: f.name, exif: parseRawExif(head) });
    }
  }
  const plan = planAebRenames(frames, needsName);
  if (!plan.size) return { files, renamed: 0 };
  const out = files.map((f) => {
    const next = plan.get(f.name);
    return next ? new File([f], next, { type: f.type, lastModified: f.lastModified }) : f;
  });
  return { files: out, renamed: plan.size };
}
