"""Anima Model Viewer v1.1 — Tkinter + moderngl + image viewer + sky panoramas."""
import sys, os, json, math, base64, traceback, zlib, struct, io, weakref, tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np, glm

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    HAS_DND = False

os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
import pygame
pygame.init()
try:
    pygame.display.set_mode((1, 1),
        pygame.OPENGL | pygame.DOUBLEBUF | pygame.HIDDEN)
except Exception:
    try:
        pygame.display.set_mode((1, 1), pygame.OPENGL | pygame.DOUBLEBUF)
    except Exception:
        pygame.display.set_mode((1, 1))

import moderngl
ctx_gl = moderngl.create_context()

MODEL_EXTS = {".obj", ".glb", ".gltf", ".mdl", ".fbx", ".dae",
              ".3ds", ".x", ".stl", ".ply", ".off", ".3mf"}
TEX_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".gif"}
SKY_EXTS = {".exr"}          # HDR sky panoramas (LDR sky images use TEX_EXTS)

# ============================================================
# Source .mdl support — reuse the engine's pure-Python loader
# ============================================================
_MDL_LOADER = None
_MDL_RESOLVE_TEX = None
try:
    _ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _ENGINE_ROOT not in sys.path:
        sys.path.insert(0, _ENGINE_ROOT)
    from engine_pkg.resources.mdl_loader import load_mdl as _MDL_LOADER
    from engine_pkg.resources import resolve_texture_path as _MDL_RESOLVE_TEX
except Exception as _mdl_err:
    _MDL_LOADER = None
    _MDL_RESOLVE_TEX = None
    print(f"[hlmv] .mdl support unavailable: {_mdl_err}")


def _png(w, h, rgb):
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    arr = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3)
    with_filter = np.zeros((h, 1 + w * 3), dtype=np.uint8)
    with_filter[:, 1:] = arr.reshape(h, -1)
    idat = zlib.compress(with_filter.tobytes(), 1)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", idat)
            + chunk(b"IEND", b""))


# ============================================================
# Sky panoramas — equirectangular sky behind the model
# ============================================================
_SKY_MAX_HDR_W = 4096      # oversized HDR panoramas are box-filtered down
_SKY_MAX_LDR_W = 8192


class ExrError(ValueError):
    """Raised when an OpenEXR image cannot be decoded."""


_EXR_MAGIC = b"\x76\x2f\x31\x01"
# compression id -> (scanlines per block, name)
_EXR_COMPRESSIONS = {
    0: (1, "NONE"), 1: (1, "RLE"), 2: (1, "ZIPS"), 3: (16, "ZIP"),
    4: (32, "PIZ"), 5: (16, "PXR24"), 6: (32, "B44"), 7: (32, "B44A"),
    8: (32, "DWAA"), 9: (256, "DWAB"),
}
_EXR_PIXEL_BYTES = {0: 4, 1: 2, 2: 4}      # UINT, HALF, FLOAT


def _exr_cstr(buf, pos):
    end = buf.find(b"\x00", pos)
    if end < 0:
        raise ExrError("truncated EXR string")
    return buf[pos:end].decode("utf-8", "replace"), end + 1


def _exr_unpredict(data):
    """Undo the ZIP/RLE pre-filter: inverse delta predictor, then de-interleave."""
    arr = np.frombuffer(data, dtype=np.uint8)
    n = len(arr)
    if n == 0:
        return arr
    d = np.empty(n, dtype=np.int32)
    d[0] = arr[0]
    if n > 1:
        d[1:] = arr[1:].astype(np.int32) - 128
    res = (np.cumsum(d) & 0xFF).astype(np.uint8)
    half = (n + 1) // 2
    t1, t2 = res[:half], res[half:]
    out = np.empty(n, dtype=np.uint8)
    m = len(t2)
    if m:
        out[0:2 * m:2] = t1[:m]
        out[1:2 * m:2] = t2
    if n % 2:
        out[n - 1] = t1[half - 1]
    return out


def _exr_rle(data, out_size):
    """OpenEXR RLE: byte >= 0 repeats the next byte (n+1)x, byte < 0 copies -n literals."""
    src = data
    dst = bytearray()
    i = 0
    n = len(src)
    while i < n and len(dst) < out_size:
        cnt = src[i]
        i += 1
        if cnt < 128:
            if i < n:
                dst += src[i:i + 1] * (cnt + 1)
                i += 1
        else:
            lit = 256 - cnt
            dst += src[i:i + lit]
            i += lit
    return bytes(dst[:out_size])


def _exr_pick_rgb(planes):
    """Pick RGB (or luminance, or the first three planes) out of decoded channels."""
    def get(name):
        a = planes.get(name)
        if a is None:
            return None
        return np.asarray(a, dtype=np.float32)

    r, g, b = get("R"), get("G"), get("B")
    if r is not None and g is not None and b is not None:
        return np.stack((r, g, b), axis=-1)
    y = get("Y")
    if y is not None:
        return np.repeat(y[:, :, None], 3, axis=2)
    names = list(planes.keys())
    if not names:
        raise ExrError("EXR image has no channels")
    arrs = [np.asarray(planes[k], dtype=np.float32) for k in names[:3]]
    if len(arrs) >= 3:
        return np.stack(arrs[:3], axis=-1)
    return np.repeat(arrs[0][:, :, None], 3, axis=2)


def decode_exr(raw):
    """Decode a scanline OpenEXR image (NONE/RLE/ZIPS/ZIP) to float32 (h, w, 3)."""
    if raw[:4] != _EXR_MAGIC:
        raise ExrError("not an OpenEXR file")
    if len(raw) < 8:
        raise ExrError("truncated EXR header")
    flags = struct.unpack_from("<i", raw, 4)[0] >> 8
    if flags & 0x8:
        raise ExrError("deep EXR images are not supported")
    if flags & 0x10:
        raise ExrError("multipart EXR images are not supported")
    if flags & 0x2:
        raise ExrError("tiled EXR images are not supported")
    pos = 8
    attrs = {}
    while pos < len(raw):
        name, pos = _exr_cstr(raw, pos)
        if not name:
            break
        _typ, pos = _exr_cstr(raw, pos)
        size = struct.unpack_from("<i", raw, pos)[0]
        pos += 4
        if size < 0 or pos + size > len(raw):
            raise ExrError("truncated EXR attribute")
        attrs[name] = raw[pos:pos + size]
        pos += size

    if "channels" not in attrs or "dataWindow" not in attrs:
        raise ExrError("EXR header is missing required attributes")

    channels = []
    p = 0
    chdata = attrs["channels"]
    while p < len(chdata):
        nm, p = _exr_cstr(chdata, p)
        if not nm:
            break
        ptype = struct.unpack_from("<i", chdata, p)[0]
        p += 8                                   # pixel type + pLinear/reserved
        xs = struct.unpack_from("<i", chdata, p)[0]
        p += 4
        ys = struct.unpack_from("<i", chdata, p)[0]
        p += 4
        if ptype not in _EXR_PIXEL_BYTES:
            raise ExrError("unknown EXR pixel type %d" % ptype)
        if xs != 1 or ys != 1:
            raise ExrError("subsampled EXR channels are not supported")
        channels.append((nm, ptype))
    if not channels:
        raise ExrError("EXR image has no channels")

    comp_id = attrs["compression"][0] if "compression" in attrs else 0
    if comp_id not in _EXR_COMPRESSIONS:
        raise ExrError("unknown EXR compression %d" % comp_id)
    lines_per, comp_name = _EXR_COMPRESSIONS[comp_id]
    if comp_name not in ("NONE", "RLE", "ZIPS", "ZIP"):
        raise ExrError("EXR compression %s is not supported "
                       "(supported: NONE, RLE, ZIPS, ZIP)" % comp_name)

    xmin, ymin, xmax, ymax = struct.unpack_from("<4i", attrs["dataWindow"], 0)
    width, height = xmax - xmin + 1, ymax - ymin + 1
    if width <= 0 or height <= 0 or width * height > 200_000_000:
        raise ExrError("invalid EXR data window")
    line_order = attrs["lineOrder"][0] if "lineOrder" in attrs else 0

    nchunks = (height + lines_per - 1) // lines_per
    if pos + 8 * nchunks > len(raw):
        raise ExrError("truncated EXR offset table")
    offsets = struct.unpack_from("<%dQ" % nchunks, raw, pos)

    bps = [_EXR_PIXEL_BYTES[pt] for _n, pt in channels]
    row_stride = width * sum(bps)
    planes = {nm: np.zeros((height, width), dtype=np.float32) for nm, _pt in channels}

    for off in offsets:
        if off + 8 > len(raw):
            continue
        y0, dsize = struct.unpack_from("<iI", raw, off)
        if off + 8 + dsize > len(raw):
            raise ExrError("truncated EXR chunk")
        cdata = raw[off + 8:off + 8 + dsize]
        # The chunk y is the lowest scanline of the block; DECREASING_Y only
        # changes the order the lines are stored in (and of the chunks themselves).
        nlines = min(lines_per, ymax - y0 + 1)
        row_start = y0 - ymin
        if nlines <= 0 or row_start < 0:
            continue
        expected = row_stride * nlines

        if comp_id == 0 or dsize >= expected:
            # EXR stores a block raw when compressing it does not pay off
            buf = np.frombuffer(cdata, dtype=np.uint8, count=min(expected, len(cdata)))
        elif comp_id in (2, 3):
            try:
                buf = _exr_unpredict(zlib.decompress(cdata))
            except Exception as exc:
                raise ExrError("corrupt EXR ZIP block: %s" % exc)
        else:
            buf = _exr_unpredict(_exr_rle(cdata, expected))
        if len(buf) < expected:
            raise ExrError("short EXR scanline block")
        block = buf[:expected].reshape(nlines, row_stride)

        coff = 0
        for ci, (nm, ptype) in enumerate(channels):
            nb = width * bps[ci]
            sub = np.ascontiguousarray(block[:, coff:coff + nb])
            coff += nb
            if ptype == 1:
                vals = sub.view(np.float16).reshape(nlines, width).astype(np.float32)
            elif ptype == 2:
                vals = sub.view(np.float32).reshape(nlines, width)
            else:
                vals = sub.view(np.uint32).reshape(nlines, width).astype(np.float32)
            planes[nm][row_start:row_start + nlines, :] = vals

    out = _exr_pick_rgb(planes)
    return np.nan_to_num(out, nan=0.0, posinf=65504.0, neginf=-65504.0)


def _exr_via_openexr(path):
    """Optional accelerator: use the official OpenEXR module when it is installed."""
    import OpenEXR
    import Imath
    fh = OpenEXR.InputFile(str(path))
    hdr = fh.header()
    dw = hdr["dataWindow"]
    w = int(dw.max.x - dw.min.x + 1)
    h = int(dw.max.y - dw.min.y + 1)
    if w <= 0 or h <= 0:
        raise ExrError("invalid EXR data window")
    pt = Imath.PixelType(Imath.PixelType.FLOAT)
    names = list(hdr["channels"].keys())
    planes = {}
    for nm in names:
        planes[nm] = np.frombuffer(fh.channel(nm, pt), dtype=np.float32).reshape(h, w)
    return _exr_pick_rgb(planes)


def _exr_via_imageio(path):
    """Optional accelerator: imageio's FreeImage plugin (only if it is usable)."""
    import imageio.v3 as iio
    arr = np.asarray(iio.imread(path, plugin="EXR-FI"), dtype=np.float32)
    if arr.ndim == 2:
        return np.repeat(arr[:, :, None], 3, axis=2)
    return arr[:, :, :3]


def load_exr(path):
    """Load an EXR panorama as float32 (h, w, 3), row 0 = top (zenith)."""
    err = None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return decode_exr(raw)
    except Exception as exc:
        err = exc
    for fn in (_exr_via_openexr, _exr_via_imageio):
        try:
            arr = fn(path)
        except Exception:
            continue
        if arr is not None and getattr(arr, "size", 0):
            return np.asarray(arr, dtype=np.float32)
    msg = str(err) or "unsupported EXR image"
    if "not supported" in msg:
        msg += " — install 'OpenEXR' (pip install OpenEXR) to read this file"
    raise ExrError(msg)


def _sky_downsample(rgb, max_w):
    """Box-filter a wide panorama down to max_w pixels (keeps the aspect ratio)."""
    h, w = rgb.shape[0], rgb.shape[1]
    if w <= max_w or w <= 0 or h <= 0:
        return rgb
    f = int(math.ceil(w / float(max_w)))
    hh, ww = (h // f) * f, (w // f) * f
    if hh < f or ww < f:
        return rgb
    a = rgb[:hh, :ww].reshape(hh // f, f, ww // f, f, 3)
    return a.mean(axis=(1, 3), dtype=np.float32)


def _sky_exposure(rgb):
    """Auto-exposure for HDR skies: put the median luminance near mid grey."""
    try:
        a = np.asarray(rgb, dtype=np.float32)
        if a.ndim == 3 and a.shape[0] > 8:
            # measure the sky, not the terrain in the panorama's lower half
            a = a[: max(1, int(round(a.shape[0] * 0.55)))]
        step = max(1, int(math.sqrt(max(1, a.shape[0] * a.shape[1]) / 65536.0)))
        lum = a[::step, ::step] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        finite = lum[np.isfinite(lum)]
        if finite.size == 0:
            return 1.0
        med = float(np.median(finite))
        if not math.isfinite(med) or med < 1e-5:
            return 1.0
        # Display-referred target: the shader applies Reinhard + sRGB, so
        # 0.48 / med made the sky ~64% grey (blown out).  Invert Reinhard at
        # the desired 0.5 display value instead.
        want = 0.5 ** 2.2
        return float(max(1e-4, min(1e4, (want / (1.0 - want)) / med)))
    except Exception:
        return 1.0


def load_sky_image(path):
    """Load a sky panorama -> (float32 RGB, is_hdr).

    EXR files are decoded to linear float (``is_hdr`` True); everything else is
    read through pygame as 8-bit sRGB. Row 0 is the top of the image, mapping to
    the zenith of an equirectangular panorama.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in SKY_EXTS:
        return load_exr(path), True
    surf = pygame.image.load(path)
    try:
        surf = surf.convert()
    except Exception:
        pass
    w, h = surf.get_size()
    if w <= 0 or h <= 0:
        raise ValueError("image has no pixels")
    raw = pygame.image.tobytes(surf, "RGB", False)
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)
    return arr.astype(np.float32) / 255.0, False


VS = """#version 330
in vec3 in_pos; in vec3 in_normal; in vec2 in_uv;
uniform mat4 proj, view, model;
out vec3 v_n; out vec3 v_w; out vec2 v_uv;
void main(){
    vec4 w = model * vec4(in_pos,1.0);
    v_w = w.xyz; v_n = normalize(mat3(model)*in_normal); v_uv = in_uv;
    gl_Position = proj * view * w;
}"""
SKIN_VS = """#version 330
in vec3 in_pos; in vec3 in_normal; in vec2 in_uv;
in vec4 in_joints; in vec4 in_weights;
uniform mat4 proj, view, model;
uniform int bone_count;
uniform mat4 bone_matrices[64];
out vec3 v_n; out vec3 v_w; out vec2 v_uv;
void main(){
    mat4 skin = mat4(1.0);
    if (bone_count > 0){
        skin = bone_matrices[int(in_joints.x)]*in_weights.x
             + bone_matrices[int(in_joints.y)]*in_weights.y
             + bone_matrices[int(in_joints.z)]*in_weights.z
             + bone_matrices[int(in_joints.w)]*in_weights.w;
    }
    vec4 l = skin * vec4(in_pos,1.0);
    vec4 w = model * l;
    v_w = w.xyz; v_n = normalize(mat3(model)*mat3(skin)*in_normal); v_uv = in_uv;
    gl_Position = proj * view * w;
}"""
FS = """#version 330
in vec3 v_n; in vec3 v_w; in vec2 v_uv;
uniform sampler2D tex;
uniform vec3 cam_pos, light_dir, light_color, ambient_color;
uniform float light_strength, spec_shininess, spec_strength;
uniform vec3 spec_tint;
uniform int alpha_test;
out vec4 f_color;
void main(){
    vec4 b = texture(tex, v_uv);
    if (alpha_test == 1 && b.a < 0.35) discard;
    vec3 n = normalize(v_n), l = normalize(light_dir);
    float d = max(dot(n,l),0.0);
    vec3 c = b.rgb * (ambient_color + light_color*d*light_strength);
    if (spec_strength > 0.0){
        vec3 v = normalize(cam_pos - v_w);
        vec3 h = normalize(l+v);
        c += spec_tint * pow(max(dot(n,h),0.0), spec_shininess) * spec_strength;
    }
    f_color = vec4(c, b.a);
}"""
CUBE_VS = """#version 330
in vec3 in_pos;
uniform mat4 proj, view, model;
void main(){ gl_Position = proj*view*model*vec4(in_pos,1.0); }"""
CUBE_FS = """#version 330
uniform vec4 color; out vec4 f_color;
void main(){ f_color = color; }"""

CUBE_DATA = np.array([
    -1,-1,-1, 1,-1,-1, 1,-1,1, -1,-1,-1, 1,-1,1, -1,-1,1,
    -1,1,1, 1,1,1, 1,1,-1, -1,1,1, 1,1,-1, -1,1,-1,
    -1,-1,-1, -1,-1,1, -1,1,1, -1,-1,-1, -1,1,1, -1,1,-1,
    1,-1,1, 1,-1,-1, 1,1,-1, 1,-1,1, 1,1,-1, 1,1,1,
    -1,-1,1, 1,-1,1, 1,1,1, -1,-1,1, 1,1,1, -1,1,1,
    1,-1,-1, -1,-1,-1, -1,1,-1, 1,-1,-1, -1,1,-1, 1,1,-1,
], dtype=np.float32)

# --- sky background: equirectangular panorama drawn behind everything ---
SKY_VS = """#version 330
in vec2 in_pos;
out vec2 v_uv;
void main(){ v_uv = in_pos * 0.5 + 0.5; gl_Position = vec4(in_pos, 1.0, 1.0); }"""
SKY_FS = """#version 330
in vec2 v_uv;
out vec4 f_color;
uniform sampler2D sky_tex;
uniform vec3 sky_right, sky_up, sky_back;
uniform float sky_tan_half_fov, sky_aspect, sky_exposure;
uniform int sky_tonemap;
const float PI = 3.141592653589793;
void main(){
    vec2 ndc = v_uv * 2.0 - 1.0;
    vec3 d = normalize(ndc.x * sky_aspect * sky_tan_half_fov * sky_right
                     + ndc.y * sky_tan_half_fov * sky_up
                     - sky_back);
    // +1.0 keeps the equirect wrap behind the default view, never across the
    // screen centre (where it drew a bright vertical line).
    float u = atan(d.x, -d.z) / (2.0 * PI) + 1.0;
    float v = acos(clamp(d.y, -1.0, 1.0)) / PI;
    vec2 uv = vec2(u, v);
    vec2 ddx = dFdx(uv);
    vec2 ddy = dFdy(uv);
    ddx.x -= floor(ddx.x + 0.5);
    ddy.x -= floor(ddy.x + 0.5);
    vec3 c = textureGrad(sky_tex, uv, ddx, ddy).rgb;
    if (sky_tonemap == 1){
        c = max(c, vec3(0.0)) * sky_exposure;
        c = c / (vec3(1.0) + c);
        c = pow(c, vec3(1.0 / 2.2));
    }
    f_color = vec4(c, 1.0);
}"""
SKY_QUAD = np.array([
    -1, -1, 1, -1, 1, 1,
    -1, -1, 1, 1, -1, 1,
], dtype=np.float32)


def _release_sub(s):
    for k in ("skinned_vao", "vao"):
        obj = s.get(k)
        if obj is not None:
            try: obj.release()
            except Exception: pass
    for k in ("vbo", "ibo", "jv", "wv"):
        obj = s.get(k)
        if obj is not None:
            try: obj.release()
            except Exception: pass
    for k in ("skinned_vao", "vao", "vbo", "ibo", "jv", "wv"):
        s[k] = None


def load_obj(fp):
    V, N, T = [], [], []
    faces = {}; cur = "_"; mtl = None
    bd = os.path.dirname(os.path.abspath(fp))
    for line in open(fp, "r", encoding="utf-8", errors="ignore"):
        s = line.strip()
        if not s or s.startswith("#"): continue
        p = s.split()
        try:
            if p[0] == "v": V.append((float(p[1]), float(p[2]), float(p[3])))
            elif p[0] == "vt": T.append((float(p[1]), 1.0 - float(p[2])))
            elif p[0] == "vn": N.append((float(p[1]), float(p[2]), float(p[3])))
            elif p[0] == "usemtl": cur = p[1] if len(p) > 1 else "_"
            elif p[0] == "mtllib": mtl = p[1]
            elif p[0] == "f":
                f = []
                for tok in p[1:]:
                    g = tok.split("/")
                    vi = int(g[0]) - 1 if int(g[0]) > 0 else len(V) + int(g[0])
                    ti = int(g[1]) - 1 if len(g) >= 2 and g[1] and int(g[1]) > 0 else -1
                    ni = int(g[2]) - 1 if len(g) >= 3 and g[2] and int(g[2]) > 0 else -1
                    f.append((vi, ti, ni))
                if len(f) >= 3: faces.setdefault(cur, []).append(f)
        except Exception: pass
    if not V or not faces: return []
    mtex = {}
    if mtl:
        for c in (os.path.join(bd, mtl), os.path.join(bd, os.path.basename(mtl))):
            if os.path.exists(c):
                mcur = None
                for line in open(c, "r", encoding="utf-8", errors="ignore"):
                    q = line.split()
                    if not q: continue
                    if q[0].lower() == "newmtl" and len(q) >= 2: mcur = q[1]
                    elif q[0].lower() == "map_kd" and mcur and len(q) >= 2:
                        mtex[mcur] = q[-1].replace("\\", "/")
                break
    subs = []
    for mn, fs in faces.items():
        out = []
        for f in fs:
            for k in range(1, len(f) - 1):
                tri = (f[0], f[k], f[k + 1])
                p0, p1, p2 = V[tri[0][0]], V[tri[1][0]], V[tri[2][0]]
                ux, uy, uz = p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2]
                vx, vy, vz = p2[0]-p0[0], p2[1]-p0[1], p2[2]-p0[2]
                nx, ny, nz = uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx
                ln = (nx*nx+ny*ny+nz*nz) ** 0.5
                fn = (nx/ln, ny/ln, nz/ln) if ln > 1e-9 else (0, 1, 0)
                for vi, ti, ni in tri:
                    p = V[vi]
                    n = N[ni] if 0 <= ni < len(N) else fn
                    uv = T[ti] if 0 <= ti < len(T) else (0, 0)
                    out.extend([p[0], p[1], p[2], n[0], n[1], n[2], uv[0], uv[1]])
        if out:
            tex = None; tn = mtex.get(mn)
            if tn:
                for c in (os.path.join(bd, tn), os.path.join(bd, os.path.basename(tn))):
                    if os.path.exists(c): tex = c; break
            subs.append({"verts": np.array(out, dtype=np.float32), "texture_path": tex,
                         "joints": None, "weights": None, "transparent": False, "name": mn})
    return subs


_COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16,
         5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_TYPE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def _b64(s):
    tbl = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    lu = {c: i for i, c in enumerate(tbl)}
    s = s.rstrip("="); out = bytearray(); val = 0; bits = 0
    for c in s:
        if c not in lu: continue
        val = (val << 6) | lu[c]; bits += 6
        while bits >= 8:
            bits -= 8; out.append((val >> bits) & 0xFF)
    return bytes(out)


def _acc(g, b, i):
    a = g["accessors"][i]
    if "bufferView" not in a: return None
    bv = g["bufferViews"][a["bufferView"]]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    cnt = a["count"]; dt = _COMP[a["componentType"]]; sh = _TYPE[a["type"]]
    st = bv.get("byteStride", 0); eb = np.dtype(dt).itemsize * sh
    if st and st != eb:
        r = np.zeros((cnt, sh), dtype=dt)
        for k in range(cnt):
            r[k] = np.frombuffer(b, dtype=dt, count=sh, offset=off + k * st)
        res = r
    else:
        res = np.frombuffer(b, dtype=dt, count=cnt * sh, offset=off).reshape(cnt, sh)
    if a.get("normalized") and dt in (np.uint8, np.uint16, np.int8, np.int16):
        res = res.astype(np.float32) / float(np.iinfo(dt).max)
    return res


def _glb(d):
    if d[:4] != b"glTF": raise ValueError("not GLB")
    ln = int.from_bytes(d[8:12], "little"); pos = 12; gj = None; bd = b""
    while pos + 8 <= ln:
        cl = int.from_bytes(d[pos:pos+4], "little"); ct = d[pos+4:pos+8]; pos += 8
        ch = d[pos:pos+cl]; pos += cl
        if ct == b"JSON": gj = json.loads(ch.decode())
        elif ct[:3] == b"BIN": bd = ch
    return gj, bd


def _trs(T, R, S):
    x, y, z, w = float(R[0]), float(R[1]), float(R[2]), float(R[3])
    n = math.sqrt(x*x+y*y+z*z+w*w)
    if n > 1e-9: x, y, z, w = x/n, y/n, z/n, w/n
    else: x, y, z, w = 0, 0, 0, 1
    xx, yy, zz = x*x, y*y, z*z; xy, xz, yz = x*y, x*z, y*z; wx, wy, wz = w*x, w*y, w*z
    m = np.array([[1-2*(yy+zz), 2*(xy-wz), 2*(xz+wy), 0],
                  [2*(xy+wz), 1-2*(xx+zz), 2*(yz-wx), 0],
                  [2*(xz-wy), 2*(yz+wx), 1-2*(xx+yy), 0],
                  [0, 0, 0, 1]], dtype=np.float64)
    m[0, 0] *= S[0]; m[1, 1] *= S[1]; m[2, 2] *= S[2]
    m[0, 3] = T[0]; m[1, 3] = T[1]; m[2, 3] = T[2]
    return m


def _nmat(nd):
    if "matrix" in nd: return np.array(nd["matrix"], dtype=np.float64).reshape(4, 4, order="F")
    return _trs(nd.get("translation", [0, 0, 0]), nd.get("rotation", [0, 0, 0, 1]),
                nd.get("scale", [1, 1, 1]))


def _tex(g, b, bd, i):
    if i >= len(g.get("textures", [])): return None
    t = g["textures"][i]
    if "source" not in t: return None
    ii = t["source"]
    if ii >= len(g.get("images", [])): return None
    img = g["images"][ii]
    if "uri" in img:
        u = img["uri"]
        if u.startswith("data:"):
            head, _, b64 = u.partition(",")
            d = _b64(b64)
            nm = "tex.png"
            if "jpeg" in head or "jpg" in head: nm = "tex.jpg"
            elif "webp" in head: nm = "tex.webp"
            return (d, nm)
        p = os.path.join(bd, u)
        return p if os.path.exists(p) else None
    if "bufferView" in img:
        bv = g["bufferViews"][img["bufferView"]]
        off = bv.get("byteOffset", 0)
        d = b[off:off + bv["byteLength"]]
        nm = "tex.png"
        if d[:2] == b"\xff\xd8": nm = "tex.jpg"
        elif d[:4] == b"RIFF" and d[8:12] == b"WEBP": nm = "tex.webp"
        return (d, nm)
    return None


def load_gltf(fp):
    d = open(fp, "rb").read()
    bd = os.path.dirname(os.path.abspath(fp))
    if d[:4] == b"glTF":
        g, b = _glb(d)
    else:
        g = json.loads(d.decode()); b = b""
        for buf in g.get("buffers", []):
            u = buf.get("uri", "")
            if u.startswith("data:"):
                _, _, b64 = u.partition(","); b = _b64(b64); break
            elif u:
                p = os.path.join(bd, u)
                if os.path.exists(p): b = open(p, "rb").read()
                break
    skel = None; skin = g["skins"][0] if g.get("skins") else None; b2i = {}
    if skin:
        bones = []
        for i, ni in enumerate(skin["joints"]):
            b2i[ni] = i
            bones.append({"name": g["nodes"][ni].get("name", f"b{i}"), "index": i,
                          "parent": -1, "children": [],
                          "local_bind": np.eye(4, dtype=np.float64),
                          "inverse_bind": np.eye(4, dtype=np.float64)})
        for pi, pn in enumerate(g["nodes"]):
            for ci in pn.get("children", []):
                if ci in b2i and pi in b2i:
                    bones[b2i[ci]]["parent"] = b2i[pi]
        for ni, bi in b2i.items():
            bones[bi]["local_bind"] = _nmat(g["nodes"][ni])
        if "inverseBindMatrices" in skin:
            ibm = _acc(g, b, skin["inverseBindMatrices"])
            if ibm is not None:
                for i in range(min(len(ibm), len(bones))):
                    bones[i]["inverse_bind"] = ibm[i].reshape(4, 4, order="F").astype(np.float64)
        for i, bn in enumerate(bones):
            if bn["parent"] >= 0: bones[bn["parent"]]["children"].append(i)
        skel = {"bones": bones,
                "root_indices": [i for i, bn in enumerate(bones) if bn["parent"] == -1]}
    subs = []
    for mesh in g.get("meshes", []):
        for pr in mesh.get("primitives", []):
            at = pr.get("attributes", {})
            if "POSITION" not in at: continue
            P = _acc(g, b, at["POSITION"])
            Nn = _acc(g, b, at["NORMAL"]) if "NORMAL" in at else None
            U = _acc(g, b, at["TEXCOORD_0"]) if "TEXCOORD_0" in at else None
            J = _acc(g, b, at["JOINTS_0"]) if "JOINTS_0" in at else None
            W = _acc(g, b, at["WEIGHTS_0"]) if "WEIGHTS_0" in at else None
            if Nn is None: Nn = np.zeros_like(P)
            if U is None: U = np.zeros((len(P), 2), dtype=np.float32)
            if W is not None:
                W = W.astype(np.float32); s = W.sum(axis=1, keepdims=True)
                s[s < 1e-6] = 1.0; W = W / s
            idx = _acc(g, b, pr["indices"]).reshape(-1) if "indices" in pr \
                else np.arange(len(P), dtype=np.uint32)
            n = len(idx); o = np.zeros((n, 8), dtype=np.float32)
            jt = np.zeros((n, 4), dtype=np.uint16) if J is not None else None
            wt = np.zeros((n, 4), dtype=np.float32) if W is not None else None
            for k in range(n):
                s = int(idx[k]); o[k, 0:3] = P[s]; o[k, 3:6] = Nn[s]; o[k, 6:8] = U[s]
                if J is not None: jt[k] = J[s]
                if W is not None: wt[k] = W[s]
            tp = None; am = "OPAQUE"; av = 1.0
            if "material" in pr and pr["material"] < len(g.get("materials", [])):
                mt = g["materials"][pr["material"]]
                am = str(mt.get("alphaMode", "OPAQUE")).upper()
                pbr = mt.get("pbrMetallicRoughness", {})
                bcf = pbr.get("baseColorFactor")
                if bcf and len(bcf) >= 4: av = float(bcf[3])
                if "baseColorTexture" in pbr:
                    tp = _tex(g, b, bd, pbr["baseColorTexture"]["index"])
            subs.append({"verts": o, "joints": jt, "weights": wt, "texture_path": tp,
                         "transparent": (am == "BLEND") or (av < 0.99),
                         "name": mesh.get("name", "")})
    anims = {}
    if skel:
        for a in g.get("animations", []):
            nm = a.get("name", f"a{len(anims)}"); chs = []; dur = 0.0
            for ch in a.get("channels", []):
                ni = ch.get("target", {}).get("node"); pt = ch.get("target", {}).get("path", "")
                if ni not in b2i: continue
                sm = a["samplers"][ch["sampler"]]
                ts = _acc(g, b, sm["input"]).reshape(-1); vs = _acc(g, b, sm["output"])
                if len(ts) > 0: dur = max(dur, float(ts[-1]))
                chs.append({"bone_idx": b2i[ni], "path": pt, "times": ts, "values": vs})
            anims[nm] = {"name": nm, "duration": max(0.001, dur), "channels": chs}
    return subs, skel, anims


def _mdl_texture(hint, model_dir):
    if not hint: return None
    p = None
    try:
        if _MDL_RESOLVE_TEX is not None:
            p = _MDL_RESOLVE_TEX(hint)
    except Exception:
        p = None
    if p is None or not os.path.isfile(str(p)):
        base = os.path.splitext(os.path.basename(str(hint).replace("\\", "/")))[0]
        p = None
        for ext in (".png", ".jpg", ".jpeg", ".tga", ".bmp"):
            c = os.path.join(model_dir, base + ext)
            if os.path.isfile(c):
                p = c
                break
    if p is None or not os.path.isfile(str(p)):
        return None
    p = str(p)
    if p.lower().endswith(".vtf"):
        try:
            from engine_pkg.resources.mdl_loader import vtf_to_png
            png = os.path.splitext(p)[0] + ".png"
            if vtf_to_png(p, png):
                return png
        except Exception as e:
            print(f"[mdl] vtf convert failed: {e}")
        return None
    return p


def _mdl_skel_to_dict(skel):
    bones = []
    for b in skel.bones:
        bones.append({
            "name": getattr(b, "name", f"b{b.index}"),
            "index": int(b.index),
            "parent": int(b.parent),
            "children": list(getattr(b, "children", []) or []),
            "local_bind": np.asarray(b.local_bind, dtype=np.float64).reshape(4, 4),
            "inverse_bind": np.asarray(b.inverse_bind, dtype=np.float64).reshape(4, 4),
        })
    roots = list(getattr(skel, "root_indices", []) or [])
    if not roots:
        roots = [b["index"] for b in bones if b["parent"] == -1]
    return {"bones": bones, "root_indices": roots}


def _mdl_anims_to_dict(animations):
    out = {}
    for name, a in (animations or {}).items():
        dur = float(getattr(a, "duration", 1.0) or 1.0)
        chs = getattr(a, "channels", None)
        if chs:
            out[name] = {
                "name": name, "duration": max(0.001, dur),
                "channels": [
                    {"bone_idx": int(c.target_bone), "path": c.path,
                     "times": np.asarray(c.times, dtype=np.float64),
                     "values": np.asarray(c.values)}
                    for c in chs],
            }
            continue
        fa = getattr(a, "frame_at", None)
        if fa is not None:
            out[name] = {"name": name, "duration": max(0.001, dur),
                         "channels": [], "frame_at": fa}
    return out


def _mdl_sequence_sources(fp, raw):
    from engine_pkg.resources.mdl_anim import sequence_names
    out = {}
    try:
        for nm in sequence_names(raw):
            out[nm] = fp
    except Exception:
        pass
    try:
        from engine_pkg.resources.mdl_loader import _parse_mdl, _read_includes
        from engine_pkg.core.config import BASE_DIR
    except Exception:
        return out
    try:
        hdr = _parse_mdl(fp)
        base = os.path.dirname(os.path.abspath(fp))
        roots = [base, os.path.dirname(base), os.path.dirname(os.path.dirname(base)),
                 str(BASE_DIR)]
        for inc in _read_includes(hdr, raw):
            cands = [os.path.join(r, inc) for r in roots]
            cands.append(os.path.join(base, os.path.basename(inc)))
            for c in cands:
                if os.path.isfile(c):
                    try:
                        idata = open(c, "rb").read()
                        for nm in sequence_names(idata):
                            out.setdefault(nm, c)
                    except Exception:
                        pass
                    break
    except Exception as e:
        print(f"[hlmv] include sweep skipped: {e}")
    return out


def load_mdl(fp):
    if _MDL_LOADER is None:
        print("[hlmv] .mdl support not available (engine_pkg not importable)")
        return [], None, {}
    try:
        data = _MDL_LOADER(fp)
    except Exception as e:
        print(f"[hlmv] mdl load failed: {e}")
        traceback.print_exc()
        return [], None, {}
    if not data:
        return [], None, {}
    submeshes, skeleton, animations = data
    model_dir = os.path.dirname(os.path.abspath(fp))
    subs = []
    for sub in submeshes:
        verts = np.asarray(sub["verts"], dtype=np.float32).reshape(-1, 8)
        joints = sub.get("joints")
        weights = sub.get("weights")
        idx = sub.get("indices")
        if idx is not None and len(idx) >= 3:
            idx = np.asarray(idx, dtype=np.int64).reshape(-1)
            idx = idx[(idx >= 0) & (idx < len(verts))]
            if len(idx) >= 3:
                verts = verts[idx]
                if joints is not None:
                    joints = np.asarray(joints, dtype=np.float32)[idx]
                if weights is not None:
                    weights = np.asarray(weights, dtype=np.float32)[idx]
        hint = sub.get("texture_hint") or ""
        transparent = bool(sub.get("transparent", False))
        subs.append({
            "verts": verts,
            "joints": None if joints is None else np.asarray(joints, dtype=np.float32),
            "weights": None if weights is None else np.asarray(weights, dtype=np.float32),
            "texture_path": _mdl_texture(hint, model_dir),
            "transparent": transparent,
            "alpha_test": transparent,
            "name": hint,
        })
    skel = _mdl_skel_to_dict(skeleton) if skeleton is not None else None
    anims = _mdl_anims_to_dict(animations)
    try:
        with open(fp, "rb") as fh:
            raw = fh.read()
        for nm, src in _mdl_sequence_sources(fp, raw).items():
            if nm not in anims:
                anims[nm] = {"name": nm, "duration": 1.0, "channels": [],
                             "lazy": (src, nm)}
    except Exception as e:
        print(f"[hlmv] sequence table skipped: {e}")
    return subs, skel, anims


_MDL_SEQ_CACHE = {}


def _mdl_build_sequence(path, name):
    key = (path, name)
    if key in _MDL_SEQ_CACHE:
        return _MDL_SEQ_CACHE[key]
    out = None
    try:
        from engine_pkg.resources.mdl_anim import build_animation
        a = build_animation(path, name)
        if a is not None and a.channels:
            out = {
                "name": name, "duration": max(0.001, float(a.duration)),
                "channels": [
                    {"bone_idx": int(c.target_bone), "path": c.path,
                     "times": np.asarray(c.times, dtype=np.float64),
                     "values": np.asarray(c.values)}
                    for c in a.channels],
            }
    except Exception as e:
        print(f"[hlmv] sequence '{name}' failed: {e}")
    _MDL_SEQ_CACHE[key] = out
    return out


def _prune_mdl_seq_cache(model_path):
    try:
        cur = os.path.abspath(model_path)
        for k in list(_MDL_SEQ_CACHE.keys()):
            if os.path.abspath(k[0]) != cur:
                del _MDL_SEQ_CACHE[k]
    except Exception:
        pass


def load_model(fp):
    e = os.path.splitext(fp)[1].lower()
    if e == ".obj": return load_obj(fp) or [], None, {}
    if e in (".gltf", ".glb"): return load_gltf(fp)
    if e == ".mdl": return load_mdl(fp)
    if e in MODEL_EXTS:
        try:
            from engine_pkg.resources.model import load_model_mesh
            raw = load_model_mesh(fp) or []
            subs = []
            for s in raw:
                verts = np.asarray(s["verts"], dtype=np.float32).reshape(-1, 8)
                subs.append({
                    "verts": verts,
                    "indices": s.get("indices"),
                    "texture_path": s.get("texture_hint") or "",
                })
            return subs, None, {}
        except Exception as e2:
            print(f"[hlmv] load failed ({e}): {e2}")
            return [], None, {}
    return [], None, {}


def _slerp(q0, q1, t):
    q0 = np.asarray(q0, float); q1 = np.asarray(q1, float)
    d = float(np.dot(q0, q1))
    if d < 0: q1 = -q1; d = -d
    if d > 0.9995:
        r = q0 + (q1 - q0) * t; n = np.linalg.norm(r)
        return r / n if n > 1e-8 else q0
    th0 = math.acos(max(-1, min(1, d))); th = th0 * t
    return q0 * (math.cos(th) - d * math.sin(th) / math.sin(th0)) \
        + q1 * math.sin(th) / math.sin(th0)


def _sample(a, t):
    out = {}
    for ch in a["channels"]:
        ts, vs = ch["times"], ch["values"]; K = len(ts)
        if K == 0: continue
        if K == 1 or t <= ts[0]: v = vs[0]
        elif t >= ts[-1]: v = vs[-1]
        else:
            lo, hi = 0, K - 1
            while hi - lo > 1:
                m = (lo + hi) // 2
                if ts[m] <= t: lo = m
                else: hi = m
            f = (t - ts[lo]) / max(ts[hi] - ts[lo], 1e-6)
            v = _slerp(vs[lo], vs[hi], f) if ch["path"] == "rotation" \
                else vs[lo] + (vs[hi] - vs[lo]) * f
        out.setdefault(ch["bone_idx"], {})[ch["path"]] = v
    return out


def _dtrs(m):
    T = m[:3, 3].copy()
    sx = float(np.linalg.norm(m[:3, 0])); sy = float(np.linalg.norm(m[:3, 1]))
    sz = float(np.linalg.norm(m[:3, 2]))
    R = m[:3, :3].copy()
    if sx > 1e-9: R[:, 0] /= sx
    if sy > 1e-9: R[:, 1] /= sy
    if sz > 1e-9: R[:, 2] /= sz
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1) * 2; w = 0.25 * s
        x = (R[2, 1]-R[1, 2])/s; y = (R[0, 2]-R[2, 0])/s; z = (R[1, 0]-R[0, 1])/s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1+R[0, 0]-R[1, 1]-R[2, 2]) * 2
        w = (R[2, 1]-R[1, 2])/s; x = 0.25*s
        y = (R[0, 1]+R[1, 0])/s; z = (R[0, 2]+R[2, 0])/s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1+R[1, 1]-R[0, 0]-R[2, 2]) * 2
        w = (R[0, 2]-R[2, 0])/s; x = (R[0, 1]+R[1, 0])/s
        y = 0.25*s; z = (R[1, 2]+R[2, 1])/s
    else:
        s = math.sqrt(1+R[2, 2]-R[0, 0]-R[1, 1]) * 2
        w = (R[1, 0]-R[0, 1])/s; x = (R[0, 2]+R[2, 0])/s
        y = (R[1, 2]+R[2, 1])/s; z = 0.25*s
    return T, np.array([x, y, z, w]), np.array([sx, sy, sz])


def _worlds(skel, pose):
    bs = skel["bones"]; n = len(bs); wld = [None] * n
    def walk(i, p):
        b = bs[i]; pth = pose.get(i, {})
        T, R, S = _dtrs(b["local_bind"])
        l = _trs(pth.get("translation", T), pth.get("rotation", R), pth.get("scale", S))
        w = p @ l; wld[i] = w
        for c in b["children"]: walk(c, w)
    for r in skel["root_indices"]: walk(r, np.eye(4, dtype=np.float64))
    for i in range(n):
        if wld[i] is None: wld[i] = bs[i]["local_bind"]
    return wld


def bone_mats(skel, pose):
    wl = _worlds(skel, pose); bs = skel["bones"]
    out = np.zeros((len(bs), 16), dtype=np.float32)
    for i in range(len(bs)):
        M = (wl[i] @ bs[i]["inverse_bind"]).astype(np.float32)
        out[i] = M.reshape(-1, order="F")
    return out


class Renderer:
    def __init__(self, ctx, W, H):
        self.ctx = ctx; self.W = W; self.H = H
        self.prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
        try: self.skin = ctx.program(vertex_shader=SKIN_VS, fragment_shader=FS)
        except Exception: self.skin = self.prog
        self.cube = ctx.program(vertex_shader=CUBE_VS, fragment_shader=CUBE_FS)
        self.ctex = ctx.texture((W, H), 4); self.dtex = ctx.depth_texture((W, H))
        self.fbo = ctx.framebuffer(color_attachments=[self.ctex], depth_attachment=self.dtex)
        self.cbuf = ctx.buffer(CUBE_DATA.tobytes())
        self.cvao = ctx.vertex_array(self.cube, [(self.cbuf, "3f", "in_pos")])
        self.sky_prog = ctx.program(vertex_shader=SKY_VS, fragment_shader=SKY_FS)
        self.sky_qbuf = ctx.buffer(SKY_QUAD.tobytes())
        self.sky_vao = ctx.vertex_array(self.sky_prog, [(self.sky_qbuf, "2f", "in_pos")])
        self.sky_tex = None; self.sky_path = None; self.sky_hdr = False
        self.sky_size = (0, 0); self.sky_exposure = 1.0
        d = np.array([[[220, 220, 220, 255]]], dtype=np.uint8).tobytes()
        self.solid = ctx.texture((1, 1), 4, d)
        self.solid.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.tcache = {}
        self.yaw = math.radians(35); self.pitch = math.radians(18)
        self.dist = 2.5; self.tgt = glm.vec3(0); self.fov = 65.0
        self.subs = []; self.skel = None; self.anims = {}; self.anames = []
        self.cur = None; self.atime = 0.0; self.playing = True
        self.bm = None; self.bw = []; self.stats = {"v": 0, "t": 0, "b": 0, "a": 0}
        self._bind_world = None
        self.center = (0, 0, 0); self.size = 1.0
        self.rot_x = 0.0
        self.opt = {"normal_mapping": True, "attachments": False, "bones": False,
                    "normals": False, "tangents": False, "wireframe": False,
                    "specular": True, "ground": False, "movement": False, "background": True,
                    "hit_boxes": False, "seq_boxes": False, "shadow": False,
                    "software_skin": False, "physics_model": False, "enable_ik": False,
                    "head_turn": False, "illum_position": False, "play_sounds": False,
                    "overbright": True, "sky": False}

    def _flush_textures(self):
        for t in self.tcache.values():
            if t is not self.solid:
                try: t.release()
                except Exception: pass
        self.tcache.clear()

    def _release_all_subs(self):
        for s in self.subs:
            _release_sub(s)
        self.subs = []

    def close(self):
        self._release_all_subs()
        self._flush_textures()
        self._release_sky()
        if getattr(self, "fbo", None) is not None:
            try: self.fbo.release()
            except Exception: pass
            self.fbo = None
        for name in ("ctex", "dtex", "cvao", "cbuf", "sky_vao", "sky_qbuf"):
            obj = getattr(self, name, None)
            if obj is not None:
                try: obj.release()
                except Exception: pass
                setattr(self, name, None)
        if getattr(self, "solid", None) is not None:
            try: self.solid.release()
            except Exception: pass
            self.solid = None
        seen = set()
        for name in ("prog", "skin", "cube", "sky_prog"):
            obj = getattr(self, name, None)
            if obj is None or id(obj) in seen: continue
            seen.add(id(obj))
            try: obj.release()
            except Exception: pass
            setattr(self, name, None)

    # ---------------- sky ----------------
    def _release_sky(self):
        if getattr(self, "sky_tex", None) is not None:
            try: self.sky_tex.release()
            except Exception: pass
        self.sky_tex = None; self.sky_path = None
        self.sky_hdr = False; self.sky_size = (0, 0)

    def load_sky(self, fp):
        """Load an equirectangular sky panorama; returns (ok, error message)."""
        try:
            rgb, is_hdr = load_sky_image(fp)
        except Exception as e:
            print(f"[sky] {os.path.basename(fp)}: {e}")
            return False, str(e)
        if rgb is None or getattr(rgb, "size", 0) == 0:
            return False, "image has no pixels"
        if is_hdr:
            # keep the tonemap in the shader finite even for absurd pixel values
            rgb = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 65504.0)
        rgb = _sky_downsample(rgb, _SKY_MAX_HDR_W if is_hdr else _SKY_MAX_LDR_W)
        h, w = int(rgb.shape[0]), int(rgb.shape[1])
        max_tex = 16384
        try: max_tex = int(self.ctx.info.get("GL_MAX_TEXTURE_SIZE") or 16384)
        except Exception: pass
        if w > max_tex or h > max_tex:
            return False, f"{w}x{h} is larger than this GPU supports ({max_tex} px)"

        tex = None
        try:
            if is_hdr:
                data = np.ascontiguousarray(rgb, dtype="f4").tobytes()
                tex = self.ctx.texture((w, h), 3, data, dtype="f4")
            else:
                u8 = (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype("u1")
                tex = self.ctx.texture((w, h), 3, np.ascontiguousarray(u8).tobytes())
            try:
                tex.build_mipmaps()
                tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            except Exception:
                tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            tex.repeat_x = True; tex.repeat_y = False
        except Exception as e:
            if tex is not None:
                try: tex.release()
                except Exception: pass
            traceback.print_exc()
            return False, f"GPU upload failed: {e}"

        self._release_sky()
        self.sky_tex = tex
        self.sky_path = fp
        self.sky_hdr = bool(is_hdr)
        self.sky_size = (w, h)
        self.sky_exposure = _sky_exposure(rgb) if is_hdr else 1.0
        self.opt["sky"] = True
        return True, ""

    def _draw_sky(self, eye):
        if self.sky_tex is None:
            return
        ctx = self.ctx
        sp = self.sky_prog
        fwd = glm.normalize(self.tgt - eye)
        right = glm.cross(fwd, glm.vec3(0.0, 1.0, 0.0))
        n = float(glm.length(right))
        right = glm.vec3(1.0, 0.0, 0.0) if n < 1e-6 else right / n
        up = glm.cross(right, fwd)
        try:
            sp["sky_right"].value = (float(right.x), float(right.y), float(right.z))
            sp["sky_up"].value = (float(up.x), float(up.y), float(up.z))
            sp["sky_back"].value = (float(-fwd.x), float(-fwd.y), float(-fwd.z))
            sp["sky_tan_half_fov"].value = math.tan(math.radians(max(1.0, self.fov) * 0.5))
            sp["sky_aspect"].value = float(self.W) / float(max(1, self.H))
            sp["sky_exposure"].value = float(self.sky_exposure)
            sp["sky_tonemap"].value = 1 if self.sky_hdr else 0
            sp["sky_tex"].value = 0
        except Exception:
            pass
        self.sky_tex.use(0)
        ctx.disable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        ctx.depth_mask = False
        try:
            self.sky_vao.render()
        finally:
            ctx.depth_mask = True
            ctx.enable(moderngl.DEPTH_TEST)
            ctx.enable(moderngl.CULL_FACE)

    def _tex(self, p):
        if not p: return self.solid
        if isinstance(p, tuple):
            key = ("b", p[1], len(p[0]))
        else:
            key = p
        if key in self.tcache: return self.tcache[key]
        try:
            if isinstance(p, tuple):
                s = pygame.image.load(io.BytesIO(p[0]), p[1])
            else:
                s = pygame.image.load(p)
            try: s = s.convert_alpha()
            except Exception:
                try: s = s.convert()
                except Exception: pass
            w, h = s.get_size(); d = pygame.image.tobytes(s, "RGBA", True)
            t = self.ctx.texture((w, h), 4, d)
            t.build_mipmaps()
            t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            t.repeat_x = True; t.repeat_y = True
            self.tcache[key] = t; return t
        except Exception as e:
            print(f"[tex] fail: {e}")
            self.tcache[key] = self.solid; return self.solid

    def _dedupe(self, a):
        sc = 10**6; seen = {}; uq = []; idx = np.empty(len(a), dtype=np.uint32)
        for i, r in enumerate(a):
            k = tuple(int(round(float(r[j]) * sc)) for j in range(8))
            ix = seen.get(k)
            if ix is None: ix = len(uq); seen[k] = ix; uq.append(r)
            idx[i] = ix
        return np.asarray(uq, dtype=np.float32), idx

    def load(self, fp):
        try: subs, skel, anims = load_model(fp)
        except Exception as e:
            print(f"[load] {e}"); traceback.print_exc(); return False
        if not subs: return False

        allp = np.concatenate([s["verts"].reshape(-1, 8)[:, :3] for s in subs], axis=0)
        mn = allp.min(0); mx = allp.max(0); c = (mn + mx) * 0.5; sz = float((mx - mn).max())
        if sz < 1e-9: sz = 1.0
        center = (float(c[0]), float(c[1]), float(c[2])); size = sz

        sizes = mx - mn
        rot_x = 0.0
        has_skel = any(s.get("joints") is not None for s in subs)
        if (has_skel and sizes[1] > 1e-6
                and os.path.splitext(fp)[1].lower() != ".mdl"):
            if sizes[2] > sizes[1] * 1.6 and sizes[2] > sizes[0] * 1.2:
                rot_x = -90.0

        new_subs = []
        tv = tt = 0
        try:
            for sub in subs:
                a = sub["verts"].reshape(-1, 8)
                n = len(a); tv += n; tt += n // 3
                uq, idx = self._dedupe(a)
                vbo = ibo = vao = sva = jv = wv = None
                try:
                    vbo = self.ctx.buffer(uq.tobytes())
                    ibo = self.ctx.buffer(idx.tobytes())
                    vao = self.ctx.vertex_array(self.prog,
                        [(vbo, "3f 3f 2f", "in_pos", "in_normal", "in_uv")], ibo)
                    if sub.get("joints") is not None and sub.get("weights") is not None:
                        sc = 10**6; seen = {}; uj = []; uw = []
                        for i, r in enumerate(a):
                            k = tuple(int(round(float(r[j]) * sc)) for j in range(8))
                            if k not in seen:
                                seen[k] = len(uj); uj.append(sub["joints"][i])
                                uw.append(sub["weights"][i])
                        uj = np.asarray(uj, dtype=np.float32)
                        uw = np.asarray(uw, dtype=np.float32)
                        if len(uj) == len(uq):
                            jv = self.ctx.buffer(uj.tobytes())
                            wv = self.ctx.buffer(uw.tobytes())
                            try:
                                sva = self.ctx.vertex_array(self.skin,
                                    [(vbo, "3f 3f 2f", "in_pos", "in_normal", "in_uv"),
                                     (jv, "4f", "in_joints"), (wv, "4f", "in_weights")], ibo)
                            except Exception:
                                for o in (jv, wv):
                                    if o is not None:
                                        try: o.release()
                                        except Exception: pass
                                jv = wv = None
                    tp = sub.get("texture_path")
                    tn = os.path.basename(tp) if isinstance(tp, str) else ""
                    new_subs.append({
                        "vao": vao, "vbo": vbo, "ibo": ibo,
                        "skinned_vao": sva, "jv": jv, "wv": wv,
                        "texture_path": tp, "tex_name": tn,
                        "transparent": sub.get("transparent", False),
                        "alpha_test": bool(sub.get("alpha_test", False))})
                except Exception:
                    for o in (sva, vao, jv, wv, vbo, ibo):
                        if o is not None:
                            try: o.release()
                            except Exception: pass
                    raise
        except Exception as e:
            for s in new_subs:
                _release_sub(s)
            print(f"[load] build failed: {e}"); traceback.print_exc()
            return False

        self._release_all_subs()
        self.subs = new_subs

        self.center = center; self.size = size; self.rot_x = rot_x
        self.skel = skel; self.anims = anims; self.anames = sorted(anims.keys())
        self.cur = None
        for nm in self.anames:
            a = anims.get(nm) or {}
            if a.get("channels") or a.get("frame_at"):
                self.cur = nm
                break
        if self.cur is None:
            self.cur = self.anames[0] if self.anames else None
        self.atime = 0.0
        self._bind_world = None
        if self.skel:
            self._bind_world = []
            for b in self.skel["bones"]:
                try:
                    inv = np.asarray(b["inverse_bind"], dtype=np.float64).reshape(4, 4)
                    self._bind_world.append(np.linalg.inv(inv))
                except Exception:
                    self._bind_world.append(np.eye(4, dtype=np.float64))
        self.stats = {"v": tv, "t": tt,
                      "b": len(skel["bones"]) if skel else 0, "a": len(anims)}
        if self.skel:
            self.bm = bone_mats(self.skel, {}); self.bw = _worlds(self.skel, {})
        else:
            self.bm = None; self.bw = []
        self.tgt = glm.vec3(*self.center)
        r = max(1e-4, self.size) * 0.75
        self.dist = r / max(0.05, math.tan(math.radians(self.fov * 0.5))) * 1.35

        _prune_mdl_seq_cache(fp)
        return True

    def update(self, dt):
        a = self.anims.get(self.cur) if self.cur else None
        if (a is not None and a.get("lazy") and not a.get("channels")
                and not a.get("frame_at")):
            path, nm = a["lazy"]
            built = _mdl_build_sequence(path, nm)
            if built is not None:
                self.anims[self.cur] = built
                a = built
            else:
                a.pop("lazy", None)
                a["no_data"] = True
        if self.playing and a:
            self.atime += dt
            d = float(a.get("duration", 1.0) or 1.0)
            if d > 1e-6 and self.atime > d:
                self.atime %= d
        if a is None:
            return
        fa = a.get("frame_at")
        if fa is not None:
            self.bm = np.asarray(fa(self.atime), dtype=np.float32)
            if self.opt.get("bones") and self._bind_world:
                n = min(len(self.bm), len(self._bind_world))
                try:
                    self.bw = [self.bm[i].reshape(4, 4, order="F") @ self._bind_world[i]
                               for i in range(n)]
                except Exception:
                    pass
            return
        if not self.skel:
            return
        p = _sample(a, self.atime)
        self.bm = bone_mats(self.skel, p); self.bw = _worlds(self.skel, p)

    def eye(self):
        cp = math.cos(self.pitch)
        off = glm.vec3(math.sin(self.yaw)*cp, math.sin(self.pitch),
                       math.cos(self.yaw)*cp) * self.dist
        return self.tgt + off

    def render(self):
        ctx = self.ctx
        self.fbo.use(); ctx.viewport = (0, 0, self.W, self.H)
        ctx.enable(moderngl.DEPTH_TEST); ctx.enable(moderngl.CULL_FACE)
        if self.opt.get("background", True): ctx.clear(0.196, 0.196, 0.196, 1.0)
        else: ctx.clear(0, 0, 0, 1.0)
        e = self.eye()
        if self.opt.get("sky") and self.sky_tex is not None:
            try:
                self._draw_sky(e)
            except Exception as ex:
                print(f"[sky] {ex}")
        if self.subs:
            view = glm.lookAt(e, self.tgt, glm.vec3(0, 1, 0))
            proj = glm.perspective(glm.radians(self.fov), self.W / self.H, 0.01, 10000.0)

            mm = glm.mat4(1.0)
            if self.rot_x:
                mm = glm.rotate(glm.mat4(1.0), glm.radians(self.rot_x),
                                glm.vec3(1, 0, 0))
            mm_b = mm.to_bytes()

            for sp in [self.prog, self.skin]:
                try:
                    sp["proj"].write(proj.to_bytes()); sp["view"].write(view.to_bytes())
                    sp["model"].write(mm_b)
                    sp["cam_pos"].value = (float(e.x), float(e.y), float(e.z))
                    sp["light_dir"].value = (0.4, 0.85, 0.35)
                    sp["light_color"].value = (1.0, 0.97, 0.92)
                    sp["ambient_color"].value = (0.42, 0.45, 0.50)
                    sp["light_strength"].value = 1.0
                    sp["spec_shininess"].value = 32.0 if self.opt["specular"] else 1.0
                    sp["spec_strength"].value = 0.25 if self.opt["specular"] else 0.0
                    sp["spec_tint"].value = (1.0, 1.0, 1.0)
                except Exception: pass
            if self.skel and self.bm is not None and self.skin is not self.prog:
                sp = self.skin; n = min(len(self.skel["bones"]), 64)
                pad = np.zeros((64, 16), dtype=np.float32); pad[:n] = self.bm[:n]
                em = np.eye(4, dtype=np.float32).reshape(16, order="F")
                for i in range(n, 64): pad[i] = em
                try:
                    sp["bone_count"].value = n
                    sp["bone_matrices"].write(pad.tobytes())
                except Exception: pass
            if self.opt["wireframe"]:
                try: ctx.polygon_mode = moderngl.LINE
                except Exception: pass
            ctx.disable(moderngl.BLEND); ctx.depth_mask = True
            us = self.skel is not None and self.bm is not None
            for sub in self.subs:
                if sub["transparent"]: continue
                self._draw(sub, us)
            tr = [s for s in self.subs if s["transparent"]]
            if tr:
                ctx.enable(moderngl.BLEND)
                ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
                ctx.depth_mask = False
                for sub in tr: self._draw(sub, us)
                ctx.depth_mask = True; ctx.disable(moderngl.BLEND)
            if self.opt["wireframe"]:
                try: ctx.polygon_mode = moderngl.FILL
                except Exception: pass
            if self.opt["bones"] and self.bw:
                self._bones(proj, view)
        d = self.fbo.read(components=3, dtype='f1')
        a = np.frombuffer(d, dtype=np.uint8).reshape(self.H, self.W, 3)[::-1]
        png = _png(self.W, self.H, a.tobytes())
        return base64.b64encode(png)

    def _draw(self, sub, us):
        sp = self.prog; v = sub["vao"]
        if us and sub.get("skinned_vao") is not None:
            sp = self.skin; v = sub["skinned_vao"]
        if v is None:
            return
        t = self._tex(sub.get("texture_path")); t.use(0)
        try: sp["tex"].value = 0
        except Exception: pass
        try: sp["alpha_test"].value = 1 if sub.get("alpha_test") else 0
        except Exception: pass
        v.render()

    def _bones(self, proj, view):
        ctx = self.ctx; sp = self.cube
        sp["proj"].write(proj.to_bytes()); sp["view"].write(view.to_bytes())
        sp["color"].value = (1.0, 0.25, 0.15, 1.0)
        mk = max(0.004, self.size * 0.008)
        rot = glm.mat4(1.0)
        if self.rot_x:
            rot = glm.rotate(glm.mat4(1.0), glm.radians(self.rot_x),
                             glm.vec3(1, 0, 0))
        ctx.disable(moderngl.CULL_FACE)
        for w in self.bw:
            if w is None: continue
            p = glm.vec3(float(w[0, 3]), float(w[1, 3]), float(w[2, 3]))
            wp = (rot * glm.vec4(p, 1.0)).xyz
            m = glm.translate(glm.mat4(1.0), wp)
            m = glm.scale(m, glm.vec3(mk, mk, mk))
            sp["model"].write(m.to_bytes()); self.cvao.render()
        ctx.enable(moderngl.CULL_FACE)


class App:
    W, H = 625, 660
    VW, VH = 625, 420
    FRAME_MS = 33

    def __init__(self):
        if HAS_DND:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()
        self.root.title("Anima Model Viewer v1.1")
        self.root.geometry(f"{self.W}x{self.H}")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        import tkinter.font as tkf
        fams = set(tkf.families())
        fn = "Tahoma" if "Tahoma" in fams else (
            "Microsoft Sans Serif" if "Microsoft Sans Serif" in fams else "Arial")
        self.fu = (fn, 8)

        self.r = Renderer(ctx_gl, self.VW, self.VH)
        self.path = None; self._photo = None; self._img = None
        self._drag = None; self._dpos = (0, 0); self._tick = None
        self._dirty = True
        self._closing = False
        self._img_windows = weakref.WeakSet()
        self._preview_active = False
        self._browser_win = None
        self._thumb_cache = {}

        self._menu(); self._viewport(); self._tabs(); self._bottom(); self._keys()
        self.root.after(50, self._loop)
        self.root.after(300, self._auto)
        self.root.after(100, self._reg_dnd)

    def _on_close(self):
        if self._closing: return
        self._closing = True
        self._preview_active = False
        if getattr(self, "_browser_win", None):
            try: self._browser_win.destroy()
            except Exception: pass
            self._browser_win = None
        try: self.r.close()
        except Exception: pass
        try: pygame.quit()
        except Exception: pass
        try: self.root.destroy()
        except Exception: pass

    def _auto(self):
        if self._closing: return
        base = os.path.dirname(os.path.abspath(__file__))
        for d in [os.path.join(os.path.dirname(base), "models"), base]:
            if not os.path.isdir(d): continue
            for r, _, fs in os.walk(d):
                for f in fs:
                    if os.path.splitext(f)[1].lower() in MODEL_EXTS:
                        if self.load(os.path.join(r, f), quiet=True):
                            return

    def _reg_dnd(self):
        if not HAS_DND: return
        def walk(w):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind('<<Drop>>', self._on_drop)
            except Exception: pass
            for c in w.winfo_children(): walk(c)
        walk(self.root)

    def _on_drop(self, e):
        try: files = self.root.tk.splitlist(e.data)
        except Exception: files = e.data.split()

        models = []; textures = []; skies = []
        for f in files:
            f = f.strip("{}").strip()
            if not f: continue
            ext = os.path.splitext(f)[1].lower()
            if ext in MODEL_EXTS: models.append(f)
            elif ext in SKY_EXTS: skies.append(f)
            elif ext in TEX_EXTS: textures.append(f)

        if skies: self.load_sky(skies[0])
        if models: self.load(models[0])
        if textures:
            if self.r.subs:
                if messagebox.askyesno("Apply Texture", "Apply as texture?"):
                    self._apply_textures(textures)
                else:
                    for p in textures: self._open_image_viewer(p)
            else:
                for p in textures: self._open_image_viewer(p)

    def _open_image_viewer(self, path):
        try: img = tk.PhotoImage(file=path)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot load:\n{path}\n\n{e}")
            return
        win = tk.Toplevel(self.root)
        win.title(f"Image — {os.path.basename(path)}")
        iw, ih = img.width(), img.height()
        cw = min(iw + 20, 900); ch = min(ih + 40, 700)
        win.geometry(f"{cw}x{ch}")
        canvas = tk.Canvas(win, bg='#222222', highlightthickness=0)
        canvas.pack(fill='both', expand=True)
        scale = min(cw / iw, ch / ih, 1.0)
        if scale < 1.0:
            factor = max(1, int(1 / scale))
            img = img.subsample(factor, factor)
        canvas.create_image(cw // 2, ch // 2, image=img, anchor='center')
        canvas.image = img
        tk.Label(win, text=f"{os.path.basename(path)}  |  {iw}×{ih}",
                 bg='#333333', fg='white', anchor='w').pack(side='bottom', fill='x')
        self._img_windows.add(win)

    def _menu(self):
        mb = tk.Menu(self.root)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="Open Model...\tCtrl+O", command=self.open)
        fm.add_command(label="Load Model...\tCtrl+L", command=self.open_model_browser)
        fm.add_command(label="Apply Texture...\tCtrl+T", command=self.apply_texture_dialog)
        fm.add_command(label="Open Image Viewer...\tCtrl+I", command=self.open_image_dialog)
        fm.add_command(label="Open Sky...\tCtrl+K", command=self.open_sky_dialog)
        fm.add_command(label="Refresh", command=self.refresh)
        fm.add_separator()
        fm.add_command(label="Exit\tAlt+F4", command=self._on_close)
        mb.add_cascade(label="File", menu=fm)
        om = tk.Menu(mb, tearoff=0)
        om.add_command(label="Reset Camera\tR", command=self.reset)
        om.add_command(label="Toggle Wireframe\tW", command=lambda: self._tog("wireframe"))
        om.add_command(label="Toggle Skeleton\tB", command=lambda: self._tog("bones"))
        om.add_command(label="Toggle Ground\tG", command=lambda: self._tog("ground"))
        om.add_command(label="Toggle Sky\tK", command=lambda: self._tog("sky"))
        om.add_command(label="Rotate Model Z-up\tZ", command=self._rot_model)
        mb.add_cascade(label="Options", menu=om)
        vm = tk.Menu(mb, tearoff=0)
        vm.add_command(label="Front\t1", command=lambda: self._view(0, 0))
        vm.add_command(label="Back\t2", command=lambda: self._view(180, 0))
        vm.add_command(label="Left\t3", command=lambda: self._view(-90, 0))
        vm.add_command(label="Right\t4", command=lambda: self._view(90, 0))
        vm.add_command(label="Top\t5", command=lambda: self._view(0, 89))
        vm.add_command(label="Bottom\t6", command=lambda: self._view(0, -89))
        mb.add_cascade(label="View", menu=vm)
        hm = tk.Menu(mb, tearoff=0)
        hm.add_command(label="About",
                       command=lambda: messagebox.showinfo("About", "Anima Model Viewer v1.1"))
        mb.add_cascade(label="Help", menu=hm)
        self.root.config(menu=mb)

    def _viewport(self):
        self.cv = tk.Canvas(self.root, width=self.VW, height=self.VH,
                            bg='#323232', highlightthickness=0, bd=0)
        self.cv.pack(side='top', fill='x')
        self.cv.bind('<ButtonPress-1>', lambda e: self._down(e, 'rot'))
        self.cv.bind('<ButtonRelease-1>', lambda e: self._up())
        self.cv.bind('<ButtonPress-3>', lambda e: self._down(e, 'pan'))
        self.cv.bind('<ButtonRelease-3>', lambda e: self._up())
        self.cv.bind('<B1-Motion>', self._move)
        self.cv.bind('<B3-Motion>', self._move)
        self.cv.bind('<MouseWheel>', self._wheel)

    def _tabs(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(side='top', fill='x')
        rt = ttk.Frame(self.nb); self.nb.add(rt, text="Render"); self._render_tab(rt)
        st = ttk.Frame(self.nb); self.nb.add(st, text="Sequence"); self._seq_tab(st)
        mt = ttk.Frame(self.nb); self.nb.add(mt, text="Model"); self._model_tab(mt)
        xt = ttk.Frame(self.nb); self.nb.add(xt, text="Texture"); self._tex_tab(xt)
        for nm in ["Flex", "Physics", "Bones", "Attachments", "IK Rule", "Events"]:
            t = ttk.Frame(self.nb); self.nb.add(t, text=nm)
            if nm == "Bones":
                self.btx = tk.Text(t, height=7, font=('Courier New', 8),
                                   state='disabled', bd=1, relief='solid')
                self.btx.pack(fill='both', expand=True, padx=4, pady=4)
            else:
                ttk.Label(t, text="(not implemented)",
                          foreground='#888').pack(anchor='nw', padx=8, pady=8)

    def _render_tab(self, p):
        c = ttk.Frame(p); c.pack(fill='both', expand=True, padx=6, pady=4)
        L = ttk.Frame(c); L.grid(row=0, column=0, sticky='nw', padx=(0, 18))
        self.bw_var = tk.StringVar(value="BoneWeights")
        ttk.Combobox(L, textvariable=self.bw_var,
                     values=["BoneWeights", "None"], state='readonly',
                     width=15).pack(anchor='w', pady=(0, 5))
        self.cv_vars = {}
        for lab, at, dv in [("Normal Mapping", "normal_mapping", 1),
                            ("Attachments (Ctrl+A)", "attachments", 0),
                            ("Bones (Ctrl+O)", "bones", 0),
                            ("Normals (Ctrl+N)", "normals", 0),
                            ("Tangents (Ctrl+T)", "tangents", 0),
                            ("Wireframe (Ctrl+W)", "wireframe", 0),
                            ("Specular", "specular", 1)]:
            v = tk.IntVar(value=dv); self.cv_vars[at] = v
            ttk.Checkbutton(L, text=lab, variable=v,
                            command=lambda a=at: self._chk(a)).pack(anchor='w')
        ob = tk.IntVar(value=1); self.cv_vars['overbright'] = ob
        ttk.Checkbutton(L, text="Enable Overbrightening", variable=ob,
                        command=lambda: self._chk('overbright')).pack(anchor='w', pady=(6, 0))
        M = ttk.Frame(c); M.grid(row=0, column=1, sticky='nw', padx=(0, 18))
        for lab, at, dv in [("Ground (Ctrl+G)", "ground", 0),
                            ("Movement (Ctrl+M)", "movement", 0),
                            ("Background (Ctrl+B)", "background", 1),
                            ("Hit Boxes (Ctrl+H)", "hit_boxes", 0),
                            ("Seq. Boxes", "seq_boxes", 0),
                            ("Shadow (Ctrl+S)", "shadow", 0),
                            ("Software Skin", "software_skin", 0)]:
            v = tk.IntVar(value=dv); self.cv_vars[at] = v
            ttk.Checkbutton(M, text=lab, variable=v,
                            command=lambda a=at: self._chk(a)).pack(anchor='w')
        self.none_var = tk.StringVar(value="None")
        ttk.Combobox(M, textvariable=self.none_var, values=["None"],
                     state='readonly', width=15).pack(anchor='w', pady=(5, 0))
        R = ttk.Frame(c); R.grid(row=0, column=2, sticky='nw')
        for lab, at, dv in [("Physics Model", "physics_model", 0),
                            ("Enable IK", "enable_ik", 0),
                            ("Head Turn", "head_turn", 0),
                            ("Illum. Position", "illum_position", 0),
                            ("Play Sounds", "play_sounds", 0),
                            ("Sky (K)", "sky", 0)]:
            v = tk.IntVar(value=dv); self.cv_vars[at] = v
            ttk.Checkbutton(R, text=lab, variable=v,
                            command=lambda a=at: self._chk(a)).pack(anchor='w')

    def _seq_tab(self, p):
        self.seq = tk.Listbox(p, height=7, font=self.fu, activestyle='none',
                              bd=1, relief='solid')
        self.seq.pack(fill='both', expand=True, padx=4, pady=4)
        self.seq.bind('<<ListboxSelect>>', self._seq_sel)

    def _model_tab(self, p):
        self.mtx = tk.Text(p, height=7, font=('Courier New', 9), bd=1,
                           relief='solid', state='disabled')
        self.mtx.pack(fill='both', expand=True, padx=4, pady=4)

    def _tex_tab(self, p):
        fr = ttk.Frame(p); fr.pack(fill='both', expand=True, padx=4, pady=4)
        self.tex_list = tk.Listbox(fr, height=7, font=('Courier New', 9),
                                    activestyle='none', bd=1, relief='solid')
        sb = ttk.Scrollbar(fr, orient='vertical', command=self.tex_list.yview)
        self.tex_list.config(yscrollcommand=sb.set)
        self.tex_list.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.tex_list.bind('<Double-Button-1>', self._tex_swap)
        hint = ttk.Label(p, text="Double click to replace texture for submesh",
                          foreground='#666')
        hint.pack(anchor='w', padx=6, pady=(0, 4))

    def _tex_refresh(self):
        if not hasattr(self, 'tex_list'): return
        self.tex_list.delete(0, 'end')
        for i, sub in enumerate(self.r.subs):
            name = sub.get('tex_name') or os.path.basename(
                sub.get('texture_path') or '') or '(none)'
            self.tex_list.insert('end', f"[{i:2d}]  {name}")

    def _tex_swap(self, e):
        s = self.tex_list.curselection()
        if not s: return
        i = s[0]
        r = self.r
        if not (0 <= i < len(r.subs)): return
        p = filedialog.askopenfilename(
            title=f"Select texture for submesh [{i}]",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tga"),
                       ("All files", "*.*")])
        if not p: return
        old_path = r.subs[i].get("texture_path")
        r.subs[i]["texture_path"] = p
        r.subs[i]["tex_name"] = os.path.basename(p)
        if old_path and old_path != p and not isinstance(old_path, tuple):
            still_used = any(s.get("texture_path") == old_path for s in r.subs)
            if not still_used:
                tex = r.tcache.pop(old_path, None)
                if tex is not None and tex is not r.solid:
                    try: tex.release()
                    except Exception: pass
        self._tex_refresh()
        self._dirty = True

    def _bottom(self):
        f = tk.Frame(self.root, bg='#ececec'); f.pack(side='bottom', fill='x', padx=6, pady=2)
        tk.Label(f, text="FOV:", bg='#ececec', font=self.fu, width=5,
                 anchor='e').grid(row=0, column=0, sticky='e')
        self.fov_var = tk.DoubleVar(value=65.0)
        tk.Scale(f, from_=20, to=120, orient='horizontal', variable=self.fov_var,
                 showvalue=False, length=460, sliderlength=14, resolution=1,
                 bg='#ececec', highlightthickness=0,
                 command=self._fov_ch).grid(row=0, column=1, sticky='ew', padx=4)
        self.fl = tk.Label(f, text="65", bg='#ececec', font=self.fu,
                           width=4, anchor='e')
        self.fl.grid(row=0, column=2)
        tk.Label(f, text="Frame:", bg='#ececec', font=self.fu, width=5,
                 anchor='e').grid(row=1, column=0, sticky='e')
        self.frm_var = tk.DoubleVar(value=0.0)
        tk.Scale(f, from_=0, to=1, orient='horizontal', variable=self.frm_var,
                 showvalue=False, length=460, sliderlength=14, resolution=0.001,
                 bg='#ececec', highlightthickness=0,
                 command=self._frm_ch).grid(row=1, column=1, sticky='ew', padx=4)
        self.frl = tk.Label(f, text="0.0 / 0.0", bg='#ececec', font=self.fu,
                            width=10, anchor='e')
        self.frl.grid(row=1, column=2)
        f.columnconfigure(1, weight=1)

    def _keys(self):
        b = self.root.bind
        b('<Control-o>', lambda e: self.open()); b('<Control-O>', lambda e: self.open())
        b('<Control-l>', lambda e: self.open_model_browser())
        b('<Control-L>', lambda e: self.open_model_browser())
        b('<Control-t>', lambda e: self.apply_texture_dialog())
        b('<Control-T>', lambda e: self.apply_texture_dialog())
        b('<Control-i>', lambda e: self.open_image_dialog())
        b('<Control-I>', lambda e: self.open_image_dialog())
        b('<Control-k>', lambda e: self.open_sky_dialog())
        b('<Control-K>', lambda e: self.open_sky_dialog())
        b('<KeyPress-r>', lambda e: self.reset()); b('<KeyPress-R>', lambda e: self.reset())
        b('<KeyPress-b>', lambda e: self._tog("bones")); b('<KeyPress-B>', lambda e: self._tog("bones"))
        b('<KeyPress-w>', lambda e: self._tog("wireframe")); b('<KeyPress-W>', lambda e: self._tog("wireframe"))
        b('<KeyPress-g>', lambda e: self._tog("ground")); b('<KeyPress-G>', lambda e: self._tog("ground"))
        b('<KeyPress-k>', lambda e: self._tog("sky")); b('<KeyPress-K>', lambda e: self._tog("sky"))
        b('<KeyPress-z>', lambda e: self._rot_model()); b('<KeyPress-Z>', lambda e: self._rot_model())
        b('<Tab>', lambda e: self._next_anim()); b('<space>', lambda e: self._play_tog())
        for k, y, p in [("1", 0, 0), ("2", 180, 0), ("3", -90, 0),
                        ("4", 90, 0), ("5", 0, 89), ("6", 0, -89)]:
            b(f'<KeyPress-{k}>', lambda e, y=y, p=p: self._view(y, p))
        b('<Left>', lambda e: self._rot(-8, 0)); b('<Right>', lambda e: self._rot(8, 0))
        b('<Up>', lambda e: self._rot(0, 8)); b('<Down>', lambda e: self._rot(0, -8))

    def _down(self, e, m): self._drag = m; self._dpos = (e.x, e.y)
    def _up(self): self._drag = None

    def _move(self, e):
        if not self._drag: return
        dx, dy = e.x - self._dpos[0], e.y - self._dpos[1]
        self._dpos = (e.x, e.y); r = self.r
        if self._drag == 'rot':
            r.yaw -= dx * 0.01; r.pitch += dy * 0.01
            lim = math.pi * 0.499; r.pitch = max(-lim, min(lim, r.pitch))
        else:
            ey = r.eye(); fw = glm.normalize(r.tgt - ey)
            rt = glm.normalize(glm.cross(fw, glm.vec3(0, 1, 0))); up = glm.cross(rt, fw)
            s = r.dist * 0.003
            r.tgt += rt * (-dx * s) + up * (dy * s)
        self._dirty = True

    def _wheel(self, e):
        d = e.delta / 120 if abs(e.delta) >= 120 else e.delta
        self.r.dist *= 0.9 if d > 0 else 1.1
        self.r.dist = max(0.05, self.r.dist)
        self._dirty = True

    def _rot(self, y, p):
        r = self.r; r.yaw += math.radians(y); r.pitch += math.radians(p)
        lim = math.pi * 0.499; r.pitch = max(-lim, min(lim, r.pitch))
        self._dirty = True

    def _view(self, y, p):
        self.r.yaw = math.radians(y); self.r.pitch = math.radians(p)
        self._dirty = True

    def _rot_model(self):
        self.r.rot_x = 0.0 if self.r.rot_x else -90.0
        self._dirty = True

    def reset(self):
        r = self.r; r.tgt = glm.vec3(*r.center)
        sz = max(1e-4, r.size) * 0.75
        r.dist = sz / max(0.05, math.tan(math.radians(r.fov * 0.5))) * 1.35
        self._dirty = True

    def _chk(self, a):
        self.r.opt[a] = bool(self.cv_vars[a].get())
        self._dirty = True
        if a == "sky":
            try: self._info()
            except Exception: pass

    def _tog(self, a):
        if a not in self.cv_vars: return
        c = self.cv_vars[a].get(); self.cv_vars[a].set(0 if c else 1)
        self.r.opt[a] = not bool(c)
        self._dirty = True
        if a == "sky":
            try: self._info()
            except Exception: pass

    def _fov_ch(self, v):
        try:
            self.r.fov = float(v); self.fl.config(text=f"{int(float(v))}")
            self._dirty = True
        except Exception: pass

    def _frm_ch(self, v):
        try:
            r = self.r
            if r.cur and r.cur in r.anims:
                t = r.anims[r.cur]["duration"]
                r.atime = float(v) * t; r.playing = False
                self.frl.config(text=f"{r.atime:.2f} / {t:.2f}")
                self._dirty = True
        except Exception: pass

    def _next_anim(self):
        r = self.r
        if not r.anames: return
        try: i = r.anames.index(r.cur)
        except Exception: i = -1
        r.cur = r.anames[(i + 1) % len(r.anames)]; r.atime = 0.0; r.playing = True
        self._seq_refresh(); self._dirty = True

    def _play_tog(self):
        self.r.playing = not self.r.playing
        self._dirty = True

    def _seq_sel(self, e):
        s = self.seq.curselection()
        if not s: return
        i = s[0]
        if 0 <= i < len(self.r.anames):
            self.r.cur = self.r.anames[i]; self.r.atime = 0.0; self.r.playing = True
            self._dirty = True

    def _seq_refresh(self):
        self.seq.delete(0, 'end')
        for i, n in enumerate(self.r.anames):
            self.seq.insert('end', n)
            if n == self.r.cur: self.seq.selection_set(i)

    def open(self):
        p = filedialog.askopenfilename(
            title="Open Model",
            filetypes=[("Model files",
                        "*.obj *.glb *.gltf *.mdl *.smd *.fbx *.dae *.3ds "
                        "*.x *.stl *.ply *.off *.3mf"),
                       ("Source models", "*.mdl"),
                       ("OBJ models", "*.obj"),
                       ("glTF models", "*.glb *.gltf"),
                       ("SMD/FBX/DAE/3DS models",
                        "*.smd *.fbx *.dae *.3ds *.x"),
                       ("Mesh exchange (STL/PLY/OFF/3MF)",
                        "*.stl *.ply *.off *.3mf"),
                       ("All files", "*.*")])
        if p: self.load(p)

    def open_image_dialog(self):
        ps = filedialog.askopenfilenames(
            title="Open Image(s)",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tga *.webp *.gif"),
                       ("All files", "*.*")])
        for p in ps: self._open_image_viewer(p)

    def open_sky_dialog(self):
        p = filedialog.askopenfilename(
            title="Open Sky",
            filetypes=[("Sky images (EXR panoramas and images)",
                        "*.exr *.png *.jpg *.jpeg *.bmp *.tga *.webp *.gif"),
                       ("OpenEXR HDR sky", "*.exr"),
                       ("Images", "*.png *.jpg *.jpeg *.bmp *.tga *.webp *.gif"),
                       ("All files", "*.*")])
        if p: self.load_sky(p)

    def load_sky(self, p):
        ok, err = self.r.load_sky(p)
        if not ok:
            messagebox.showerror("Open Sky", f"Cannot load sky:\n{p}\n\n{err}")
            return False
        var = self.cv_vars.get("sky")
        if var is not None:
            var.set(1)
        self._dirty = True
        try: self._info()
        except Exception: pass
        return True

    def apply_texture_dialog(self):
        ps = filedialog.askopenfilenames(
            title="Apply Texture(s)",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tga *.webp"),
                       ("All files", "*.*")])
        if ps: self._apply_textures(list(ps))

    def load(self, p, quiet=False):
        try:
            ok = self.r.load(p)
        except Exception as e:
            print(f"[load] {e}"); traceback.print_exc()
            if not quiet: messagebox.showerror("Error", f"{e}")
            return False
        if not ok:
            if not quiet: messagebox.showerror("Error", f"Failed: {p}")
            return False
        self.path = p
        self._seq_refresh(); self._info(); self._binfo()
        try: self._tex_refresh()
        except Exception: pass
        self._dirty = True
        return True

    def refresh(self):
        if self.path: self.load(self.path)

    def _apply_textures(self, paths):
        r = self.r
        if not r.subs:
            messagebox.showinfo("No model", "Load a model first.")
            return
        if not paths: return
        for i, sub in enumerate(r.subs):
            sub["texture_path"] = paths[i % len(paths)]
            sub["tex_name"] = os.path.basename(paths[i % len(paths)])
        r._flush_textures()
        try: self._tex_refresh()
        except Exception: pass
        self._dirty = True

    def _sky_line(self):
        r = self.r
        if r.sky_tex is not None:
            w, h = r.sky_size
            state = "on" if r.opt.get("sky") else "off"
            return (f"Sky:        {os.path.basename(r.sky_path or '')} "
                    f"{w}x{h} {'HDR' if r.sky_hdr else 'LDR'} ({state})")
        return "Sky:        (none — File > Open Sky)"

    def _info(self):
        r = self.r; self.mtx.config(state='normal'); self.mtx.delete('1.0', 'end')
        if self.path:
            L = [f"File:       {self.path}", "",
                 f"Vertices:   {r.stats['v']}", f"Triangles:  {r.stats['t']}",
                 f"Submeshes:  {len(r.subs)}", f"Bones:      {r.stats['b']}",
                 f"Animations: {r.stats['a']}",
                 f"Model rot:  {r.rot_x:+.0f}°"]
        else:
            L = ["No model loaded.", "Use File > Open Model (Ctrl+O)."]
        L.append(self._sky_line())
        self.mtx.insert('1.0', "\n".join(L)); self.mtx.config(state='disabled')

    def _binfo(self):
        r = self.r; self.btx.config(state='normal'); self.btx.delete('1.0', 'end')
        if r.skel:
            bs = r.skel["bones"]
            self.btx.insert('end', f"Total bones: {len(bs)}\n\n")
            for b in bs[:50]:
                self.btx.insert('end',
                    f"[{b['index']:3d}]  {b['name']:<28s}  parent={b['parent']}\n")
            if len(bs) > 50: self.btx.insert('end', f"... {len(bs) - 50} more\n")
        else:
            self.btx.insert('end', "(no skeleton)")
        self.btx.config(state='disabled')

    def _models_root(self):
        base = os.path.dirname(os.path.abspath(__file__))
        for d in (os.path.join(os.path.dirname(base), "models"),
                  os.path.join(base, "models")):
            if os.path.isdir(d): return d
        return None

    def _render_static_thumb(self, fp, target_w):
        if not hasattr(self, "_thumb_cache"):
            self._thumb_cache = {}
        key = (os.path.abspath(fp), os.path.getmtime(fp), target_w)
        if key in self._thumb_cache:
            return self._thumb_cache[key]
            
        r = self.r
        try:
            if not r.load(fp): return None
        except Exception:
            return None
        try:
            r.yaw = 0.0; r.pitch = 0.0
            r.tgt = glm.vec3(*r.center)
            sz = max(1e-4, r.size) * 0.75
            r.dist = sz / max(0.05, math.tan(math.radians(r.fov * 0.5))) * 1.35
            r.playing = False; r.atime = 0.0
            if r.skel:
                r.bm = bone_mats(r.skel, {})
                r.bw = _worlds(r.skel, {})
            b64 = r.render()
            png = base64.b64decode(b64)
            surf = pygame.image.load(io.BytesIO(png))
            w, h = surf.get_size()
            size = min(w, h)
            crop_rect = pygame.Rect((w - size) // 2, (h - size) // 2, size, size)
            surf = surf.subsurface(crop_rect)
            surf2 = pygame.transform.smoothscale(surf, (target_w, target_w))
            rgb = pygame.image.tobytes(surf2, "RGB")
            ppm = f"P6\n{target_w} {target_w}\n255\n".encode() + rgb
            try:
                photo = tk.PhotoImage(data=base64.b64encode(ppm).decode())
            except Exception:
                photo = tk.PhotoImage(data=b64)
                f = max(1, photo.width() // target_w)
                if f > 1: photo = photo.subsample(f, f)
            
            self._thumb_cache[key] = photo
            return photo
        except Exception:
            return None

    def open_model_browser(self):
        root_dir = self._models_root()
        if not root_dir:
            messagebox.showinfo("Load Model", "models directory not found.")
            return
        files = []
        for dp, _, fs in os.walk(root_dir):
            for f in fs:
                ext = os.path.splitext(f)[1].lower()
                if ext in MODEL_EXTS and ext != ".smd":
                    files.append(os.path.join(dp, f))
        files.sort()
        if not files:
            messagebox.showinfo("Load Model", "No model files found.")
            return

        if getattr(self, "_browser_win", None):
            try:
                if self._browser_win.winfo_exists():
                    self._browser_win.destroy()
            except Exception: pass
            self._browser_win = None

        win = tk.Toplevel(self.root)
        self._browser_win = win
        win.title("Open")
        S = 640
        win.geometry(f"{S}x{S}")
        win.resizable(False, False)
        win.configure(bg="#F0F0F0")
        win.minsize(S, S)
        win.maxsize(S, S)
        win.update_idletasks()
        win.geometry(f"{S}x{S}")

        state = {"orig_path": self.path, "cancelled": False, "finished": False}
        self._preview_active = True

        top = tk.Frame(win, bg="#F0F0F0")
        top.pack(side="top", fill="x", padx=8, pady=8)
        tk.Label(top, text="Look in:", bg="#F0F0F0", font=("Tahoma", 8)).pack(side="left")
        path_var = tk.StringVar(value=root_dir)
        path_entry = tk.Entry(top, textvariable=path_var, font=("Tahoma", 8), state="readonly")
        path_entry.pack(side="left", fill="x", expand=True, padx=6)
        up_btn = tk.Button(top, text="Up", font=("Tahoma", 8), width=5)
        up_btn.pack(side="left")

        bot = tk.Frame(win, bg="#F0F0F0")
        bot.pack(side="bottom", fill="x", padx=8, pady=8)
        tk.Label(bot, text="File name:", bg="#F0F0F0", font=("Tahoma", 8)).pack(side="left")
        fname_var = tk.StringVar()
        fname_entry = tk.Entry(bot, textvariable=fname_var, font=("Tahoma", 8))
        fname_entry.pack(side="left", fill="x", expand=True, padx=6)

        def _restore_orig():
            if state["orig_path"]:
                try: self.r.load(state["orig_path"])
                except Exception: pass
            else:
                try:
                    self.r._release_all_subs()
                    self.r.subs = []
                except Exception: pass
            self.r.playing = True
            self._preview_active = False
            self._dirty = True

        def _close():
            if state["cancelled"]: return
            state["cancelled"] = True
            if not state["finished"]:
                _restore_orig()
            else:
                self._preview_active = False
                self._dirty = True
            try: win.destroy()
            except Exception: pass
            self._browser_win = None

        def _open_selected():
            p = fname_var.get()
            if p and os.path.exists(p):
                self.load(p)
                _close()

        open_btn = tk.Button(bot, text="Open", width=8, font=("Tahoma", 8), command=_open_selected)
        open_btn.pack(side="right", padx=(6, 0))
        cancel_btn = tk.Button(bot, text="Cancel", width=8, font=("Tahoma", 8), command=_close)
        cancel_btn.pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", _close)

        mid_frame = tk.Frame(win, bg="#D0D0D0", bd=1, relief="sunken")
        mid_frame.pack(side="top", fill="both", expand=True, padx=8)
        
        # 浅蓝色背景
        cv = tk.Canvas(mid_frame, bg="#E6F0FA", highlightthickness=0)
        sb = ttk.Scrollbar(mid_frame, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(cv, bg="#E6F0FA")
        inner_id = cv.create_window((0, 0), window=inner, anchor="nw")

        def _sync(_=None):
            try:
                cv.configure(scrollregion=cv.bbox("all"))
                cv.itemconfigure(inner_id, width=cv.winfo_width())
            except Exception: pass
        inner.bind("<Configure>", _sync)
        cv.bind("<Configure>", _sync)
        def _on_wheel(e):
            try: cv.yview_scroll(-1 if e.delta > 0 else 1, "units")
            except Exception: pass
        cv.bind("<MouseWheel>", _on_wheel)
        inner.bind("<MouseWheel>", _on_wheel)

        COLS = 5
        THUMB_SIZE = 80
        cells = []

        for idx, fp in enumerate(files):
            row, col = divmod(idx, COLS)
            cell = tk.Frame(inner, bg="#E6F0FA", bd=1, relief="solid", width=110, height=110, cursor="hand2")
            cell.grid(row=row, column=col, padx=4, pady=4)
            cell.grid_propagate(False)
            
            img_lbl = tk.Label(cell, bg="#E6F0FA", text="", cursor="hand2")
            img_lbl.place(relx=0.5, y=5, anchor="n", width=80, height=80)
            
            name = os.path.basename(fp)
            disp_name = name if len(name) <= 15 else name[:13] + "..."
            name_lbl = tk.Label(cell, bg="#E6F0FA", text=disp_name, font=("Tahoma", 7), anchor="center", cursor="hand2")
            name_lbl.place(relx=0.5, y=98, anchor="center")

            def _click(_e, c=cell, l1=img_lbl, l2=name_lbl, p=fp):
                fname_var.set(p)
                for c2, _, _ in cells:
                    c2.config(bg="#E6F0FA")
                    for child in c2.winfo_children():
                        child.config(bg="#E6F0FA")
                c.config(bg="#B3D4F0")
                l1.config(bg="#B3D4F0")
                l2.config(bg="#B3D4F0")

            def _dbl_click(_e, p=fp):
                self.load(p)
                _close()

            for w in (cell, img_lbl, name_lbl):
                w.bind("<Button-1>", _click)
                w.bind("<Double-Button-1>", _dbl_click)

            cells.append((cell, img_lbl, fp))

        queue = list(cells)
        def process_queue():
            if state["cancelled"] or not win.winfo_exists() or not queue:
                if not queue and not state["finished"]:
                    state["finished"] = True
                    _restore_orig()
                return
            cell, img_lbl, fp = queue.pop(0)
            
            ext = os.path.splitext(fp)[1].upper().replace(".", "")
            img_lbl.config(text=ext, font=("Tahoma", 12, "bold"), fg="#000000")
            
            try: win.update_idletasks()
            except Exception: pass

            photo = self._render_static_thumb(fp, THUMB_SIZE)
            if photo and not state["cancelled"]:
                img_lbl.config(image=photo, text="")
                img_lbl.image = photo

            win.after(10, process_queue)

        win.after(100, process_queue)

    def _loop(self):
        if self._closing: return
        if getattr(self, "_preview_active", False):
            self.root.after(self.FRAME_MS, self._loop)
            return
        try:
            now = pygame.time.get_ticks() / 1000.0
            dt = 0.0 if self._tick is None else min(0.1, now - self._tick)
            self._tick = now

            r = self.r
            need = self._dirty
            if r.playing and r.cur and r.cur in r.anims:
                need = True

            if need:
                if r.playing and r.cur and r.cur in r.anims:
                    r.update(dt)
                b64 = r.render()
                ph = tk.PhotoImage(data=b64)
                if self._img is None:
                    self._img = self.cv.create_image(0, 0, anchor='nw', image=ph)
                else:
                    self.cv.itemconfig(self._img, image=ph)
                self._photo = ph
                self._dirty = False

            if r.cur and r.cur in r.anims:
                t = r.anims[r.cur]["duration"]
                if t > 1e-6: self.frm_var.set(r.atime / t)
                self.frl.config(text=f"{r.atime:.2f} / {t:.2f}")
        except Exception as e:
            print(f"[loop] {e}"); traceback.print_exc()
        if not self._closing:
            self.root.after(self.FRAME_MS, self._loop)

    def run(self): self.root.mainloop()


def main():
    try:
        App().run()
    except Exception as e:
        print(f"[fatal] {e}"); traceback.print_exc()
        try: messagebox.showerror("Fatal", f"{e}\n\n{traceback.format_exc()}")
        except Exception: pass


if __name__ == "__main__":
    main()