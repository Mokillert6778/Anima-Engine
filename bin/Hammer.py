import sys
import os
import math
import time
import copy
import json
import numpy as np
import moderngl
import glm
import pygame

pygame.init()
pygame.display.set_mode((1, 1), pygame.HIDDEN)

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QGridLayout,
                             QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QPushButton, QStatusBar, QFileDialog, QMessageBox,
                             QInputDialog, QListWidget, QListWidgetItem, QMenu,
                             QGroupBox, QTabWidget, QSpinBox, QDialog, QListView,
                             QLineEdit, QDoubleSpinBox, QScrollArea, QSizePolicy,
                             QCheckBox, QRadioButton, QButtonGroup, QToolBar,
                             QAction, QShortcut, QSplitter, QPlainTextEdit,
                             QStyle, QFrame, QToolButton, QAbstractItemView,
                             QTableWidget, QTableWidgetItem, QHeaderView)
from PyQt5.QtGui import (QPixmap, QIcon, QCursor, QKeySequence, QPainter,
                         QColor, QPen, QBrush, QPolygon, QFont)
from PyQt5.QtCore import Qt, QPoint, QSize, QTimer, pyqtSignal
from PyQt5.QtOpenGL import QGLWidget
from PyQt5.QtGui import QSurfaceFormat

fmt = QSurfaceFormat()
fmt.setVersion(3, 3)
fmt.setProfile(QSurfaceFormat.CoreProfile)
fmt.setDepthBufferSize(24)
QSurfaceFormat.setDefaultFormat(fmt)

# ============================================================
# 导入 Brush (保留用于兼容旧地图)
# ============================================================
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from csg_brush import Brush
except ImportError:
    Brush = None

# ============================================================
# 目录定位
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def find_project_root(start_dir):
    cur = os.path.abspath(start_dir)
    for _ in range(8):
        if os.path.isdir(os.path.join(cur, "materials")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur: break
        cur = parent
    return start_dir

PROJECT_ROOT = find_project_root(BASE_DIR)
import sys as _sys
if PROJECT_ROOT not in _sys.path:
    _sys.path.insert(0, PROJECT_ROOT)

def get_materials_dir(): return os.path.join(PROJECT_ROOT, "materials")
def get_map_dir(): return os.path.join(PROJECT_ROOT, "map")


def get_sky_dir():
    # Sky configs live in skybox/ next to the cubemap folders; some older
    # builds kept them in sky/.
    d = os.path.join(PROJECT_ROOT, "sky")
    if os.path.isdir(d):
        return d
    return os.path.join(PROJECT_ROOT, "skybox")

SETTINGS_FILE = os.path.join(PROJECT_ROOT, "hammer_settings.json")

def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except: return {}

def save_settings(data):
    """Merge *data* into the settings file instead of replacing it."""
    try:
        cur = load_settings()
        if not isinstance(cur, dict):
            cur = {}
        if data:
            cur.update(data)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(_jsonable(cur), f, indent=2)
    except Exception:
        pass


def _jsonable(obj):
    """Recursively turn numpy scalars/arrays into plain JSON values.

    Block/entity data is often numpy (float32 positions from drag maths, glm
    matrices), and ``json.dump`` refuses those, which is what produced the
    "Object of type float32 is not JSON serializable" save error.
    """
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # glm types and anything else: fall back to a plain float list
    try:
        if isinstance(obj, glm.mat4):
            return [float(obj[i][j]) for j in range(4) for i in range(4)]
        if isinstance(obj, (glm.vec2, glm.vec3, glm.vec4)):
            return [float(obj[i]) for i in range(len(obj))]
    except Exception:
        pass
    return str(obj)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp")
GRID_SIZES = [1, 2, 4, 8, 16, 32, 64, 128]
MOVE_KEYS = frozenset((Qt.Key_W, Qt.Key_A, Qt.Key_S, Qt.Key_D,
                       Qt.Key_Space, Qt.Key_Shift))

# ============================================================
# ★ 水纹理判定
# ============================================================
WATER_KEYWORDS = ("water", "ocean", "river", "lake", "sea", "liquid")
WATER_EXCLUDE = ("waterfall_foam", "water_drip", "underwater_caustics")

def is_water_texture(tex_name):
    """纹理名是否代表水。"""
    if not tex_name:
        return False
    n = tex_name.lower()
    for ex in WATER_EXCLUDE:
        if ex in n:
            return False
    return any(kw in n for kw in WATER_KEYWORDS)

# ============================================================
# 实体加载
# ============================================================
# The editor may be launched by a wrapper that does not put bin/ on sys.path;
# entity_loader and its sibling modules live next to this file.
_BIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

try:
    from entity_loader import (
        load_all_entities, find_addons_root, entity_to_map_line,
    )
    _ADDONS_ROOT = find_addons_root(BASE_DIR)
    if _ADDONS_ROOT is None:
        _ADDONS_ROOT = os.path.join(os.path.dirname(BASE_DIR), "addons")
    ENTITY_DEFS = load_all_entities(_ADDONS_ROOT)
    print(f"[hammer] loaded {len(ENTITY_DEFS)} entities from {_ADDONS_ROOT}")
except Exception as _e:
    print(f"[hammer] entity_loader not available: {_e}")
    ENTITY_DEFS = {}
    def entity_to_map_line(etype, pos, params):
        x, y, z = pos
        return f"entity {etype} {x:.4f} {y:.4f} {z:.4f}"

def scan_materials():
    mat_dir = get_materials_dir()
    results = []
    if not os.path.isdir(mat_dir): return results
    for root, dirs, files in os.walk(mat_dir):
        rel_root = os.path.relpath(root, mat_dir)
        for f in sorted(files):
            if f.lower().endswith(IMAGE_EXTS):
                rel_path = f if rel_root == "." else os.path.join(rel_root, f)
                rel_path = rel_path.replace("\\", "/")
                results.append(rel_path)
    return results

def material_full_path(name): return os.path.join(get_materials_dir(), name)

# ============================================================
# OBJ 加载器
# ============================================================
def load_obj_simple(filepath):
    positions = []; normals = []; triangles = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                parts = line.split()
                if not parts: continue
                tag = parts[0]
                if tag == "v" and len(parts) >= 4:
                    bx, by, bz = float(parts[1]), float(parts[2]), float(parts[3])
                    positions.append((bx, bz, -by))
                elif tag == "vn" and len(parts) >= 4:
                    bx, by, bz = float(parts[1]), float(parts[2]), float(parts[3])
                    normals.append((bx, bz, -by))
                elif tag == "f" and len(parts) >= 4:
                    verts = []
                    for p in parts[1:]:
                        subs = p.split("/")
                        vi = int(subs[0]) - 1
                        ni = int(subs[2]) - 1 if len(subs) >= 3 and subs[2] else -1
                        verts.append((vi, ni))
                    for i in range(1, len(verts) - 1):
                        triangles.append([verts[0], verts[i], verts[i + 1]])
    except Exception as e:
        print(f"[obj] parse fail {filepath}: {e}")
        return None
    if not positions or not triangles: return None
    out = []
    for tri in triangles:
        p0 = positions[tri[0][0]]; p1 = positions[tri[1][0]]; p2 = positions[tri[2][0]]
        e1 = (p1[0]-p0[0], p1[1]-p0[1], p1[2]-p0[2])
        e2 = (p2[0]-p0[0], p2[1]-p0[1], p2[2]-p0[2])
        cx = e1[1]*e2[2] - e1[2]*e2[1]
        cy = e1[2]*e2[0] - e1[0]*e2[2]
        cz = e1[0]*e2[1] - e1[1]*e2[0]
        cl = (cx*cx + cy*cy + cz*cz) ** 0.5
        fn = (cx/cl, cy/cl, cz/cl) if cl > 1e-9 else (0, 1, 0)
        for (vi, ni) in tri:
            p = positions[vi]
            n = normals[ni] if (0 <= ni < len(normals)) else fn
            out.extend([p[0], p[1], p[2], n[0], n[1], n[2]])
    return np.array(out, dtype=np.float32)

# ============================================================
# 天空配置
# ============================================================
def parse_sky_cfg(sky_name):
    cfg = {"horizon": (0.5, 0.7, 0.9), "zenith": (0.1, 0.3, 0.6),
           "skybox": "", "skybox_flip_h": 1, "skybox_flip_v": 1, "skybox_rotate_up": 0}
    if not sky_name:
        return cfg
    path = os.path.join(get_sky_dir(), sky_name)
    if not os.path.exists(path):
        return cfg
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                key, _, val = line.partition("=")
                key = key.strip(); val = val.strip()
                if key == "skybox": cfg["skybox"] = val
                elif key == "horizon":
                    p = [float(x) for x in val.split(",")]
                    cfg["horizon"] = tuple(p[:3])
                elif key == "zenith":
                    p = [float(x) for x in val.split(",")]
                    cfg["zenith"] = tuple(p[:3])
                elif key == "skybox_flip_h": cfg["skybox_flip_h"] = int(float(val))
                elif key == "skybox_flip_v": cfg["skybox_flip_v"] = int(float(val))
                elif key == "skybox_rotate_up": cfg["skybox_rotate_up"] = int(float(val))
    except Exception:
        pass
    return cfg

SKYBOX_FACES = ["right.png", "left.png", "up.png", "down.png", "front.png", "back.png"]
SKYBOX_FACES_ALT = [
    ["right.png", "left.png", "up.png", "down.png", "front.png", "back.png"],
    ["px.png", "nx.png", "py.png", "ny.png", "pz.png", "nz.png"],
    ["posx.png", "negx.png", "posy.png", "negy.png", "posz.png", "negz.png"],
    ["+x.png", "-x.png", "+y.png", "-y.png", "+z.png", "-z.png"],
    ["cubemap_px.png", "cubemap_nx.png", "cubemap_py.png",
     "cubemap_ny.png", "cubemap_pz.png", "cubemap_nz.png"],
]

def _find_skybox_folder(folder_name):
    candidates = [
        os.path.join(get_sky_dir(), "skybox", folder_name),
        os.path.join(get_sky_dir(), folder_name),
        os.path.join(PROJECT_ROOT, "skybox", folder_name),
        os.path.join(get_materials_dir(), "skybox", folder_name),
    ]
    for c in candidates:
        if os.path.isdir(c):
            for face_set in SKYBOX_FACES_ALT:
                if all(os.path.exists(os.path.join(c, f)) for f in face_set):
                    return c, face_set
    return None, None

def load_skybox_cubemap(ctx, folder_name, flip_h=1, flip_v=1, rotate_up=0):
    if not folder_name:
        return None
    folder, face_set = _find_skybox_folder(folder_name)
    if folder is None:
        return None
    face_data = []; size = None
    for fname in face_set:
        p = os.path.join(folder, fname)
        if not os.path.exists(p):
            found = None
            try:
                for alt in os.listdir(folder):
                    if alt.lower() == fname.lower():
                        found = os.path.join(folder, alt)
                        break
            except Exception:
                pass
            if found is None:
                return None
            p = found
        try:
            surf = pygame.image.load(p)
        except Exception:
            return None
        apply_flip_v = 0 if fname.lower().startswith(("up", "py", "posy", "+y")) else flip_v
        if flip_h or apply_flip_v:
            surf = pygame.transform.flip(surf, bool(flip_h), bool(apply_flip_v))
        if fname.lower().startswith(("up", "py", "posy", "+y")) and rotate_up:
            surf = pygame.transform.rotate(surf, rotate_up)
            w, h = surf.get_size(); s = min(w, h)
            surf = surf.subsurface((w//2 - s//2, h//2 - s//2, s, s)).copy()
        if size is None:
            size = surf.get_size()
        elif surf.get_size() != size:
            return None
        if surf.get_bitsize() not in (24, 32):
            surf = surf.convert(24)
        face_data.append(pygame.image.tobytes(surf, "RGB", True))
    try:
        tex = ctx.texture_cube(size, 3)
        for i, d in enumerate(face_data):
            tex.write(i, d)
        tex.build_mipmaps()
        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        try:
            tex.seamless_cube_map = True
        except Exception:
            pass
        return tex
    except Exception:
        return None

# ============================================================
# 多语言
# ============================================================
LANGUAGES = {
    "zh": {
        "title": "锤子编辑器", "file": "文件", "new_map": "新建地图", "open_map": "打开地图...",
        "save_map": "保存地图", "save_as": "另存为...", "close_map": "关闭地图", "exit": "退出",
        "map": "地图", "set_sky": "设置天空...", "compile_map": "编译地图",
        "select": "选择:", "pointer": "指针", "groups": "组", "objects": "实体", "solids": "固体",
        "cut": "切割",
        "current_texture": "当前纹理:", "browse": "浏览...", "replace": "替换...",
        "visgroups": "可视组:", "show": "显示", "edit": "编辑", "mark": "标记",
        "move_selected": "移动选中:", "to_world": "转到世界", "to_entity": "转到实体",
        "categories": "类别:", "objects_cat": "对象:", "create_prefab": "创建预制体",
        "sky_spawn": "天空与出生点:", "set": "设置", "map_data": "地图数据:",
        "blocks": "方块:", "entities": "实体:", "delete_block": "删除方块",
        "delete_entity": "删除实体", "texture_browser": "纹理浏览器", "filter": "过滤:",
        "path": "路径:", "create_entity": "创建实体 (应用纹理)", "edit_size": "编辑尺寸...",
        "edit_block_size": "编辑方块尺寸 #{}", "length_x": "长度 (X 半宽):",
        "height_y": "高度 (Y 半高):", "width_z": "宽度 (Z 半宽):",
        "ok": "确定", "cancel": "取消", "msg_empty_map": "空地图，没东西可编译。",
        "msg_compile_title": "编译地图", "msg_compile_done": "编译完成!\n方块数量: {}\n实体数量: {}\n输出路径: {}",
        "ready_msg": "2D 视图：左键拖空白=平移，左键拖物体=移动，Shift+点击=多选。"
                     "3D 视图：右键拖=转视角，WASD=自由移动，滚轮=前进/后退。"
                     "Ctrl+Z 撤销 / Ctrl+Y 重做 · Ctrl+C/V 复制粘贴 · Delete 删除 · "
                     "F 适配视图 · [ ] 网格大小",
        "selected_tex": "已选择纹理: {}", "edit_entity": "编辑实体 #{}", "entity_type": "实体类型:",
        "pos_x": "X 坐标:", "pos_y": "Y 坐标:", "pos_z": "Z 坐标:",
        "delete_entity_menu": "删除实体", "edit_entity_menu": "编辑实体...",
        "cut_menu": "切割...", "cut_title": "切割方块 / 实体",
        "cut_corner_label": "勾选要切掉的角（可多选）：",
        "cut_amount_label": "切掉大小 (0.05 - 0.50):",
        "remove_cut": "清除全部切角",
        "save_prompt_title": "未保存的更改",
        "save_prompt_text": "地图已被修改，是否保存更改？",
        "save_btn_yes": "是", "save_btn_no": "否",
    },
    "en": {
        "title": "Hammer Editor", "file": "File", "new_map": "New Map", "open_map": "Open Map...",
        "save_map": "Save Map", "save_as": "Save As...", "close_map": "Close Map", "exit": "Exit",
        "map": "Map", "set_sky": "Set Sky...", "compile_map": "Compile Map",
        "select": "Select:", "pointer": "Pointer", "groups": "Groups", "objects": "Objects", "solids": "Solids",
        "cut": "Cut",
        "current_texture": "Current texture:", "browse": "Browse...", "replace": "Replace...",
        "visgroups": "VisGroups:", "show": "Show", "edit": "Edit", "mark": "Mark",
        "move_selected": "Move selected:", "to_world": "toWorld", "to_entity": "toEntity",
        "categories": "Categories:", "objects_cat": "Objects:", "create_prefab": "Create Prefab",
        "sky_spawn": "Sky & Spawn:", "set": "Set", "map_data": "Map Data:",
        "blocks": "Blocks:", "entities": "Entities:", "delete_block": "Delete block",
        "delete_entity": "Delete entity", "texture_browser": "Textures", "filter": "Filter:",
        "path": "Path:", "create_entity": "Create Entity (Apply Texture)", "edit_size": "Edit Size...",
        "edit_block_size": "Edit Block Size #{}", "length_x": "Length (X Half):",
        "height_y": "Height (Y Half):", "width_z": "Width (Z Half):",
        "ok": "OK", "cancel": "Cancel", "msg_empty_map": "Nothing to compile, map is empty.",
        "msg_compile_title": "Compile Map", "msg_compile_done": "Compile finished!\nBlocks: {}\nEntities: {}\nOutput: {}",
        "ready_msg": "2D views: LMB drag empty=pan, LMB drag object=move, Shift+Click=multi-select. "
                     "3D view: RMB drag=look, WASD=fly, wheel=forward/back. "
                     "Ctrl+Z undo / Ctrl+Y redo · Ctrl+C/V copy/paste · Delete remove · "
                     "F fit view · [ ] grid size",
        "selected_tex": "Selected texture: {}", "edit_entity": "Edit Entity #{}", "entity_type": "Entity type:",
        "pos_x": "Position X:", "pos_y": "Position Y:", "pos_z": "Position Z:",
        "delete_entity_menu": "Delete Entity", "edit_entity_menu": "Edit Entity...",
        "cut_menu": "Cut...", "cut_title": "Cut Block / Entity",
        "cut_corner_label": "Check corners to cut (multi-select):",
        "cut_amount_label": "Cut size (0.05 - 0.50):",
        "remove_cut": "Clear all cuts",
        "save_prompt_title": "Unsaved changes",
        "save_prompt_text": "The map has been modified. Save changes?",
        "save_btn_yes": "Yes", "save_btn_no": "No",
    }
}

ENTITY_TYPES = sorted(ENTITY_DEFS.keys()) if ENTITY_DEFS else [
    "info_player_start", "light", "prop_static", "npc", "trigger"]

# ============================================================
# UI 字符串（英文 / 简体中文）
# ============================================================
STRINGS = {
    "en": {
        "menu_file": "File", "menu_edit": "Edit", "menu_map": "Map",
        "menu_view": "View", "menu_tools": "Tools", "menu_instancing": "Instancing",
        "menu_window": "Window", "menu_help": "Help",
        "new": "New", "open": "Open...", "save": "Save", "save_as": "Save As...",
        "close": "Close", "exit": "Exit",
        "undo": "Undo", "redo": "Redo", "cut": "Cut", "copy": "Copy",
        "paste": "Paste", "delete": "Delete", "duplicate": "Duplicate",
        "select_all": "Select All", "clear_selection": "Clear Selection",
        "properties": "Properties",
        "map_properties": "Map Properties...", "entity_report": "Entity Report...",
        "cinematic": "Cinematic Intro...",
        "cinematic_window": "Cinematic Intro",
        "cinematic_info": (
            "Camera keys play in time order. The engine blends position and "
            "look-at between keys, fades the main title in/out and shows each "
            "subtitle between its start and end time."),
        "cinematic_main_title": "Main title:",
        "cinematic_subtitle": "Subtitle:",
        "cinematic_duration": "Duration (s):",
        "cinematic_fov": "FOV (0 = default):",
        "cinematic_letterbox": "Letterbox bars",
        "cinematic_play": "Play on map start",
        "cinematic_camera_path": "Camera path (time, camera position, look-at):",
        "cinematic_add_key": "Add key",
        "cinematic_cam_key": "Add from 3D view",
        "cinematic_del_key": "Remove key",
        "cinematic_subtitles": "Subtitles (start, end, text):",
        "cinematic_add_sub": "Add subtitle",
        "cinematic_del_sub": "Remove subtitle",
        "cinematic_time": "Time", "cinematic_cam": "Cam",
        "cinematic_look": "Look", "cinematic_start": "Start",
        "cinematic_end": "End", "cinematic_text": "Text",
        "check_problems": "Check for Problems", "compile": "Compile",
        "run_map": "Run Map",
        "fit_view": "Fit View", "show_grid": "Show Grid",
        "snap_to_grid": "Snap to Grid", "grid_larger": "Grid Larger",
        "grid_smaller": "Grid Smaller", "textured": "3D Textured",
        "wireframe": "3D Wireframe",
        "apply_texture": "Apply Texture to Selection",
        "rotate90": "Rotate 90 Degrees", "toggle_water": "Toggle Water Brush",
        "water_sea": "Water Material: Sea", "water_river": "Water Material: River",
        "edit_entity": "Edit Entity...", "hollow": "Hollow...",
        "carve": "Carve Selection", "mirror": "Mirror Selection",
        "cut_doorway": "Cut Doorway...", "terrain": "Create Terrain...",
        "doorway_title": "Cut Doorway", "doorway_width": "Width:",
        "doorway_height": "Height:",
        "terrain_title": "Create Terrain", "terrain_size": "Size X:",
        "terrain_size_z": "Size Z:", "terrain_segments": "Segments:",
        "terrain_height": "Hill height:", "terrain_seed": "Seed:",
        "group": "Group", "ungroup": "Ungroup",
        "array": "Create Array...", "snap_selection": "Snap Selection to Grid",
        "load_prefab": "Load Prefab...", "settings": "Settings...",
        "enable_instancing": "Enable Instancing", "reset_layout": "Reset Layout",
        "messages": "Messages", "shortcuts": "Keyboard Shortcuts...",
        "about": "About...",
        "align": "Align", "align_x_min": "Min X", "align_x_center": "Center X",
        "align_x_max": "Max X", "align_y_min": "Min Y", "align_y_center": "Center Y",
        "align_y_max": "Max Y", "align_z_min": "Min Z", "align_z_center": "Center Z",
        "align_z_max": "Max Z",
        "select": "Select:", "groups": "Groups", "objects": "Objects",
        "solids": "Solids", "current_texture": "Current texture:",
        "browse": "Browse...", "replace": "Replace...", "visgroups": "VisGroups:",
        "user": "User", "auto": "Auto", "cordon": "Cordon",
        "show": "Show", "edit": "Edit", "mark": "Mark",
        "move_selected": "Move selected:", "to_world": "To World",
        "to_entity": "To Entity", "categories": "Categories:",
        "objects_label": "Objects:", "create_prefab": "Create Prefab",
        "all": "All", "vis_empty": "(empty)",
        "camera": "camera", "top": "top (x/y)", "front": "front (y/z)",
        "side": "side (x/z)",
        "for_help": "For Help, press F1", "no_selection": "no selection.",
        "snap": "Snap", "grid": "Grid", "on": "On", "off": "Off",
        "selected_suffix": "selected.", "solid_suffix": "solid(s)",
        "ent_suffix": "ent(s)",
        "language": "Language:", "theme": "Theme:", "dark": "Dark",
        "light": "Light", "settings_title": "Settings",
        "ok": "OK", "cancel": "Cancel", "grid_label": "Grid:",
        "map_props_title": "Map Properties", "sky_config": "Sky configuration:",
        "spawn_position": "Spawn position (world units):",
        "entity_report_title": "Entity Report", "report_solids": "Solids:",
        "report_entities": "Entities:", "delete_solid": "Delete Solid",
        "resize": "Resize...", "delete_entity": "Delete Entity",
        "props": "Properties...", "configurations": "Configurations:",
        "game_config_title": "Select a game configuration to use",
        "check_title": "Check for Problems", "no_problems": "No problems found.",
        "hollow_title": "Hollow", "wall_thickness": "Wall thickness:",
        "array_title": "Create Array", "count": "Count:", "offset_x": "Offset X:",
        "offset_y": "Offset Y:", "offset_z": "Offset Z:", "axis": "Axis:",
        "tip_select": "Selection tool", "tip_zoom": "Magnify / zoom",
        "tip_block": "Create a solid brush", "tip_entity": "Create an entity",
        "tip_cut": "Clip / cut tool", "tip_texture": "Apply the current texture",
        "tip_light": "Place light_environment", "tip_water": "Toggle water brush mode",
        "tip_player": "Place info_player_start", "tip_grid": "Grid size larger",
        "tip_snap": "Toggle snap to grid", "tip_camera": "Focus the 3D camera",
        "tip_hand": "Pan the 2D views",
    },
    "zh": {
        "menu_file": "文件", "menu_edit": "编辑", "menu_map": "地图",
        "menu_view": "视图", "menu_tools": "工具", "menu_instancing": "实例化",
        "menu_window": "窗口", "menu_help": "帮助",
        "new": "新建", "open": "打开...", "save": "保存", "save_as": "另存为...",
        "close": "关闭", "exit": "退出",
        "undo": "撤销", "redo": "重做", "cut": "剪切", "copy": "复制",
        "paste": "粘贴", "delete": "删除", "duplicate": "创建副本",
        "select_all": "全选", "clear_selection": "取消选择",
        "properties": "属性",
        "map_properties": "地图属性...", "entity_report": "实体报告...",
        "cinematic": "开场动画（镜头/标题/字幕）...",
        "cinematic_window": "开场动画",
        "cinematic_info": (
            "镜头关键帧按时间顺序播放。引擎在关键帧之间插值相机位置和"
            "注视点，主标题淡入淡出，字幕在各自的起止时间内显示。"),
        "cinematic_main_title": "主标题：",
        "cinematic_subtitle": "副标题：",
        "cinematic_duration": "时长（秒）：",
        "cinematic_fov": "视场角（0 = 默认）：",
        "cinematic_letterbox": "上下黑边",
        "cinematic_play": "地图开始时播放",
        "cinematic_camera_path": "相机路径（时间、相机位置、注视点）：",
        "cinematic_add_key": "添加关键帧",
        "cinematic_cam_key": "从 3D 视图添加",
        "cinematic_del_key": "删除关键帧",
        "cinematic_subtitles": "字幕（开始、结束、文本）：",
        "cinematic_add_sub": "添加字幕",
        "cinematic_del_sub": "删除字幕",
        "cinematic_time": "时间", "cinematic_cam": "相机",
        "cinematic_look": "注视", "cinematic_start": "开始",
        "cinematic_end": "结束", "cinematic_text": "文本",
        "check_problems": "检查问题", "compile": "编译",
        "run_map": "运行地图",
        "fit_view": "适应视图", "show_grid": "显示网格",
        "snap_to_grid": "吸附到网格", "grid_larger": "增大网格",
        "grid_smaller": "减小网格", "textured": "3D 纹理",
        "wireframe": "3D 线框",
        "apply_texture": "应用纹理到选中对象",
        "rotate90": "旋转 90 度", "toggle_water": "切换水方块",
        "water_sea": "水材质：海水", "water_river": "水材质：河水",
        "edit_entity": "编辑实体...", "hollow": "掏空...",
        "carve": "雕刻（挖空）", "mirror": "镜像选中对象",
        "cut_doorway": "开洞（爬行通道）...", "terrain": "创建地形...",
        "doorway_title": "开洞", "doorway_width": "宽度：",
        "doorway_height": "高度：",
        "terrain_title": "创建地形", "terrain_size": "X 尺寸：",
        "terrain_size_z": "Z 尺寸：", "terrain_segments": "分段：",
        "terrain_height": "起伏高度：", "terrain_seed": "随机种子：",
        "group": "编组", "ungroup": "取消编组",
        "array": "创建阵列...", "snap_selection": "选中对象吸附到网格",
        "load_prefab": "加载预制体...", "settings": "设置...",
        "enable_instancing": "启用实例化", "reset_layout": "重置布局",
        "messages": "消息", "shortcuts": "键盘快捷键...",
        "about": "关于...",
        "align": "对齐", "align_x_min": "最小 X", "align_x_center": "居中 X",
        "align_x_max": "最大 X", "align_y_min": "最小 Y", "align_y_center": "居中 Y",
        "align_y_max": "最大 Y", "align_z_min": "最小 Z", "align_z_center": "居中 Z",
        "align_z_max": "最大 Z",
        "select": "选择:", "groups": "组", "objects": "对象",
        "solids": "固体", "current_texture": "当前纹理:",
        "browse": "浏览...", "replace": "替换...", "visgroups": "可视组:",
        "user": "用户", "auto": "自动", "cordon": "围栏",
        "show": "显示", "edit": "编辑", "mark": "标记",
        "move_selected": "移动选中:", "to_world": "转到世界",
        "to_entity": "转到实体", "categories": "类别:",
        "objects_label": "对象:", "create_prefab": "创建预制体",
        "all": "全部", "vis_empty": "（空）",
        "camera": "摄像机", "top": "俯视 (x/y)", "front": "前视 (y/z)",
        "side": "侧视 (x/z)",
        "for_help": "按 F1 获取帮助", "no_selection": "未选择。",
        "snap": "吸附", "grid": "网格", "on": "开", "off": "关",
        "selected_suffix": "个已选择。", "solid_suffix": "个固体",
        "ent_suffix": "个实体",
        "language": "语言:", "theme": "主题:", "dark": "深色",
        "light": "浅色", "settings_title": "设置",
        "ok": "确定", "cancel": "取消", "grid_label": "网格:",
        "map_props_title": "地图属性", "sky_config": "天空配置:",
        "spawn_position": "出生点位置 (世界单位):",
        "entity_report_title": "实体报告", "report_solids": "固体:",
        "report_entities": "实体:", "delete_solid": "删除固体",
        "resize": "调整尺寸...", "delete_entity": "删除实体",
        "props": "属性...", "configurations": "配置:",
        "game_config_title": "选择要使用的游戏配置",
        "check_title": "检查问题", "no_problems": "未发现问题。",
        "hollow_title": "掏空", "wall_thickness": "墙厚:",
        "array_title": "创建阵列", "count": "数量:", "offset_x": "偏移 X:",
        "offset_y": "偏移 Y:", "offset_z": "偏移 Z:", "axis": "轴向:",
        "tip_select": "选择工具", "tip_zoom": "放大 / 缩放",
        "tip_block": "创建固体方块", "tip_entity": "创建实体",
        "tip_cut": "裁剪 / 切割工具", "tip_texture": "应用当前纹理",
        "tip_light": "放置环境光", "tip_water": "切换水方块模式",
        "tip_player": "放置出生点", "tip_grid": "增大网格",
        "tip_snap": "切换网格吸附", "tip_camera": "聚焦 3D 摄像机",
        "tip_hand": "平移 2D 视图",
    },
}


def tr_text(lang, key, *args):
    """Look up a UI string, falling back to English then the key itself."""
    s = STRINGS.get(lang, {}).get(key)
    if s is None:
        s = STRINGS["en"].get(key, key)
    return s.format(*args) if args else s


# ============================================================
# Hammer 风格外观
# ============================================================
GAME_CONFIGS = ["Half-Life 2", "Half-Life 2: Deathmatch",
                "Half-Life 2: Episode One", "Half-Life 2: Episode Two"]

DARK_QSS = """
QWidget { background-color: #3c3c3c; color: #d8d8d8; font-size: 9pt; }
QMainWindow { background-color: #3c3c3c; }
QMenuBar { background-color: #3c3c3c; color: #e8e8e8; border-bottom: 1px solid #1e1e1e; }
QMenuBar::item { padding: 3px 9px; background: transparent; }
QMenuBar::item:selected { background-color: #4a6a8a; }
QMenu { background-color: #3c3c3c; color: #e8e8e8; border: 1px solid #1e1e1e; }
QMenu::item { padding: 4px 22px 4px 22px; }
QMenu::item:selected { background-color: #4a6a8a; }
QMenu::separator { height: 1px; background: #1e1e1e; margin: 3px 6px; }
QToolBar { background-color: #3c3c3c; border: none; border-bottom: 1px solid #1e1e1e;
           spacing: 1px; padding: 2px 4px; }
QToolButton { background-color: transparent; border: 1px solid transparent; padding: 2px; }
QToolButton:hover { background-color: #4a4a4a; border: 1px solid #5a5a5a; }
QToolButton:pressed, QToolButton:checked { background-color: #2f4f6f; border: 1px solid #6a8aaa; }
QPushButton { background-color: #4a4a4a; border: 1px solid #1e1e1e; padding: 3px 8px; color: #e8e8e8; }
QPushButton:hover { background-color: #5a5a5a; }
QPushButton:pressed { background-color: #2f2f2f; }
QPushButton:checked { background-color: #2f4f6f; border: 1px solid #6a8aaa; }
QPushButton:disabled { color: #7a7a7a; }
QComboBox { background-color: #2b2b2b; border: 1px solid #1e1e1e; padding: 2px 4px; color: #e8e8e8; }
QComboBox QAbstractItemView { background-color: #2b2b2b; color: #e8e8e8;
                              selection-background-color: #4a6a8a; }
QLineEdit, QPlainTextEdit, QTextEdit { background-color: #2b2b2b; border: 1px solid #1e1e1e;
                                       color: #d8d8d8; }
QListWidget { background-color: #2b2b2b; border: 1px solid #1e1e1e; color: #e0e0e0; }
QListWidget::item { padding: 1px 2px; }
QListWidget::item:selected { background-color: #4a6a8a; }
QTabWidget::pane { border: 1px solid #1e1e1e; background: #2b2b2b; }
QTabBar::tab { background-color: #3c3c3c; color: #c8c8c8; padding: 3px 12px;
               border: 1px solid #1e1e1e; border-bottom: none; }
QTabBar::tab:selected { background-color: #4a6a8a; color: #ffffff; }
QGroupBox { border: 1px solid #1e1e1e; margin-top: 7px; font-weight: bold; color: #e0e0e0; }
QGroupBox::title { subcontrol-origin: margin; left: 6px; padding: 0 3px; }
QStatusBar { background-color: #3c3c3c; color: #d0d0d0; border-top: 1px solid #1e1e1e; }
QStatusBar::item { border: none; }
QStatusBar QLabel { background: transparent; padding: 0 8px; }
QSplitter::handle { background-color: #3a3a3a; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
QSplitter::handle:hover { background-color: #4a7fb5; }
QLabel { background: transparent; }
QSpinBox, QDoubleSpinBox { background-color: #2b2b2b; border: 1px solid #1e1e1e;
                           color: #e8e8e8; padding: 1px 2px; }
QScrollArea { border: none; }
"""

LIGHT_QSS = """
QWidget { background-color: #f0f0f0; color: #1a1a1a; font-size: 9pt; }
QMainWindow { background-color: #f0f0f0; }
QMenuBar { background-color: #e8e8e8; color: #1a1a1a; border-bottom: 1px solid #b8b8b8; }
QMenuBar::item { padding: 3px 9px; background: transparent; }
QMenuBar::item:selected { background-color: #bcd4ee; }
QMenu { background-color: #f7f7f7; color: #1a1a1a; border: 1px solid #b8b8b8; }
QMenu::item { padding: 4px 22px 4px 22px; }
QMenu::item:selected { background-color: #bcd4ee; }
QMenu::separator { height: 1px; background: #c8c8c8; margin: 3px 6px; }
QToolBar { background-color: #e8e8e8; border: none; border-bottom: 1px solid #b8b8b8;
           spacing: 1px; padding: 2px 4px; }
QToolButton { background-color: transparent; border: 1px solid transparent; padding: 2px; }
QToolButton:hover { background-color: #d8e6f4; border: 1px solid #9ab8d8; }
QToolButton:pressed, QToolButton:checked { background-color: #bcd4ee; border: 1px solid #6a8aaa; }
QPushButton { background-color: #e2e2e2; border: 1px solid #b8b8b8; padding: 3px 8px; color: #1a1a1a; }
QPushButton:hover { background-color: #d8d8d8; }
QPushButton:pressed { background-color: #c4c4c4; }
QPushButton:checked { background-color: #bcd4ee; border: 1px solid #6a8aaa; }
QPushButton:disabled { color: #909090; }
QComboBox { background-color: #ffffff; border: 1px solid #b8b8b8; padding: 2px 4px; color: #1a1a1a; }
QComboBox QAbstractItemView { background-color: #ffffff; color: #1a1a1a;
                              selection-background-color: #bcd4ee; }
QLineEdit, QPlainTextEdit, QTextEdit { background-color: #ffffff; border: 1px solid #b8b8b8;
                                       color: #1a1a1a; }
QListWidget { background-color: #ffffff; border: 1px solid #b8b8b8; color: #1a1a1a; }
QListWidget::item { padding: 1px 2px; }
QListWidget::item:selected { background-color: #bcd4ee; }
QTabWidget::pane { border: 1px solid #b8b8b8; background: #ffffff; }
QTabBar::tab { background-color: #e2e2e2; color: #333333; padding: 3px 12px;
               border: 1px solid #b8b8b8; border-bottom: none; }
QTabBar::tab:selected { background-color: #bcd4ee; color: #000000; }
QGroupBox { border: 1px solid #b8b8b8; margin-top: 7px; font-weight: bold; color: #1a1a1a; }
QGroupBox::title { subcontrol-origin: margin; left: 6px; padding: 0 3px; }
QStatusBar { background-color: #e8e8e8; color: #333333; border-top: 1px solid #b8b8b8; }
QStatusBar::item { border: none; }
QStatusBar QLabel { background: transparent; padding: 0 8px; }
QSplitter::handle { background-color: #b8b8b8; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
QSplitter::handle:hover { background-color: #7aa7d8; }
QLabel { background: transparent; }
QSpinBox, QDoubleSpinBox { background-color: #ffffff; border: 1px solid #b8b8b8;
                           color: #1a1a1a; padding: 1px 2px; }
QScrollArea { border: none; }
"""

THEME_QSS = {"dark": DARK_QSS, "light": LIGHT_QSS}
THEME_COLORS = {
    "dark": {"viewport_bg": (0.05, 0.05, 0.07), "grid": (0.16, 0.36, 0.16),
             "caption": "#2b2b2b", "caption_fg": "#d8d8d8",
             "caption_border": "#1e1e1e", "strip": "#333333",
             "strip_border": "#1e1e1e", "icon": "#dcdcdc"},
    "light": {"viewport_bg": (0.72, 0.72, 0.74), "grid": (0.30, 0.52, 0.30),
              "caption": "#e2e2e2", "caption_fg": "#1a1a1a",
              "caption_border": "#b8b8b8", "strip": "#e8e8e8",
              "strip_border": "#b8b8b8", "icon": "#2a2a2a"},
}



def _tool_icon(kind, size=22, fg="#dcdcdc"):
    """Small monochrome tool icons drawn in code (no external assets)."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    try:
        p.setRenderHint(QPainter.Antialiasing, True)
        fg = QColor(fg)
        pen = QPen(fg)
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if kind == "select":
            p.setBrush(QBrush(fg))
            p.drawPolygon(QPolygon([QPoint(5, 3), QPoint(5, 17), QPoint(9, 13),
                                    QPoint(12, 19), QPoint(14, 18), QPoint(11, 12),
                                    QPoint(17, 12)]))
        elif kind == "zoom":
            p.drawEllipse(QPoint(9, 9), 6, 6)
            p.drawLine(14, 14, 19, 19)
        elif kind == "block":
            p.drawRect(4, 4, 14, 14)
        elif kind == "hollow":
            p.drawRect(4, 4, 14, 14)
            p.drawRect(8, 8, 6, 6)
        elif kind == "entity":
            p.setBrush(QBrush(fg))
            p.drawPolygon(QPolygon([QPoint(11, 3), QPoint(19, 11),
                                    QPoint(11, 19), QPoint(3, 11)]))
        elif kind == "cut":
            p.drawLine(5, 17, 16, 6)
            p.drawLine(6, 6, 17, 17)
            p.drawLine(4, 19, 8, 15)
        elif kind == "texture":
            p.drawRect(3, 3, 16, 16)
            p.setBrush(QBrush(fg))
            p.drawRect(3, 3, 8, 8)
            p.drawRect(11, 11, 8, 8)
        elif kind == "light":
            p.drawEllipse(QPoint(11, 11), 4, 4)
            for dx, dy in ((0, -8), (0, 8), (-8, 0), (8, 0),
                           (-6, -6), (6, -6), (-6, 6), (6, 6)):
                p.drawLine(11 + int(dx * 0.55), 11 + int(dy * 0.55),
                           11 + dx, 11 + dy)
        elif kind == "water":
            for y in (7, 12, 17):
                p.drawArc(2, y - 3, 9, 6, 0, 180 * 16)
                p.drawArc(11, y - 3, 9, 6, 0, 180 * 16)
        elif kind == "player":
            p.drawEllipse(QPoint(11, 5), 3, 3)
            p.drawLine(11, 9, 11, 16)
            p.drawLine(7, 11, 15, 11)
            p.drawLine(11, 16, 8, 20)
            p.drawLine(11, 16, 14, 20)
        elif kind == "camera":
            p.drawRect(3, 7, 16, 10)
            p.drawEllipse(QPoint(11, 12), 3, 3)
            p.drawRect(8, 5, 6, 3)
        elif kind == "grid":
            for i in range(4):
                p.drawLine(4, 4 + i * 5, 18, 4 + i * 5)
                p.drawLine(4 + i * 5, 4, 4 + i * 5, 18)
        elif kind == "snap":
            p.drawArc(5, 8, 12, 10, 0, 180 * 16)
            p.drawLine(5, 8, 5, 14)
            p.drawLine(17, 8, 17, 14)
        elif kind == "hand":
            p.drawRect(7, 7, 8, 10)
            for x in (8, 11, 14):
                p.drawLine(x, 7, x, 3)
        else:
            p.drawRect(5, 5, 12, 12)
    finally:
        p.end()
    return QIcon(pix)


class GameConfigDialog(QDialog):
    """Hammer's start-up "Select a game configuration to use" prompt."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select a game configuration to use")
        self.setFixedSize(316, 232)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Select a game configuration to use"))
        layout.addWidget(QLabel("Configurations:"))
        self.list = QListWidget()
        self.list.addItems(GAME_CONFIGS)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        layout.addWidget(self.list, stretch=1)
        row = QHBoxLayout()
        row.addStretch()
        btn_ok = QPushButton("OK")
        btn_ok.setFixedWidth(80)
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_ok)
        layout.addLayout(row)

    def get_config(self):
        item = self.list.currentItem()
        return item.text() if item is not None else GAME_CONFIGS[0]


class MapPropertiesDialog(QDialog):
    """Map > Map Properties: sky configuration and spawn position."""

    def __init__(self, editor, sky_names, sky, spawn, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Map Properties")
        self.setMinimumWidth(320)
        lang = getattr(editor, "lang", "en")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr_text(lang, "sky_config")))
        self.sky_combo = QComboBox()
        self.sky_combo.addItems(sky_names or ["day_clear.txt"])
        self.sky_combo.setCurrentText(sky)
        layout.addWidget(self.sky_combo)
        layout.addWidget(QLabel(tr_text(lang, "spawn_position")))
        row = QHBoxLayout()
        row.addWidget(QLabel("X:"))
        self.spin_x = QDoubleSpinBox()
        self.spin_x.setRange(-9999, 9999)
        self.spin_x.setDecimals(2)
        self.spin_x.setValue(float(spawn[0]))
        row.addWidget(self.spin_x)
        row.addWidget(QLabel("Z:"))
        self.spin_z = QDoubleSpinBox()
        self.spin_z.setRange(-9999, 9999)
        self.spin_z.setDecimals(2)
        self.spin_z.setValue(float(spawn[1]))
        row.addWidget(self.spin_z)
        layout.addLayout(row)
        btns = QHBoxLayout()
        btns.addStretch()
        ok = QPushButton(tr_text(lang, "ok"))
        cancel = QPushButton(tr_text(lang, "cancel"))
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(cancel)
        layout.addLayout(btns)

    def values(self):
        return self.sky_combo.currentText(), (self.spin_x.value(),
                                              self.spin_z.value())


class SettingsDialog(QDialog):
    """Language + theme settings."""

    def __init__(self, lang, theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr_text(lang, "settings_title"))
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.addWidget(QLabel(tr_text(lang, "language")), 0, 0)
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("English", "en")
        self.lang_combo.addItem("简体中文", "zh")
        self.lang_combo.setCurrentIndex(0 if lang == "en" else 1)
        form.addWidget(self.lang_combo, 0, 1)
        form.addWidget(QLabel(tr_text(lang, "theme")), 1, 0)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr_text(lang, "dark"), "dark")
        self.theme_combo.addItem(tr_text(lang, "light"), "light")
        self.theme_combo.setCurrentIndex(0 if theme == "dark" else 1)
        form.addWidget(self.theme_combo, 1, 1)
        layout.addLayout(form)
        btns = QHBoxLayout()
        btns.addStretch()
        ok = QPushButton(tr_text(lang, "ok"))
        cancel = QPushButton(tr_text(lang, "cancel"))
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(cancel)
        layout.addLayout(btns)

    def values(self):
        return self.lang_combo.currentData(), self.theme_combo.currentData()


# ============================================================
# 立方体几何
# ============================================================
CUBE_CORNERS = [
    (-1, -1, -1), (-1, -1, 1), (-1, 1, -1), (-1, 1, 1),
    ( 1, -1, -1), ( 1, -1, 1), ( 1, 1, -1), ( 1, 1, 1),
]

CUBE_FACES = [
    ([4, 6, 7, 5], ( 1, 0, 0)),
    ([0, 1, 3, 2], (-1, 0, 0)),
    ([2, 3, 7, 6], ( 0, 1, 0)),
    ([0, 4, 5, 1], ( 0,-1, 0)),
    ([1, 5, 7, 3], ( 0, 0, 1)),
    ([0, 2, 6, 4], ( 0, 0,-1)),
]

def _cross(u, v):
    return (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])

def _corner_idx(cx, cy, cz):
    return ((cx + 1) // 2) * 4 + ((cy + 1) // 2) * 2 + ((cz + 1) // 2)

def make_multi_cut_cube_data(cuts):
    if not cuts: return None
    cut_map = {}
    for c in cuts:
        try:
            cx, cy, cz, amount = int(c[0]), int(c[1]), int(c[2]), float(c[3])
        except Exception:
            continue
        if cx not in (-1, 1) or cy not in (-1, 1) or cz not in (-1, 1): continue
        amount = max(0.01, min(0.5, amount))
        cut_map[_corner_idx(cx, cy, cz)] = amount
    if not cut_map: return None

    def trunc_point(corner_i, other_i, amount):
        c = CUBE_CORNERS[corner_i]; o = CUBE_CORNERS[other_i]
        return (c[0] + amount*(o[0]-c[0]), c[1] + amount*(o[1]-c[1]), c[2] + amount*(o[2]-c[2]))

    verts = []
    for face_indices, normal in CUBE_FACES:
        cut_info = None
        for i, ci in enumerate(face_indices):
            if ci in cut_map:
                cut_info = (i, ci, cut_map[ci]); break
        if cut_info is None:
            pts = [CUBE_CORNERS[ci] for ci in face_indices]
            tris = [(0, 1, 2), (0, 2, 3)]
        else:
            i, ci, amount = cut_info
            prev_ci = face_indices[(i - 1) % 4]
            next_ci = face_indices[(i + 1) % 4]
            pts = []
            for j, cj in enumerate(face_indices):
                if j == i:
                    pts.append(trunc_point(ci, prev_ci, amount))
                    pts.append(trunc_point(ci, next_ci, amount))
                else:
                    pts.append(CUBE_CORNERS[cj])
            tris = [(0, 1, 2), (0, 2, 3), (0, 3, 4)]
        for tri in tris:
            for vi in tri:
                p = pts[vi]
                verts.extend([p[0], p[1], p[2], normal[0], normal[1], normal[2], 0.0, 0.0])

    for ci, amount in cut_map.items():
        cx, cy, cz = CUBE_CORNERS[ci]
        A = (cx * (1 - 2*amount), cy, cz)
        B = (cx, cy * (1 - 2*amount), cz)
        C = (cx, cy, cz * (1 - 2*amount))
        ln = (cx*cx + cy*cy + cz*cz) ** 0.5
        sn = (cx/ln, cy/ln, cz/ln)
        u = (B[0]-A[0], B[1]-A[1], B[2]-A[2])
        v = (C[0]-A[0], C[1]-A[1], C[2]-A[2])
        cr = _cross(u, v)
        dot = cr[0]*sn[0] + cr[1]*sn[1] + cr[2]*sn[2]
        order = [A, B, C] if dot > 0 else [A, C, B]
        for pt in order:
            verts.extend([pt[0], pt[1], pt[2], sn[0], sn[1], sn[2], 0.0, 0.0])

    return np.array(verts, dtype=np.float32) if verts else None

def make_multi_cut_cube_lines(cuts):
    data = make_multi_cut_cube_data(cuts)
    if data is None:
        return make_cube_lines(1.0, 1.0, 1.0)
    n = len(data) // 8
    positions = [tuple(round(v, 5) for v in data[i*8:i*8+3]) for i in range(n)]
    edges = set()
    for i in range(0, n, 3):
        tri = [positions[i], positions[i+1], positions[i+2]]
        for j in range(3):
            a = tri[j]; b = tri[(j+1) % 3]
            edges.add(tuple(sorted([a, b])))
    lines = []
    for a, b in edges:
        lines.extend(a); lines.extend(b)
    return np.array(lines, dtype=np.float32)

def normalize_cuts(cuts):
    if not cuts: return None
    if isinstance(cuts[0], (int, float)):
        return [list(cuts)]
    return [list(c) for c in cuts]

def cuts_to_key(cuts):
    n = normalize_cuts(cuts)
    if not n: return None
    return tuple(sorted((int(c[0]), int(c[1]), int(c[2]), round(float(c[3]), 3)) for c in n))

def make_box_uv(sx, sy, sz):
    faces = [
        [( sx,-sy, sz, 1,0,0, 0.0,   2*sy), ( sx,-sy,-sz, 1,0,0, 2*sx, 2*sy), ( sx, sy,-sz, 1,0,0, 2*sx, 0.0),
         ( sx,-sy, sz, 1,0,0, 0.0,   2*sy), ( sx, sy,-sz, 1,0,0, 2*sx, 0.0),  ( sx, sy, sz, 1,0,0, 0.0,  0.0)],
        [(-sx,-sy,-sz, -1,0,0, 0.0,  2*sy), (-sx,-sy, sz, -1,0,0, 2*sx, 2*sy), (-sx, sy, sz, -1,0,0, 2*sx, 0.0),
         (-sx,-sy,-sz, -1,0,0, 0.0,  2*sy), (-sx, sy, sz, -1,0,0, 2*sx, 0.0),  (-sx, sy,-sz, -1,0,0, 0.0,  0.0)],
        [(-sx, sy, sz, 0,1,0, 2*sx,2*sy), ( sx, sy, sz, 0,1,0, 0,2*sy), ( sx, sy,-sz, 0,1,0, 0,0),
         (-sx, sy, sz, 0,1,0, 2*sx,2*sy), ( sx, sy,-sz, 0,1,0, 0,0), (-sx, sy,-sz, 0,1,0, 2*sx,0)],
        [(-sx,-sy,-sz, 0,-1,0, 2*sx,2*sy), ( sx,-sy,-sz, 0,-1,0, 0,2*sy), ( sx,-sy, sz, 0,-1,0, 0,0),
         (-sx,-sy,-sz, 0,-1,0, 2*sx,2*sy), ( sx,-sy, sz, 0,-1,0, 0,0), (-sx,-sy, sz, 0,-1,0, 2*sx,0)],
        [(-sx,-sy, sz, 0,0,1, 2*sx,2*sy), ( sx,-sy, sz, 0,0,1, 0,2*sy), ( sx, sy, sz, 0,0,1, 0,0),
         (-sx,-sy, sz, 0,0,1, 2*sx,2*sy), ( sx, sy, sz, 0,0,1, 0,0), (-sx, sy, sz, 0,0,1, 2*sx,0)],
        [( sx,-sy,-sz, 0,0,-1, 2*sx,2*sy), (-sx,-sy,-sz, 0,0,-1, 0,2*sy), (-sx, sy,-sz, 0,0,-1, 0,0),
         ( sx,-sy,-sz, 0,0,-1, 2*sx,2*sy), (-sx, sy,-sz, 0,0,-1, 0,0), ( sx, sy,-sz, 0,0,-1, 2*sx,0)],
    ]
    verts = []
    for f in faces:
        for v in f: verts.extend(v)
    return np.array(verts, dtype=np.float32)

def make_cube_lines(sx, sy, sz):
    return np.array([
        -sx,-sy,-sz,  sx,-sy,-sz,  sx,-sy,-sz,  sx, sy,-sz,
         sx, sy,-sz, -sx, sy,-sz, -sx, sy,-sz, -sx,-sy,-sz,
        -sx,-sy, sz,  sx,-sy, sz,  sx,-sy, sz,  sx, sy, sz,
         sx, sy, sz, -sx, sy, sz, -sx, sy, sz, -sx,-sy, sz,
        -sx,-sy,-sz, -sx,-sy, sz,  sx,-sy,-sz,  sx,-sy, sz,
         sx, sy,-sz,  sx, sy, sz, -sx, sy,-sz, -sx, sy, sz,
    ], dtype=np.float32)

def make_cube_pos():
    return np.array([
        -1,-1,-1, 1,-1,-1, 1,-1,1, -1,-1,-1, 1,-1,1, -1,-1,1,
        -1,1,1, 1,1,1, 1,1,-1, -1,1,1, 1,1,-1, -1,1,-1,
        -1,-1,-1, -1,-1,1, -1,1,1, -1,-1,-1, -1,1,1, -1,1,-1,
        1,-1,1, 1,-1,-1, 1,1,-1, 1,-1,1, 1,1,-1, 1,1,1,
        -1,-1,1, 1,-1,1, 1,1,1, -1,-1,1, 1,1,1, -1,1,1,
        1,-1,-1, -1,-1,-1, -1,1,-1, 1,-1,-1, -1,1,-1, 1,1,-1,
    ], dtype=np.float32)

# ============================================================
# 语言选择对话框
# ============================================================
class LanguageDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Language / 语言")
        self.setFixedSize(300, 150)
        self.selected_lang = None
        layout = QVBoxLayout(self)
        self.btn_zh = QPushButton("中文")
        self.btn_en = QPushButton("English")
        self.btn_ok = QPushButton("OK"); self.btn_ok.setEnabled(False)
        for btn in (self.btn_zh, self.btn_en):
            btn.setFixedHeight(40)
            btn.clicked.connect(lambda _, b=btn: self.select_lang(b))
        self.btn_ok.clicked.connect(self.accept)
        layout.addWidget(self.btn_zh); layout.addWidget(self.btn_en); layout.addWidget(self.btn_ok)
    def select_lang(self, btn):
        self.btn_zh.setStyleSheet(""); self.btn_en.setStyleSheet("")
        btn.setStyleSheet("background-color: #4A90E2; color: white;")
        self.selected_lang = "zh" if btn == self.btn_zh else "en"
        self.btn_ok.setEnabled(True)
    def get_lang(self): return self.selected_lang

# ============================================================
# 纹理浏览器对话框
# ============================================================
class TextureBrowserDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Textures")
        self.resize(900, 600)
        self.materials = scan_materials()
        self.selected_path = None
        layout = QVBoxLayout(self)
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.textChanged.connect(self.filter_items)
        top_layout.addWidget(self.filter_edit)
        layout.addLayout(top_layout)
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListView.IconMode)
        self.list_widget.setIconSize(QSize(128, 128))
        self.list_widget.setGridSize(QSize(150, 170))
        self.list_widget.setResizeMode(QListView.Adjust)
        self.list_widget.setMovement(QListView.Static)
        self.list_widget.setSpacing(10)
        self.list_widget.setStyleSheet("""
            QListWidget { background: #1e1e1e; }
            QListWidget::item:selected { border: 2px solid #ffcc00; background: #404040; }
            QListWidget::item { border: 1px solid #333; }
        """)
        self.list_widget.itemClicked.connect(self.on_item_selected)
        self.list_widget.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list_widget)
        bottom_layout = QHBoxLayout()
        self.path_label = QLabel("")
        bottom_layout.addWidget(QLabel("Path:"))
        bottom_layout.addWidget(self.path_label, stretch=1)
        self.btn_ok = QPushButton("OK"); self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = QPushButton("Cancel"); self.btn_cancel.clicked.connect(self.reject)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_ok); bottom_layout.addWidget(self.btn_cancel)
        layout.addLayout(bottom_layout)
        self.populate_list()
    def populate_list(self):
        self.list_widget.clear()
        for mat in self.materials:
            full_path = material_full_path(mat)
            pix = QPixmap(full_path)
            if not pix.isNull():
                item = QListWidgetItem(QIcon(pix), mat)
                item.setData(Qt.UserRole, mat)
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(Qt.white)
                self.list_widget.addItem(item)
    def filter_items(self, text):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text.lower() not in item.text().lower())
    def on_item_selected(self, item):
        self.selected_path = item.data(Qt.UserRole)
        self.path_label.setText(self.selected_path)
    def get_selected_texture(self): return self.selected_path

# ============================================================
# 实体编辑对话框
# ============================================================
class EntityEditDialog(QDialog):
    def __init__(self, editor, index, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.index = index
        e = editor.entities[index]
        etype = e[0]
        x, y, z = e[1], e[2], e[3]
        params = dict(e[5]) if len(e) > 5 and e[5] else {}

        self.setWindowTitle(editor.texts["edit_entity"].format(index))
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(editor.texts["entity_type"]))
        self.type_combo = QComboBox()
        self.type_combo.addItems(ENTITY_TYPES)
        if etype in ENTITY_TYPES:
            self.type_combo.setCurrentText(etype)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        layout.addWidget(self.type_combo)

        for label, key, val in [(editor.texts["pos_x"], "x", x),
                                 (editor.texts["pos_y"], "y", y),
                                 (editor.texts["pos_z"], "z", z)]:
            layout.addWidget(QLabel(label))
            sp = QDoubleSpinBox()
            sp.setRange(-9999, 9999)
            sp.setDecimals(3)
            sp.setValue(val)
            setattr(self, f"spin_{key}", sp)
            layout.addWidget(sp)

        layout.addWidget(QLabel("--- Parameters ---"))
        self.param_widgets = {}
        self.param_group = QGroupBox("")
        self.param_layout = QVBoxLayout(self.param_group)
        layout.addWidget(self.param_group)
        self._build_params(etype, params)

        btn_box = QHBoxLayout()
        btn_ok = QPushButton(editor.texts["ok"])
        btn_cancel = QPushButton(editor.texts["cancel"])
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

    def _on_type_changed(self, new_type):
        self._build_params(new_type, {})

    def _clear_params(self):
        while self.param_layout.count():
            item = self.param_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.param_widgets.clear()

    def _build_params(self, etype, current):
        self._clear_params()
        edef = ENTITY_DEFS.get(etype, {})
        defaults = dict(edef.get("params", {}))
        merged = {**defaults, **current}

        def add_text(key, label):
            self.param_layout.addWidget(QLabel(label))
            le = QLineEdit(str(merged.get(key, "")))
            self.param_widgets[key] = le
            self.param_layout.addWidget(le)

        def add_choice(key, label, values):
            self.param_layout.addWidget(QLabel(label))
            cb = QComboBox()
            cb.setEditable(True)
            cb.addItems(values)
            cur = str(merged.get(key, "") or "")
            if cur:
                cb.setCurrentText(cur)
            self.param_widgets[key] = cb
            self.param_layout.addWidget(cb)

        weapon_names = sorted(
            k for k, d in ENTITY_DEFS.items()
            if d.get("folder") == "weapon" or d.get("category") == "Weapon")
        npc_names = sorted(
            k for k, d in ENTITY_DEFS.items()
            if d.get("folder") == "npc" or d.get("category") == "NPC")
        item_names = sorted(
            k for k, d in ENTITY_DEFS.items()
            if d.get("folder") in ("items", "models")
            or d.get("category") in ("Item", "Model"))

        def add_float(key, label, lo=-9999, hi=9999, step=0.1):
            self.param_layout.addWidget(QLabel(label))
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(4)
            sp.setValue(float(merged.get(key, 0.0)))
            self.param_widgets[key] = sp
            self.param_layout.addWidget(sp)

        if etype == "light_environment":
            add_float("yaw",   "Yaw (deg)",   -360, 360, 5)
            add_float("pitch", "Pitch (deg)", -90, 90, 5)
            add_float("r", "R", 0, 1, 0.05)
            add_float("g", "G", 0, 1, 0.05)
            add_float("b", "B", 0, 1, 0.05)
            add_float("intensity", "Intensity", 0, 10, 0.1)
        elif etype == "light":
            add_float("r", "R", 0, 1, 0.05)
            add_float("g", "G", 0, 1, 0.05)
            add_float("b", "B", 0, 1, 0.05)
            add_float("intensity", "Intensity", 0, 10, 0.1)
        elif etype == "weapon_spawn":
            add_choice("weapon", "Weapon name (reg_name)", weapon_names)
        elif etype == "item_spawn":
            add_choice("item", "Item name (reg_name)", item_names)
        elif etype == "npc":
            add_choice("npc", "NPC name (reg_name)", npc_names)
        elif etype == "prop_static":
            add_text("model", "Model path (models/xxx.obj)")
        elif etype == "trigger":
            add_text("name", "Trigger name")
        else:
            edef_full = ENTITY_DEFS.get(etype, {})
            src_params = edef_full.get("params", {})
            if src_params:
                info = QLabel(
                    f"<i>来自 addons/{edef_full.get('folder', '?')}/"
                    f"{os.path.basename(edef_full.get('file', '?'))}</i>")
                info.setWordWrap(True)
                self.param_layout.addWidget(info)
                for k, v in src_params.items():
                    self.param_layout.addWidget(QLabel(f"{k} = {v}"))

    def apply(self):
        e = self.editor.entities[self.index]
        e[0] = self.type_combo.currentText()
        e[1] = self.spin_x.value()
        e[2] = self.spin_y.value()
        e[3] = self.spin_z.value()
        params = {}
        for k, w in self.param_widgets.items():
            if isinstance(w, QDoubleSpinBox):
                params[k] = w.value()
            elif isinstance(w, QComboBox):
                params[k] = w.currentText()
            elif isinstance(w, QLineEdit):
                params[k] = w.text()
        while len(e) < 6:
            e.append(None)
        e[5] = params

# ============================================================
# 切角对话框
# ============================================================
class CutBlockDialog(QDialog):
    CORNER_NAMES = [
        "(-X, -Y, -Z)", "(-X, -Y, +Z)", "(-X, +Y, -Z)", "(-X, +Y, +Z)",
        "(+X, -Y, -Z)", "(+X, -Y, +Z)", "(+X, +Y, -Z)", "(+X, +Y, +Z)",
    ]
    CORNER_VALUES = [
        (-1,-1,-1), (-1,-1, 1), (-1, 1,-1), (-1, 1, 1),
        ( 1,-1,-1), ( 1,-1, 1), ( 1, 1,-1), ( 1, 1, 1),
    ]

    def __init__(self, editor, existing_cuts, parent=None):
        super().__init__(parent)
        self.editor = editor
        norm = normalize_cuts(existing_cuts) or []
        existing_set = {tuple(c[:3]): float(c[3]) for c in norm}

        self.setWindowTitle(editor.texts["cut_title"])
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(editor.texts["cut_corner_label"]))
        grid = QGridLayout()
        self.checks = []
        for i, name in enumerate(self.CORNER_NAMES):
            chk = QCheckBox(name)
            if self.CORNER_VALUES[i] in existing_set:
                chk.setChecked(True)
            self.checks.append(chk)
            grid.addWidget(chk, i // 2, i % 2)
        layout.addLayout(grid)

        layout.addWidget(QLabel(editor.texts["cut_amount_label"]))
        self.amount_spin = QDoubleSpinBox()
        self.amount_spin.setRange(0.05, 0.50)
        self.amount_spin.setSingleStep(0.05)
        self.amount_spin.setValue(next(iter(existing_set.values())) if existing_set else 0.25)
        layout.addWidget(self.amount_spin)

        btn_box = QHBoxLayout()
        btn_clear = QPushButton(editor.texts["remove_cut"])
        btn_clear.clicked.connect(self._clear_all)
        btn_ok = QPushButton(editor.texts["ok"])
        btn_cancel = QPushButton(editor.texts["cancel"])
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_clear)
        btn_box.addStretch()
        btn_box.addWidget(btn_ok)
        btn_box.addWidget(btn_cancel)
        layout.addLayout(btn_box)

    def _clear_all(self):
        for chk in self.checks:
            chk.setChecked(False)

    def get_cuts(self):
        amount = self.amount_spin.value()
        result = []
        for chk, val in zip(self.checks, self.CORNER_VALUES):
            if chk.isChecked():
                result.append([val[0], val[1], val[2], amount])
        return result

# ============================================================
# 3D 视图
# ============================================================
class HammerGLWidget(QGLWidget):
    def __init__(self, view_type, editor, parent=None):
        super().__init__(parent)
        self.view_type = view_type; self.editor = editor
        self.ctx = None; self.texture_cache = {}
        self.text_textures = {}
        self.cut_vaos = {}
        self.cam_x, self.cam_y, self.cam_z = 16.0, 16.0, 16.0
        self.yaw = -0.7; self.pitch = -0.6
        self.offset_x = 0.0; self.offset_y = 0.0
        self.scale = 40.0; self.grid_step = 8
        self.snap_enabled = True
        self.show_grid = True
        self.wireframe = False
        self.viewport_bg = (0.05, 0.05, 0.07)
        self.grid_color = (0.16, 0.36, 0.16)
        self.mouse_down = False; self.drag_start = QPoint()
        self.drag_world_start = None; self.drag_world_end = None
        self.is_dragging = False; self.is_panning = False
        self.grid_vbo = None; self.grid_vao = None; self.last_grid_key = None
        self.vbo_unit = None; self.vao_unit = None; self.vao_unit_solid = None
        self.vbo_lines = None; self.vao_lines = None
        self.player_obj_vbo = None
        self.player_obj_solid_vao = None
        self.player_obj_lines_vao = None
        # GPU caches so paintGL never allocates buffers per frame.
        self._brush_cache = {}
        self._terrain_cache = {}
        self._drag_undo = None
        self._drag_multi = []
        self._cut_undo = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.keys_down = set()
        self.skybox_prog = None; self.skybox_vao = None; self.skybox_tex = None
        self.gradient_prog = None; self.gradient_vao = None
        self.gradient_horizon = (0.5, 0.7, 0.9)
        self.gradient_zenith = (0.1, 0.3, 0.6)
        self.mouse_look_active = False
        self._last_mouse_pos = None
        self.move_timer = QTimer(self)
        self.move_timer.setInterval(16)
        self.move_timer.timeout.connect(self._update_wasd)
        self.drag_target_type = None
        self.drag_target_idx = -1
        self.drag_start_world = None
        self.drag_target_start_pos = None
        self._resize_drag = None
        self.cut_selected_idx = -1
        self.cut_drag_kind = None
        self.cut_drag_handle = -1
        self.cut_drag_start_screen = None
        self.cut_drag_block_start = None
        self.cut_drag_accum = 0.0
        self._right_press_pos = None
        self._right_dragged = False
        self._sky_loaded = False

    def initializeGL(self):
        self.ctx = moderngl.create_context()
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.enable(moderngl.CULL_FACE)

        vs_block = """
        #version 330
        in vec3 pos; in vec3 normal;
        uniform mat4 proj_view; uniform mat4 model;
        out vec3 v_world_pos; out vec3 v_normal;
        void main(){
            vec4 wp = model * vec4(pos, 1.0);
            v_world_pos = wp.xyz;
            v_normal = mat3(model) * normal;
            gl_Position = proj_view * wp;
        }
        """
        fs_block = """
        #version 330
        in vec3 v_world_pos; in vec3 v_normal;
        uniform sampler2D tex; out vec4 color;
        void main(){
            vec3 n = normalize(v_normal);
            vec2 uv;
            if (abs(n.x) > 0.5)      uv = v_world_pos.zy;
            else if (abs(n.y) > 0.5) uv = v_world_pos.xz;
            else                     uv = v_world_pos.xy;
            color = texture(tex, uv);
        }
        """
        vs_line = "#version 330\nin vec3 pos; uniform mat4 mvp;\nvoid main(){ gl_Position = mvp * vec4(pos, 1.0); }"
        fs_line = "#version 330\nuniform vec4 line_color;\nout vec4 color;\nvoid main(){ color = line_color; }"
        vs_solid = "#version 330\nin vec3 pos; uniform mat4 proj_view; uniform mat4 model;\nvoid main(){ gl_Position = proj_view * model * vec4(pos, 1.0); }"
        fs_solid = "#version 330\nuniform vec4 solid_color;\nout vec4 color;\nvoid main(){ color = solid_color; }"
        vs_overlay = "#version 330\nin vec2 pos;\nuniform vec4 u_rect;\nvoid main(){ vec2 p = u_rect.xy + pos * u_rect.zw;\n gl_Position = vec4(p, 0.0, 1.0); }"
        fs_overlay = "#version 330\nuniform vec4 u_color;\nout vec4 color;\nvoid main(){ color = u_color; }"
        vs_text = """
        #version 330
        in vec2 pos; in vec2 uv; uniform vec4 u_rect; out vec2 v_uv;
        void main(){ v_uv = uv; vec2 p = u_rect.xy + pos * u_rect.zw; gl_Position = vec4(p, 0.0, 1.0); }
        """
        fs_text = """
        #version 330
        in vec2 v_uv; uniform sampler2D u_tex; out vec4 color;
        void main(){ color = texture(u_tex, v_uv); }
        """

        self.prog_block = self.ctx.program(vertex_shader=vs_block, fragment_shader=fs_block)
        self.prog_line = self.ctx.program(vertex_shader=vs_line, fragment_shader=fs_line)
        self.prog_solid = self.ctx.program(vertex_shader=vs_solid, fragment_shader=fs_solid)
        self.prog_overlay = self.ctx.program(vertex_shader=vs_overlay, fragment_shader=fs_overlay)
        self.prog_text = self.ctx.program(vertex_shader=vs_text, fragment_shader=fs_text)

        self.vbo_unit = self.ctx.buffer(make_box_uv(1.0, 1.0, 1.0).tobytes())
        self.vao_unit = self.ctx.vertex_array(self.prog_block, [(self.vbo_unit, "3f 3f 2x4", "pos", "normal")])
        self.vao_unit_solid = self.ctx.vertex_array(self.prog_solid, [(self.vbo_unit, "3f 5x4", "pos")])
        self.vbo_lines = self.ctx.buffer(make_cube_lines(1.0, 1.0, 1.0).tobytes())
        self.vao_lines = self.ctx.vertex_array(self.prog_line, [(self.vbo_lines, "3f", "pos")])
        # A unit X in the XZ plane: the top-view centre marker.
        x_marks = np.array([
            -1.0, 0.0, -1.0, 1.0, 0.0, 1.0,
            -1.0, 0.0, 1.0, 1.0, 0.0, -1.0,
        ], dtype="f4")
        self.vbo_xmark = self.ctx.buffer(x_marks.tobytes())
        self.vao_xmark = self.ctx.vertex_array(
            self.prog_line, [(self.vbo_xmark, "3f", "pos")])

        quad_data = np.array([0,0, 1,0, 1,1, 0,0, 1,1, 0,1], dtype='f4')
        vbo_quad = self.ctx.buffer(quad_data.tobytes())
        self.vao_overlay_quad = self.ctx.vertex_array(self.prog_overlay, [(vbo_quad, "2f", "pos")])

        quad_uv_data = np.array([
            0,0, 0,0,  1,0, 1,0,  1,1, 1,1,
            0,0, 0,0,  1,1, 1,1,  0,1, 0,1,
        ], dtype='f4')
        vbo_quad_uv = self.ctx.buffer(quad_uv_data.tobytes())
        self.vao_text_quad = self.ctx.vertex_array(self.prog_text, [(vbo_quad_uv, "2f 2f", "pos", "uv")])

        vs_sky = "#version 330\nin vec3 in_pos; uniform mat4 proj; uniform mat4 view_rot; out vec3 v_dir;\nvoid main(){ v_dir=in_pos; vec4 p=proj*view_rot*vec4(in_pos,1.0); gl_Position=p.xyww; }"
        fs_sky = "#version 330\nin vec3 v_dir; uniform samplerCube sky_cube; out vec4 color;\nvoid main(){ color=texture(sky_cube, v_dir); }"
        self.skybox_prog = self.ctx.program(vertex_shader=vs_sky, fragment_shader=fs_sky)
        vbo_sky = self.ctx.buffer(make_cube_pos().tobytes())
        self.skybox_vao = self.ctx.vertex_array(self.skybox_prog, [(vbo_sky, "3f", "in_pos")])

        vs_grad = "#version 330\nin vec2 pos; out vec2 ndc;\nvoid main(){ gl_Position = vec4(pos, 0.999, 1.0); ndc = pos; }"
        fs_grad = """
        #version 330
        in vec2 ndc; uniform vec3 horizon; uniform vec3 zenith; out vec4 color;
        void main(){ float h = clamp(ndc.y * 0.5 + 0.5, 0.0, 1.0); color = vec4(mix(horizon, zenith, h), 1.0); }
        """
        self.gradient_prog = self.ctx.program(vertex_shader=vs_grad, fragment_shader=fs_grad)
        grad_data = np.array([[-1.0,-1.0],[3.0,-1.0],[-1.0,3.0]], dtype="f4")
        vbo_grad = self.ctx.buffer(grad_data.tobytes())
        self.gradient_vao = self.ctx.vertex_array(self.gradient_prog, [(vbo_grad, "2f", "pos")])

        obj_path = os.path.join(PROJECT_ROOT, "models", "player", "playerstartout.obj")
        if os.path.exists(obj_path):
            obj_data = load_obj_simple(obj_path)
            if obj_data is not None and len(obj_data) > 0:
                ys = obj_data[1::6]
                y_min = float(np.min(ys)); y_max = float(np.max(ys))
                h = max(0.001, y_max - y_min)
                TARGET_HEIGHT = 2.0
                scale = TARGET_HEIGHT / h
                scaled = obj_data.copy()
                scaled[0::6] *= scale
                scaled[1::6] = (scaled[1::6] - y_min) * scale
                scaled[2::6] *= scale
                self.player_obj_vbo = self.ctx.buffer(scaled.tobytes())
                self.player_obj_solid_vao = self.ctx.vertex_array(
                    self.prog_solid, [(self.player_obj_vbo, "3f 3x4", "pos")])
                n = len(obj_data) // 6
                pts = [tuple(round(float(v), 5) for v in scaled[i*6:i*6+3]) for i in range(n)]
                edges = set()
                for i in range(0, n, 3):
                    tri = [pts[i], pts[i+1], pts[i+2]]
                    for j in range(3):
                        a = tri[j]; b = tri[(j+1) % 3]
                        edges.add(tuple(sorted([a, b])))
                lines = []
                for a, b in edges:
                    lines.extend(a); lines.extend(b)
                if lines:
                    line_vbo = self.ctx.buffer(np.array(lines, dtype=np.float32).tobytes())
                    self.player_obj_lines_vao = self.ctx.vertex_array(
                        self.prog_line, [(line_vbo, "3f", "pos")])

        try:
            self.reload_sky()
        except Exception as e:
            print(f"[hammer] initial sky load failed: {e}")

    def reload_sky(self):
        if self.ctx is None:
            return
        cfg = parse_sky_cfg(self.editor.sky_name)
        self.gradient_horizon = cfg["horizon"]
        self.gradient_zenith = cfg["zenith"]
        if self.skybox_tex is not None:
            try: self.skybox_tex.release()
            except: pass
            self.skybox_tex = None
        if cfg.get("skybox"):
            self.skybox_tex = load_skybox_cubemap(
                self.ctx, cfg["skybox"],
                flip_h=cfg.get("skybox_flip_h", 1),
                flip_v=cfg.get("skybox_flip_v", 1),
                rotate_up=cfg.get("skybox_rotate_up", 0),
            )
        self._sky_loaded = True
        self.update()

    def resizeGL(self, w, h):
        if self.ctx: self.ctx.viewport = (0, 0, w, h)

    def _get_cut_vaos(self, cuts_key):
        if cuts_key is None: return None
        if cuts_key in self.cut_vaos: return self.cut_vaos[cuts_key]
        cuts = [list(c) for c in cuts_key]
        data = make_multi_cut_cube_data(cuts)
        if data is None: return None
        vbo = self.ctx.buffer(data.tobytes())
        tex_vao = self.ctx.vertex_array(self.prog_block, [(vbo, "3f 3f 2x4", "pos", "normal")])
        solid_vao = self.ctx.vertex_array(self.prog_solid, [(vbo, "3f 5x4", "pos")])
        line_data = make_multi_cut_cube_lines(cuts)
        line_vbo = self.ctx.buffer(line_data.tobytes())
        line_vao = self.ctx.vertex_array(self.prog_line, [(line_vbo, "3f", "pos")])
        result = (vbo, tex_vao, solid_vao, line_vbo, line_vao)
        self.cut_vaos[cuts_key] = result
        return result

    # ---------- 缓存管理（避免每帧重建 GPU 缓冲） ----------
    def _brush_mesh(self, blk):
        """Cached (vbo, tex_vao, solid_vao, line_vbo, line_vao, model) for a Brush."""
        key = id(blk)
        tri = blk.get_triangles()
        if len(tri) == 0:
            return None
        n = len(tri) // 8
        ud = getattr(blk, "user_data", {}) or {}
        model = ud.get("model", glm.mat4())
        try:
            msig = tuple(round(float(model[i][j]), 6)
                         for j in range(4) for i in range(4))
        except Exception:
            msig = None
        sig = (n, float(tri[0]), float(tri[-1]), msig)
        ent = self._brush_cache.get(key)
        if ent is not None and ent[0] is blk and ent[1] == sig:
            return ent[2]
        if ent is not None:
            for obj in ent[2][:5]:
                if obj is not None:
                    try: obj.release()
                    except Exception: pass
        vbo = self.ctx.buffer(tri.tobytes())
        tex_vao = self.ctx.vertex_array(
            self.prog_block, [(vbo, "3f 3f 2x4", "pos", "normal")])
        solid_vao = self.ctx.vertex_array(
            self.prog_solid, [(vbo, "3f 5x4", "pos")])
        positions = [tuple(round(float(v), 5) for v in tri[i * 8:i * 8 + 3])
                     for i in range(n)]
        edges = set()
        for i in range(0, n, 3):
            a, b, c = positions[i], positions[i + 1], positions[i + 2]
            for u, v in ((a, b), (b, c), (c, a)):
                edges.add((u, v) if u <= v else (v, u))
        lines = []
        for a, b in edges:
            lines.extend(a); lines.extend(b)
        if lines:
            line_vbo = self.ctx.buffer(np.array(lines, dtype=np.float32).tobytes())
            line_vao = self.ctx.vertex_array(self.prog_line, [(line_vbo, "3f", "pos")])
        else:
            line_vbo = None; line_vao = None
        result = (vbo, tex_vao, solid_vao, line_vbo, line_vao, model)
        self._brush_cache[key] = (blk, sig, result)
        return result

    def clear_caches(self):
        """Release every cached GPU object (call when the block list changes)."""
        for ent in list(self._terrain_cache.values()):
            for obj in ent:
                if obj is not None:
                    try: obj.release()
                    except Exception: pass
        self._terrain_cache.clear()
        for ent in list(self._brush_cache.values()):
            for obj in ent[2][:5]:
                if obj is not None:
                    try: obj.release()
                    except Exception: pass
        self._brush_cache.clear()
        for result in list(self.cut_vaos.values()):
            for obj in result:
                if obj is not None:
                    try: obj.release()
                    except Exception: pass
        self.cut_vaos.clear()
        for obj in (self.grid_vao, self.grid_vbo):
            if obj is not None:
                try: obj.release()
                except Exception: pass
        self.grid_vao = None
        self.grid_vbo = None
        self.last_grid_key = None

    def _terrain_mesh(self, t, key):
        """Cached (vbo, vao, ibo) mesh for a terrain patch."""
        ent = self._terrain_cache.get(key)
        if ent is not None:
            return ent
        try:
            from engine_pkg.world.terrain import Terrain
            heights = t.get("heights") or []
            if not heights:
                return None
            seg = int(t.get("segments", len(heights) - 1))
            tr = Terrain(t.get("pos", [0, 0, 0]),
                         t.get("half", [10, 1, 10]), seg, heights,
                         t.get("texture", ""))
            verts, normals, idx = tr.build_mesh()
            arr = np.ascontiguousarray(np.hstack([verts, normals]),
                                       dtype="f4")
            vbo = self.ctx.buffer(arr.tobytes())
            ibo = self.ctx.buffer(idx.tobytes())
            vao = self.ctx.vertex_array(
                self.prog_block, [(vbo, "3f 3f", "pos", "normal")], ibo)
            ent = (vbo, vao, ibo)
            self._terrain_cache[key] = ent
            return ent
        except Exception as ex:
            try:
                self.editor.log_message(f"Terrain preview failed: {ex}")
            except Exception:
                pass
            return None

    def prune_brush_cache(self):
        live = {id(b) for b in self.editor.blocks}
        for key in list(self._brush_cache.keys()):
            if key not in live:
                for obj in self._brush_cache[key][2][:5]:
                    if obj is not None:
                        try: obj.release()
                        except Exception: pass
                del self._brush_cache[key]

    def apply_texture_to_block(self, index):
        """Apply the current texture to one block (context-menu action)."""
        if not (0 <= index < len(self.editor.blocks)):
            return
        b = self.editor.blocks[index]
        self.editor.push_undo("Apply texture")
        if Brush and isinstance(b, Brush):
            if hasattr(b, "user_data"):
                b.user_data["texture"] = self.editor.current_texture
        else:
            while len(b) < 7:
                b.append(None)
            b[6] = self.editor.current_texture
        self.editor.mark_dirty()
        self.update()
        self.editor.refresh_ui_lists()
        self.editor.set_status(self.editor.texts.get("selected_tex", "Selected texture: {}")
                               .format(self.editor.current_texture))

    def _get_text_texture(self, text):
        if text in self.text_textures: return self.text_textures[text]
        try:
            font = pygame.font.SysFont("Arial", 20, bold=True)
        except Exception:
            font = pygame.font.Font(None, 24)
        surf = font.render(text, True, (255, 255, 255))
        w, h = surf.get_size()
        bg = pygame.Surface((w + 8, h + 4), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 170)); bg.blit(surf, (4, 2))
        bw, bh = bg.get_size()
        data = pygame.image.tobytes(bg, "RGBA", True)
        try:
            tex = self.ctx.texture((bw, bh), 4, data)
            tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.text_textures[text] = (tex, bw, bh)
            return self.text_textures[text]
        except Exception: return None

    def _enter_mouse_look(self):
        if self.mouse_look_active: return
        self.mouse_look_active = True
        self.setCursor(Qt.BlankCursor)
        self.setFocus()
        center = self.mapToGlobal(self.rect().center())
        QCursor.setPos(center)
        self._last_mouse_pos = center
        self.move_timer.start()
        self.update()

    def _exit_mouse_look(self):
        if not self.mouse_look_active: return
        self.mouse_look_active = False
        self.move_timer.stop()
        try: self.releaseMouse()
        except: pass
        try: self.releaseKeyboard()
        except: pass
        self.setCursor(Qt.ArrowCursor)
        self._last_mouse_pos = None
        self.update()

    def _get_texture(self, name):
        if name in self.texture_cache: return self.texture_cache[name]
        path = material_full_path(name)
        tex = None
        if os.path.exists(path):
            try:
                surf = pygame.image.load(path)
                try: surf = surf.convert_alpha()
                except: surf = surf.convert()
                data = pygame.image.tobytes(surf, "RGBA", True)
                w, h = surf.get_size()
                tex = self.ctx.texture((w, h), 4, data)
            except Exception as e:
                print("[hammer] texture load fail:", name, e)
        if tex is None:
            size = 64
            img = np.zeros((size, size, 4), dtype=np.uint8)
            for y in range(size):
                for x in range(size):
                    img[y, x] = [255,0,255,255] if ((x//8+y//8)%2==0) else [30,30,30,255]
            tex = self.ctx.texture((size, size), 4, img.tobytes())
        tex.build_mipmaps()
        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        tex.repeat_x = True; tex.repeat_y = True
        self.texture_cache[name] = tex
        return tex

    def _camera_vectors(self):
        return np.array([np.cos(self.pitch)*np.sin(self.yaw), np.sin(self.pitch), np.cos(self.pitch)*np.cos(self.yaw)], dtype=np.float32)

    def _make_proj_view(self):
        w = max(1, self.width()); h = max(1, self.height())
        aspect = w / h
        if self.view_type == "perspective":
            eye = glm.vec3(self.cam_x, self.cam_y, self.cam_z)
            center = eye + glm.vec3(*self._camera_vectors())
            view = glm.lookAt(eye, center, glm.vec3(0, 1, 0))
            proj = glm.perspective(glm.radians(70), aspect, 0.1, 2000)
            return proj * view, proj, view
        if aspect >= 1.0:
            half_w = self.scale * aspect; half_h = self.scale
        else:
            half_w = self.scale; half_h = self.scale / aspect
        left = self.offset_x - half_w; right = self.offset_x + half_w
        bottom = self.offset_y - half_h; top = self.offset_y + half_h
        proj = glm.ortho(left, right, bottom, top, -2000, 2000)
        if self.view_type == "top":
            view = glm.lookAt(glm.vec3(0, 200, 0), glm.vec3(0, 0, 0), glm.vec3(0, 0, -1))
        elif self.view_type == "front":
            view = glm.lookAt(glm.vec3(0, 0, 200), glm.vec3(0, 0, 0), glm.vec3(0, 1, 0))
        else:
            view = glm.lookAt(glm.vec3(200, 0, 0), glm.vec3(0, 0, 0), glm.vec3(0, 1, 0))
        return proj * view, proj, view

    def _make_mvp(self):
        mvp, _, _ = self._make_proj_view()
        return mvp

    def _project_to_screen(self, world_pos, proj_view, w, h):
        try:
            clip = proj_view * glm.vec4(float(world_pos[0]), float(world_pos[1]), float(world_pos[2]), 1.0)
        except Exception: return None
        if clip.w <= 0.001: return None
        return ((clip.x/clip.w * 0.5 + 0.5) * w,
                (1.0 - (clip.y/clip.w * 0.5 + 0.5)) * h)

    def _update_wasd(self):
        if self.view_type != "perspective": return
        speed = 1.2
        fwd = self._camera_vectors()
        right = np.cross(fwd, np.array([0,1,0], dtype=np.float32))
        n = np.linalg.norm(right)
        if n > 1e-6: right /= n
        move = np.zeros(3, dtype=np.float32)
        if Qt.Key_W in self.keys_down: move += fwd
        if Qt.Key_S in self.keys_down: move -= fwd
        if Qt.Key_D in self.keys_down: move += right
        if Qt.Key_A in self.keys_down: move -= right
        if Qt.Key_Space in self.keys_down: move += np.array([0,1,0], dtype=np.float32)
        if Qt.Key_Shift in self.keys_down: move -= np.array([0,1,0], dtype=np.float32)
        if np.linalg.norm(move) > 1e-6:
            move /= np.linalg.norm(move)
            self.cam_x += float(move[0] * speed)
            self.cam_y += float(move[1] * speed)
            self.cam_z += float(move[2] * speed)
            self.update()

    def keyPressEvent(self, event):
        if self.mouse_look_active and event.key() in (Qt.Key_Z, Qt.Key_Escape):
            self._exit_mouse_look(); return
        if event.key() == Qt.Key_Delete:
            self.editor.delete_selection()
            return
        if event.key() == Qt.Key_Escape:
            self.editor._on_escape()
            return
        self.keys_down.add(event.key())
        if (self.view_type == "perspective"
                and event.key() in MOVE_KEYS):
            self.move_timer.start()
        self._update_wasd()

    def keyReleaseEvent(self, event):
        self.keys_down.discard(event.key())
        if not (self.keys_down & MOVE_KEYS):
            self.move_timer.stop()

    def _draw_screen_rect(self, sx, sy, sw, sh, color, w, h):
        self.prog_overlay["u_rect"].value = (
            (sx / w) * 2.0 - 1.0, 1.0 - ((sy + sh) / h) * 2.0,
            (sw / w) * 2.0, (sh / h) * 2.0)
        self.prog_overlay["u_color"].value = color
        self.vao_overlay_quad.render(moderngl.TRIANGLES)

    def _draw_screen_text(self, sx, sy, tex, tex_w, tex_h, w, h):
        self.prog_text["u_rect"].value = (
            (sx / w) * 2.0 - 1.0, 1.0 - ((sy + tex_h) / h) * 2.0,
            (tex_w / w) * 2.0, (tex_h / h) * 2.0)
        tex.use(0); self.prog_text["u_tex"].value = 0
        self.vao_text_quad.render(moderngl.TRIANGLES)

    def _cut_corner_world(self, blk, ci):
        """获取方块角的世界坐标（仅支持旧格式 list）"""
        if Brush and isinstance(blk, Brush):
            return None
        x, y, z, sx, sy, sz = blk[:6]
        cx, cy, cz = CUBE_CORNERS[ci]
        return (x + cx * sx, y + cy * sy, z + cz * sz)

    def _cut_face_centers(self, blk):
        """获取方块面中心的世界坐标（仅支持旧格式 list）"""
        if Brush and isinstance(blk, Brush):
            return []
        x, y, z, sx, sy, sz = blk[:6]
        return [
            (0, +1, (x + sx, y, z)),
            (0, -1, (x - sx, y, z)),
            (1, +1, (x, y + sy, z)),
            (1, -1, (x, y - sy, z)),
            (2, +1, (x, y, z + sz)),
            (2, -1, (x, y, z - sz)),
        ]

    def _pick_cut_handle(self, blk, mx, my, proj_view, w, h):
        tol = 14.0
        best_d = tol
        best = (None, -1)
        for ci in range(8):
            pos = self._cut_corner_world(blk, ci)
            s = self._project_to_screen(pos, proj_view, w, h)
            if s is None: continue
            d = ((s[0]-mx)**2 + (s[1]-my)**2) ** 0.5
            if d < best_d:
                best_d = d; best = ("corner", ci)
        for fi, (_ax, _sg, pos) in enumerate(self._cut_face_centers(blk)):
            s = self._project_to_screen(pos, proj_view, w, h)
            if s is None: continue
            d = ((s[0]-mx)**2 + (s[1]-my)**2) ** 0.5
            if d < best_d:
                best_d = d; best = ("face", fi)
        return best

    def paintGL(self):
        if self.ctx is None: return
        w = self.width(); h = self.height()
        self.ctx.viewport = (0, 0, w, h)
        self.ctx.clear(*self.viewport_bg, 1.0)

        proj_view, proj, view = self._make_proj_view()
        mvp = proj_view

        if self.view_type == "perspective":
            self.ctx.disable(moderngl.DEPTH_TEST)
            self.ctx.disable(moderngl.CULL_FACE)
            self.ctx.depth_mask = False
            if self.skybox_tex is not None:
                view_rot = glm.mat4(glm.mat3(view))
                self.skybox_prog["proj"].write(proj.to_bytes())
                self.skybox_prog["view_rot"].write(view_rot.to_bytes())
                self.skybox_tex.use(0); self.skybox_prog["sky_cube"].value = 0
                self.skybox_vao.render()
            else:
                self.gradient_prog["horizon"].value = self.gradient_horizon
                self.gradient_prog["zenith"].value = self.gradient_zenith
                self.gradient_vao.render()
            self.ctx.depth_mask = True
            self.ctx.enable(moderngl.CULL_FACE)
            self.ctx.enable(moderngl.DEPTH_TEST)

        if self.view_type != "perspective" and self.show_grid:
            self._ensure_grid()
            if self.grid_vao is not None:
                self.prog_line["line_color"].value = (
                    self.grid_color[0], self.grid_color[1], self.grid_color[2], 1.0)
                self.prog_line["mvp"].write(mvp.to_bytes())
                self.grid_vao.render(moderngl.LINES)

        # ---- Pass 1: 普通方块 ----
        wire = bool(getattr(self, "wireframe", False))
        if wire:
            try:
                self.ctx.polygon_mode = moderngl.LINE
            except Exception:
                wire = False
        # In the 2D orthographic views the solids are drawn as wireframe
        # outlines with a centre X instead of textured faces.
        for blk in (() if self.view_type != "perspective" else self.editor.blocks):
            if Brush and isinstance(blk, Brush):
                # 兼容旧版保存的 Brush 对象
                is_water = blk.user_data.get("is_water", False) if hasattr(blk, 'user_data') else False
                if is_water:
                    continue
                tex_name = blk.user_data.get("texture", "dev.png") if hasattr(blk, 'user_data') else "dev.png"
                entry = self._brush_mesh(blk)
                if entry is None:
                    continue
                tex = self._get_texture(tex_name)
                tex.use(0); self.prog_block["tex"].value = 0
                self.prog_block["proj_view"].write(proj_view.to_bytes())
                self.prog_block["model"].write(entry[5].to_bytes())
                entry[1].render(moderngl.TRIANGLES)
                continue
            
            # 旧格式：list [x, y, z, sx, sy, sz, tex, cuts, is_water]
            if not isinstance(blk, list):
                continue
            is_water = (len(blk) > 8 and blk[8]) or (len(blk) > 6 and is_water_texture(blk[6]))
            if is_water:
                continue
            x, y, z, sx, sy, sz, tex_name = blk[:7]
            cuts = blk[7] if len(blk) > 7 else None
            cuts_key = cuts_to_key(cuts)
            model = glm.translate(glm.mat4(), glm.vec3(x, y, z))
            model = glm.scale(model, glm.vec3(sx, sy, sz))
            tex = self._get_texture(tex_name)
            tex.use(0); self.prog_block["tex"].value = 0
            self.prog_block["proj_view"].write(proj_view.to_bytes())
            self.prog_block["model"].write(model.to_bytes())
            if cuts_key is not None:
                entry = self._get_cut_vaos(cuts_key)
                if entry is not None: entry[1].render(moderngl.TRIANGLES)
                else: self.vao_unit.render(moderngl.TRIANGLES)
            else:
                self.vao_unit.render(moderngl.TRIANGLES)

        # ---- Pass 1.5: smooth terrain (height-field meshes) ----
        if self.view_type == "perspective":
            for ti, t in enumerate(getattr(self.editor, "terrains", [])):
                if not isinstance(t, dict):
                    continue
                hs = t.get("heights") or []
                key = (ti, len(hs), tuple(t.get("pos", (0.0, 0.0, 0.0))))
                entry = self._terrain_mesh(t, key)
                if entry is None:
                    continue
                try:
                    tex_name = t.get("texture") or "nature/meadow_grass.png"
                    tex = self._get_texture(tex_name)
                    tex.use(0)
                    self.prog_block["tex"].value = 0
                    self.prog_block["proj_view"].write(proj_view.to_bytes())
                    model = glm.translate(
                        glm.mat4(), glm.vec3(*[float(v) for v in
                                              t.get("pos", [0, 0, 0])]))
                    self.prog_block["model"].write(model.to_bytes())
                    entry[1].render(moderngl.TRIANGLES)
                except Exception:
                    pass

        # ---- Pass 2: 水方块（半透明蓝）----
        has_water = False
        for blk in self.editor.blocks:
            if Brush and isinstance(blk, Brush):
                is_water = blk.user_data.get("is_water", False) if hasattr(blk, 'user_data') else False
            elif isinstance(blk, list):
                is_water = (len(blk) > 8 and blk[8]) or (len(blk) > 6 and is_water_texture(blk[6]))
            else:
                is_water = False
            if is_water:
                has_water = True
                break
        
        if has_water and self.view_type == "perspective":
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
            self.ctx.depth_mask = False
            self.prog_solid["solid_color"].value = (0.25, 0.55, 0.85, 0.55)
            for blk in self.editor.blocks:
                if Brush and isinstance(blk, Brush):
                    is_water = blk.user_data.get("is_water", False) if hasattr(blk, 'user_data') else False
                    if not is_water:
                        continue
                    entry = self._brush_mesh(blk)
                    if entry is None:
                        continue
                    self.prog_solid["proj_view"].write(proj_view.to_bytes())
                    self.prog_solid["model"].write(entry[5].to_bytes())
                    entry[2].render(moderngl.TRIANGLES)
                    continue
                
                # 旧格式
                if not isinstance(blk, list):
                    continue
                is_water = (len(blk) > 8 and blk[8]) or (len(blk) > 6 and is_water_texture(blk[6]))
                if not is_water:
                    continue
                x, y, z, sx, sy, sz = blk[:6]
                cuts = blk[7] if len(blk) > 7 else None
                cuts_key = cuts_to_key(cuts)
                model = glm.translate(glm.mat4(), glm.vec3(x, y, z))
                model = glm.scale(model, glm.vec3(sx, sy, sz))
                self.prog_solid["proj_view"].write(proj_view.to_bytes())
                self.prog_solid["model"].write(model.to_bytes())
                if cuts_key is not None:
                    entry = self._get_cut_vaos(cuts_key)
                    if entry is not None:
                        entry[2].render(moderngl.TRIANGLES)
                        continue
                self.vao_unit_solid.render(moderngl.TRIANGLES)
            self.ctx.depth_mask = True
            self.ctx.disable(moderngl.BLEND)

        # ---- 2D views: wireframe solids + centre X markers ----
        if self.view_type != "perspective":
            self._draw_2d_view(mvp)
            self._draw_resize_handles(proj_view)

        # 选中方块白框（支持多选）
        sel_blocks = self.editor.selected_block_indices
        if sel_blocks and self.editor.tool_mode != "cut" and self.view_type == "perspective":
            for idx in sel_blocks:
                if not (0 <= idx < len(self.editor.blocks)):
                    continue
                b = self.editor.blocks[idx]
                if Brush and isinstance(b, Brush):
                    entry = self._brush_mesh(b)
                    if entry is not None and entry[4] is not None:
                        self.prog_line["line_color"].value = (1.0, 1.0, 1.0, 1.0)
                        self.prog_line["mvp"].write((mvp * entry[5]).to_bytes())
                        entry[4].render(moderngl.LINES)
                else:
                    x, y, z, sx, sy, sz = b[:6]
                    cuts = b[7] if len(b) > 7 else None
                    cuts_key = cuts_to_key(cuts)
                    if cuts_key is not None:
                        entry = self._get_cut_vaos(cuts_key)
                        if entry is not None:
                            model = glm.translate(glm.mat4(), glm.vec3(x, y, z))
                            model = glm.scale(model, glm.vec3(sx*1.01, sy*1.01, sz*1.01))
                            self.prog_line["line_color"].value = (1.0, 1.0, 1.0, 1.0)
                            self.prog_line["mvp"].write((mvp * model).to_bytes())
                            entry[4].render(moderngl.LINES)
                    else:
                        model = glm.translate(glm.mat4(), glm.vec3(x, y, z))
                        model = glm.scale(model, glm.vec3(sx*1.01, sy*1.01, sz*1.01))
                        self.prog_line["line_color"].value = (1.0, 1.0, 1.0, 1.0)
                        self.prog_line["mvp"].write((mvp * model).to_bytes())
                        self.vao_lines.render(moderngl.LINES)

        # 2D 视图中的 cut 轮廓
        if self.view_type != "perspective":
            for blk in self.editor.blocks:
                if Brush and isinstance(blk, Brush):
                    continue
                cuts = blk[7] if len(blk) > 7 else None
                if not cuts: continue
                cuts_key = cuts_to_key(cuts)
                if cuts_key is None: continue
                entry = self._get_cut_vaos(cuts_key)
                if entry is None: continue
                x, y, z, sx, sy, sz = blk[:6]
                model = glm.translate(glm.mat4(), glm.vec3(x, y, z))
                model = glm.scale(model, glm.vec3(sx, sy, sz))
                self.prog_line["line_color"].value = (1.0, 0.35, 0.15, 1.0)
                self.prog_line["mvp"].write((mvp * model).to_bytes())
                entry[4].render(moderngl.LINES)

        # 实体
        for ent in self.editor.entities:
            _etype, x, y, z = ent[:4]
            cuts = ent[4] if len(ent) > 4 else None
            cuts_key = cuts_to_key(cuts)
            if _etype == "info_player_start" and self.player_obj_solid_vao is not None:
                model = glm.translate(glm.mat4(), glm.vec3(x, y, z))
                self.prog_solid["solid_color"].value = (0.20, 0.85, 0.35, 1.0)
                self.prog_solid["proj_view"].write(proj_view.to_bytes())
                self.prog_solid["model"].write(model.to_bytes())
                self.player_obj_solid_vao.render(moderngl.TRIANGLES)
            else:
                model = glm.translate(glm.mat4(), glm.vec3(x, y + 0.6, z))
                model = glm.scale(model, glm.vec3(0.45, 1.2, 0.45))
                edef = ENTITY_DEFS.get(_etype, {})
                col = edef.get("color", (220, 80, 80))
                self.prog_solid["solid_color"].value = (
                    col[0] / 255.0, col[1] / 255.0, col[2] / 255.0, 1.0)
                self.prog_solid["proj_view"].write(proj_view.to_bytes())
                self.prog_solid["model"].write(model.to_bytes())
                if cuts_key is not None:
                    entry = self._get_cut_vaos(cuts_key)
                    if entry is not None: entry[2].render(moderngl.TRIANGLES)
                    else: self.vao_unit_solid.render(moderngl.TRIANGLES)
                else:
                    self.vao_unit_solid.render(moderngl.TRIANGLES)

        for idx in self.editor.selected_entity_indices:
            if 0 <= idx < len(self.editor.entities):
                e = self.editor.entities[idx]
                _etype, x, y, z = e[:4]
                model = glm.translate(glm.mat4(), glm.vec3(x, y + 0.6, z))
                model = glm.scale(model, glm.vec3(0.5, 1.26, 0.5))
                self.prog_line["line_color"].value = (1.0, 1.0, 1.0, 1.0)
                self.prog_line["mvp"].write((mvp * model).to_bytes())
                self.vao_lines.render(moderngl.LINES)

        sp = self.editor.spawn
        has_obj_spawn = any(ent[0] == "info_player_start" for ent in self.editor.entities)
        if not has_obj_spawn:
            model = glm.translate(glm.mat4(), glm.vec3(float(sp[0]), 0.5, float(sp[1])))
            model = glm.scale(model, glm.vec3(0.4, 0.5, 0.4))
            self.prog_line["line_color"].value = (0.2, 1.0, 0.2, 1.0)
            self.prog_line["mvp"].write((mvp * model).to_bytes())
            self.vao_lines.render(moderngl.LINES)

        # 拖拽预览
        if self.editor.tool_mode in ("block", "entity") and self.is_dragging and self.drag_world_start and self.drag_world_end:
            a = np.array(self.drag_world_start, dtype=np.float32)
            b = np.array(self.drag_world_end, dtype=np.float32)
            if self.editor.tool_mode == "block":
                center = (a + b) * 0.5
                half = np.maximum(np.abs(b - a) * 0.5, np.array([0.5, 0.5, 0.5]))
                model = glm.translate(glm.mat4(), glm.vec3(*center))
                model = glm.scale(model, glm.vec3(*half))
                
                if self.editor.current_is_water:
                    self.prog_line["line_color"].value = (0.3, 0.7, 1.0, 1.0)
                else:
                    self.prog_line["line_color"].value = (1.0, 0.9, 0.2, 1.0)
                self.prog_line["mvp"].write((mvp * model).to_bytes())
                self.vao_lines.render(moderngl.LINES)
            else:
                model = glm.translate(glm.mat4(), glm.vec3(b[0], b[1] + 0.6, b[2]))
                model = glm.scale(model, glm.vec3(0.5, 1.26, 0.5))
                self.prog_line["line_color"].value = (1.0, 0.9, 0.2, 1.0)
                self.prog_line["mvp"].write((mvp * model).to_bytes())
                self.vao_lines.render(moderngl.LINES)

        # Cut 手柄
        if self.editor.tool_mode == "cut" and self.cut_selected_idx >= 0:
            idx = self.cut_selected_idx
            if 0 <= idx < len(self.editor.blocks):
                blk = self.editor.blocks[idx]
                if Brush and isinstance(blk, Brush):
                    pass
                else:
                    x, y, z, sx, sy, sz = blk[:6]
                    model = glm.translate(glm.mat4(), glm.vec3(x, y, z))
                    model = glm.scale(model, glm.vec3(sx*1.01, sy*1.01, sz*1.01))
                    self.prog_line["line_color"].value = (1.0, 1.0, 1.0, 1.0)
                    self.prog_line["mvp"].write((mvp * model).to_bytes())
                    self.vao_lines.render(moderngl.LINES)

                    self.ctx.disable(moderngl.DEPTH_TEST)
                    self.ctx.disable(moderngl.CULL_FACE)
                    self.ctx.disable(moderngl.BLEND)
                    for ci in range(8):
                        pos = self._cut_corner_world(blk, ci)
                        s = self._project_to_screen(pos, proj_view, w, h)
                        if s is None: continue
                        self._draw_screen_rect(s[0]-5, s[1]-5, 10, 10, (1.0, 1.0, 1.0, 1.0), w, h)
                        self._draw_screen_rect(s[0]-3, s[1]-3, 6, 6, (0.2, 0.2, 0.2, 1.0), w, h)
                    for fi, (_ax, _sg, pos) in enumerate(self._cut_face_centers(blk)):
                        s = self._project_to_screen(pos, proj_view, w, h)
                        if s is None: continue
                        self._draw_screen_rect(s[0]-5, s[1]-5, 10, 10, (1.0, 1.0, 0.2, 1.0), w, h)
                        self._draw_screen_rect(s[0]-3, s[1]-3, 6, 6, (0.6, 0.6, 0.0, 1.0), w, h)

                    self.ctx.enable(moderngl.CULL_FACE)
                    self.ctx.enable(moderngl.DEPTH_TEST)

        if wire:
            try:
                self.ctx.polygon_mode = moderngl.FILL
            except Exception:
                pass

        # 准星
        if self.mouse_look_active and self.view_type == "perspective":
            self.ctx.disable(moderngl.DEPTH_TEST)
            self.ctx.disable(moderngl.CULL_FACE)
            self.ctx.disable(moderngl.BLEND)
            cx = w * 0.5; cy = h * 0.5
            self._draw_screen_rect(cx-14, cy-1, 28, 2, (1.0, 1.0, 1.0, 1.0), w, h)
            self._draw_screen_rect(cx-1, cy-14, 2, 28, (1.0, 1.0, 1.0, 1.0), w, h)
            self._draw_screen_rect(cx-2, cy-2, 4, 4, (1.0, 1.0, 1.0, 1.0), w, h)

        # 实体标签
        if self.view_type == "perspective":
            for ent in self.editor.entities:
                etype, x, y, z = ent[:4]
                screen = self._project_to_screen((x, y + 2.3, z), proj_view, w, h)
                if screen is None: continue
                label = "player" if etype == "info_player_start" else etype
                tt = self._get_text_texture(label)
                if tt is None: continue
                tex, tw, th = tt
                self.ctx.disable(moderngl.DEPTH_TEST)
                self.ctx.disable(moderngl.CULL_FACE)
                self.ctx.enable(moderngl.BLEND)
                self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
                self._draw_screen_text(screen[0]-tw*0.5, screen[1]-th*0.5, tex, tw, th, w, h)
            self.ctx.disable(moderngl.BLEND)
            self.ctx.enable(moderngl.CULL_FACE)
            self.ctx.enable(moderngl.DEPTH_TEST)

    def _ensure_grid(self):
        step = max(1, int(self.grid_step))
        w = max(1, self.width()); h = max(1, self.height())
        aspect = w / h
        if aspect >= 1.0:
            half_w = self.scale * aspect; half_h = self.scale
        else:
            half_w = self.scale; half_h = self.scale / aspect
        # Snap the visible bounds to grid lines: the buffer is then rebuilt only
        # when the view crosses a line instead of on every pan pixel.
        a0 = int((self.offset_x - half_w) // step) * step
        a1 = int((self.offset_x + half_w) // step + 1) * step
        b0 = int((self.offset_y - half_h) // step) * step
        b1 = int((self.offset_y + half_h) // step + 1) * step
        key = (a0, a1, b0, b1, step, self.view_type)
        if key == self.last_grid_key:
            return
        self.last_grid_key = key
        lines = []
        rng_a = range(a0, a1 + step, step)
        rng_b = range(b0, b1 + step, step)
        if self.view_type == "top":
            for i in rng_a:
                lines.extend([i, 0, b0, i, 0, b1])
            for j in rng_b:
                lines.extend([a0, 0, j, a1, 0, j])
        elif self.view_type == "front":
            for i in rng_a:
                lines.extend([i, b0, 0, i, b1, 0])
            for j in rng_b:
                lines.extend([a0, j, 0, a1, j, 0])
        else:  # side
            for i in rng_a:
                lines.extend([0, b0, i, 0, b1, i])
            for j in rng_b:
                lines.extend([0, j, a0, 0, j, a1])
        arr = np.array(lines, dtype=np.float32)
        if self.grid_vbo: self.grid_vbo.release()
        if self.grid_vao: self.grid_vao.release()
        self.grid_vbo = self.ctx.buffer(arr.tobytes())
        self.grid_vao = self.ctx.vertex_array(self.prog_line, [(self.grid_vbo, "3f", "pos")])

    def _screen_to_world_on_plane(self, mx, my, plane="y", plane_val=0.0):
        w = max(1, self.width()); h = max(1, self.height())
        ndc_x = (2.0 * mx / w) - 1.0
        ndc_y = 1.0 - (2.0 * my / h)
        inv = glm.inverse(self._make_mvp())
        p0 = inv * glm.vec4(ndc_x, ndc_y, -1.0, 1.0)
        p1 = inv * glm.vec4(ndc_x, ndc_y, 1.0, 1.0)
        p0 /= p0.w; p1 /= p1.w
        d = p1 - p0
        if plane == "y":
            if abs(d.y) < 1e-6: return None
            t = (plane_val - p0.y) / d.y
        elif plane == "z":
            if abs(d.z) < 1e-6: return None
            t = (plane_val - p0.z) / d.z
        else:
            if abs(d.x) < 1e-6: return None
            t = (plane_val - p0.x) / d.x
        hit = p0 + d * t
        return (float(hit.x), float(hit.y), float(hit.z))

    def _screen_ray(self, mx, my):
        w = max(1, self.width()); h = max(1, self.height())
        ndc_x = (2.0 * mx / w) - 1.0
        ndc_y = 1.0 - (2.0 * my / h)
        inv = glm.inverse(self._make_mvp())
        p_far = inv * glm.vec4(ndc_x, ndc_y, 1.0, 1.0); p_far /= p_far.w
        origin = glm.vec3(self.cam_x, self.cam_y, self.cam_z)
        d = glm.vec3(p_far.x - origin.x, p_far.y - origin.y, p_far.z - origin.z)
        dl = glm.length(d)
        if dl < 1e-6: return origin, glm.vec3(0, 0, -1)
        return origin, d / dl

    @staticmethod
    def _ray_aabb(origin, dirv, bmin, bmax):
        tmin = -1e30; tmax = 1e30
        o = (origin.x, origin.y, origin.z); d = (dirv.x, dirv.y, dirv.z)
        for i in range(3):
            if abs(d[i]) < 1e-8:
                if o[i] < bmin[i] or o[i] > bmax[i]: return None
            else:
                inv = 1.0 / d[i]
                t1 = (bmin[i] - o[i]) * inv
                t2 = (bmax[i] - o[i]) * inv
                if t1 > t2: t1, t2 = t2, t1
                if t1 > tmin: tmin = t1
                if t2 < tmax: tmax = t2
                if tmin > tmax: return None
        if tmax < 0: return None
        return tmin if tmin > 0 else tmax

    def _snap(self, v, step):
        if not self.snap_enabled:
            return float(v)
        return round(v / step) * step

    def _world_for_mouse(self, mx, my):
        if self.view_type == "top":
            p = self._screen_to_world_on_plane(mx, my, "y", 0.0)
            if p is None: return (0.0, 0.0, 0.0)
            return (self._snap(p[0], self.grid_step), 0.0, self._snap(p[2], self.grid_step))
        if self.view_type == "front":
            p = self._screen_to_world_on_plane(mx, my, "z", 0.0)
            if p is None: return (0.0, 0.0, 0.0)
            return (self._snap(p[0], self.grid_step), self._snap(p[1], self.grid_step), 0.0)
        if self.view_type == "side":
            p = self._screen_to_world_on_plane(mx, my, "x", 0.0)
            if p is None: return (0.0, 0.0, 0.0)
            return (0.0, self._snap(p[1], self.grid_step), self._snap(p[2], self.grid_step))
        p = self._screen_to_world_on_plane(mx, my, "y", 0.0)
        if p is None: return (0.0, 0.0, 0.0)
        return (self._snap(p[0], self.grid_step), 0.0, self._snap(p[2], self.grid_step))

    def _world_for_drag(self, mx, my, obj_pos):
        if self.view_type in ("perspective", "top"):
            p = self._screen_to_world_on_plane(mx, my, "y", float(obj_pos[1]))
        elif self.view_type == "front":
            p = self._screen_to_world_on_plane(mx, my, "z", float(obj_pos[2]))
        elif self.view_type == "side":
            p = self._screen_to_world_on_plane(mx, my, "x", float(obj_pos[0]))
        else:
            return None
        if p is None:
            return None
        return (self._snap(p[0], self.grid_step),
                self._snap(p[1], self.grid_step),
                self._snap(p[2], self.grid_step))

    def _block_bounds(self, b):
        """(cx, cy, cz, sx, sy, sz) for a Brush or legacy list block."""
        if Brush and isinstance(b, Brush):
            if hasattr(b, "user_data") and "model" in b.user_data:
                m = b.user_data["model"]
                cx = float(m[3][0]); cy = float(m[3][1]); cz = float(m[3][2])
                sx = float(glm.length(glm.vec3(m[0][0], m[1][0], m[2][0])))
                sy = float(glm.length(glm.vec3(m[0][1], m[1][1], m[2][1])))
                sz = float(glm.length(glm.vec3(m[0][2], m[1][2], m[2][2])))
            else:
                try:
                    mn, mxb = b.get_aabb()
                    c = (mn + mxb) * 0.5
                    h = (mxb - mn) * 0.5
                    cx, cy, cz = float(c[0]), float(c[1]), float(c[2])
                    sx, sy, sz = float(h[0]), float(h[1]), float(h[2])
                except Exception:
                    return None
            return (cx, cy, cz, sx, sy, sz)
        if isinstance(b, list) and len(b) >= 6:
            return (float(b[0]), float(b[1]), float(b[2]),
                    float(b[3]), float(b[4]), float(b[5]))
        return None

    def _world_per_pixel(self):
        h = max(1, self.height())
        return (2.0 * float(getattr(self, "scale", 20.0))) / h

    def _draw_2d_view(self, mvp):
        """Wireframe solids + centre X markers for the orthographic views."""
        wpp = self._world_per_pixel()
        mark = max(0.28, wpp * 7.0)
        sel_blocks = set(self.editor.selected_block_indices)
        # The X lies in the view plane: XZ (top), XY (front), ZY (side).
        if self.view_type == "front":
            rot = glm.rotate(glm.mat4(1.0), glm.radians(90.0), glm.vec3(1, 0, 0))
        elif self.view_type == "side":
            rot = glm.rotate(glm.mat4(1.0), glm.radians(90.0), glm.vec3(0, 0, 1))
        else:
            rot = glm.mat4(1.0)
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.CULL_FACE)
        for i, blk in enumerate(self.editor.blocks):
            bd = self._block_bounds(blk)
            if bd is None:
                continue
            cx, cy, cz, sx, sy, sz = bd
            sel = i in sel_blocks
            self.prog_line["line_color"].value = (
                (1.0, 0.62, 0.15, 1.0) if sel else (0.62, 0.70, 0.80, 1.0))
            model = glm.translate(glm.mat4(), glm.vec3(cx, cy, cz))
            model = glm.scale(model, glm.vec3(sx, sy, sz))
            self.prog_line["mvp"].write((mvp * model).to_bytes())
            self.vao_lines.render(moderngl.LINES)
            self.prog_line["line_color"].value = (
                (1.0, 0.32, 0.18, 1.0) if sel else (0.98, 0.78, 0.25, 1.0))
            xm = glm.translate(glm.mat4(), glm.vec3(cx, cy, cz))
            xm = xm * rot
            xm = glm.scale(xm, glm.vec3(mark, 1.0, mark))
            self.prog_line["mvp"].write((mvp * xm).to_bytes())
            self.vao_xmark.render(moderngl.LINES)
        for t in getattr(self.editor, "terrains", []):
            try:
                cx, cy, cz = [float(v) for v in t.get("pos", [0, 0, 0])]
                hx, hy, hz = [float(v) for v in t.get("half", [1, 1, 1])]
            except Exception:
                continue
            self.prog_line["line_color"].value = (0.35, 0.88, 0.45, 1.0)
            model = glm.translate(glm.mat4(), glm.vec3(cx, cy, cz))
            model = glm.scale(model, glm.vec3(hx, max(hy, 0.02), hz))
            self.prog_line["mvp"].write((mvp * model).to_bytes())
            self.vao_lines.render(moderngl.LINES)
        for i, e in enumerate(self.editor.entities):
            try:
                ex, ey, ez = float(e[1]), float(e[2]), float(e[3])
            except Exception:
                continue
            sel = ("entity", i) in self.editor.selection
            self.prog_line["line_color"].value = (
                (1.0, 0.32, 0.18, 1.0) if sel else (0.45, 0.95, 0.55, 1.0))
            xm = glm.translate(glm.mat4(), glm.vec3(ex, ey, ez))
            xm = xm * rot
            xm = glm.scale(xm, glm.vec3(mark, 1.0, mark))
            self.prog_line["mvp"].write((mvp * xm).to_bytes())
            self.vao_xmark.render(moderngl.LINES)
        self.ctx.enable(moderngl.DEPTH_TEST)

    # ---------------- resize handles (2D views) ----------------
    def _selected_block(self):
        sel = self.editor.selected_block_indices
        if len(sel) != 1:
            return -1, None
        i = sel[0]
        if not (0 <= i < len(self.editor.blocks)):
            return -1, None
        b = self.editor.blocks[i]
        if not isinstance(b, list) or len(b) < 6:
            return -1, None
        return i, b

    def _resize_axes(self):
        if self.view_type == "front":
            return ("x", "y")
        if self.view_type == "side":
            return ("y", "z")
        return ("x", "z")

    def _resize_handles(self, b):
        cx, cy, cz, sx, sy, sz = (float(v) for v in b[:6])
        ax_u, ax_v = self._resize_axes()
        out = []
        for ku in (-1, 0, 1):
            for kv in (-1, 0, 1):
                if ku == 0 and kv == 0:
                    continue
                pos = {"x": cx, "y": cy, "z": cz}
                pos[ax_u] += ku * {"x": sx, "y": sy, "z": sz}[ax_u]
                pos[ax_v] += kv * {"x": sx, "y": sy, "z": sz}[ax_v]
                out.append(((ku, kv), (pos["x"], pos["y"], pos["z"])))
        return out

    def _draw_resize_handles(self, proj_view):
        i, b = self._selected_block()
        if b is None:
            return
        w = max(1, self.width()); h = max(1, self.height())
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.disable(moderngl.CULL_FACE)
        self.ctx.disable(moderngl.BLEND)
        for _key, pos in self._resize_handles(b):
            s = self._project_to_screen(pos, proj_view, w, h)
            if s is None:
                continue
            self._draw_screen_rect(s[0] - 4, s[1] - 4, 8, 8,
                                   (1.0, 1.0, 1.0, 1.0), w, h)
        # dimension readouts, like the reference
        cx, cy, cz, sx, sy, sz = (float(v) for v in b[:6])
        ax_u, ax_v = self._resize_axes()
        su = {"x": sx, "y": sy, "z": sz}[ax_u]
        sv = {"x": sx, "y": sy, "z": sz}[ax_v]
        mu = {"x": cx, "y": cy, "z": cz}
        mv = {"x": cx, "y": cy, "z": cz}
        mu[ax_u] += su; mv[ax_v] += sv
        labels = ((f"{2.0 * su:.1f}", (mu["x"], mu["y"], mu["z"])),
                  (f"{2.0 * sv:.1f}", (mv["x"], mv["y"], mv["z"])))
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        for txt, pos in labels:
            s = self._project_to_screen(pos, proj_view, w, h)
            if s is None:
                continue
            tt = self._get_text_texture(txt)
            if tt is None:
                continue
            tex, tw, th = tt
            self._draw_screen_text(s[0] - tw * 0.5, s[1] - th * 0.5,
                                   tex, tw, th, w, h)
        self.ctx.disable(moderngl.BLEND)
        self.ctx.enable(moderngl.DEPTH_TEST)
        self.ctx.enable(moderngl.CULL_FACE)

    def _start_resize_drag(self, mx, my):
        if self.view_type == "perspective":
            return False
        i, b = self._selected_block()
        if b is None:
            return False
        proj_view, _, _ = self._make_proj_view()
        w = max(1, self.width()); h = max(1, self.height())
        for key, pos in self._resize_handles(b):
            s = self._project_to_screen(pos, proj_view, w, h)
            if s is None:
                continue
            if abs(s[0] - mx) <= 9 and abs(s[1] - my) <= 9:
                self.editor.push_undo("Resize")
                self._resize_drag = {
                    "idx": i, "key": key, "view": self.view_type,
                    "b": [float(v) for v in b[:6]],
                }
                return True
        return False

    def _update_resize(self, mx, my):
        rd = self._resize_drag
        if not rd:
            return
        idx = rd["idx"]
        if not (0 <= idx < len(self.editor.blocks)):
            return
        b = self.editor.blocks[idx]
        if not isinstance(b, list) or len(b) < 6:
            return
        plane = {"top": ("y", 0.0), "front": ("z", 0.0),
                 "side": ("x", 0.0)}[rd["view"]]
        p = self._screen_to_world_on_plane(mx, my, plane[0], plane[1])
        if p is None:
            return
        ox, oy, oz, osx, osy, osz = rd["b"]
        ax_u, ax_v = self._resize_axes()
        pos = {"x": p[0], "y": p[1], "z": p[2]}
        centre = {"x": ox, "y": oy, "z": oz}
        half = {"x": osx, "y": osy, "z": osz}
        for ax, k in zip((ax_u, ax_v), rd["key"]):
            if k == 0:
                continue
            edge = pos[ax]
            other = centre[ax] - k * half[ax]
            lo, hi = (edge, other) if edge < other else (other, edge)
            centre[ax] = (lo + hi) * 0.5
            half[ax] = max(0.1, (hi - lo) * 0.5)
        b[0], b[1], b[2] = centre["x"], centre["y"], centre["z"]
        b[3], b[4], b[5] = half["x"], half["y"], half["z"]
        self.editor.mark_dirty()
        self.editor.refresh_all_views()

    def _pick_block(self, mx, my):
        blocks = self.editor.blocks
        
        def get_bounds(b):
            if Brush and isinstance(b, Brush):
                if hasattr(b, 'user_data') and 'model' in b.user_data:
                    model = b.user_data['model']
                    pos = glm.vec3(model[3][0], model[3][1], model[3][2])
                    sx = glm.length(glm.vec3(model[0][0], model[1][0], model[2][0]))
                    sy = glm.length(glm.vec3(model[0][1], model[1][1], model[2][1]))
                    sz = glm.length(glm.vec3(model[0][2], model[1][2], model[2][2]))
                else:
                    min_v, max_v = b.get_aabb()
                    pos = (min_v + max_v) * 0.5
                    sx, sy, sz = (max_v - min_v) * 0.5
                bx, by, bz = pos.x, pos.y, pos.z
            else:
                bx, by, bz, sx, sy, sz = b[:6]
            return (bx - sx, by - sy, bz - sz, bx + sx, by + sy, bz + sz)
        
        if self.view_type == "top":
            p = self._screen_to_world_on_plane(mx, my, "y", 0.0)
            if p is None: return -1
            px, pz = p[0], p[2]
            # Top view: only the centre X picks the object. The test is a 2D
            # distance, so walls never occlude the click.
            threshold = max(0.30, self._world_per_pixel() * 9.0)
            best_i = -1; best_d = threshold
            for i, b in enumerate(blocks):
                bd = self._block_bounds(b)
                if bd is None:
                    continue
                d = ((bd[0] - px) ** 2 + (bd[2] - pz) ** 2) ** 0.5
                if d < best_d:
                    best_d = d; best_i = i
            return best_i
        if self.view_type == "front":
            p = self._screen_to_world_on_plane(mx, my, "z", 0.0)
            if p is None: return -1
            px, py = p[0], p[1]
            best_i = -1; best_z = -1e9
            for i, b in enumerate(blocks):
                min_x, min_y, min_z, max_x, max_y, max_z = get_bounds(b)
                if (min_x <= px <= max_x and min_y <= py <= max_y):
                    if max_z > best_z: best_z = max_z; best_i = i
            return best_i
        if self.view_type == "side":
            p = self._screen_to_world_on_plane(mx, my, "x", 0.0)
            if p is None: return -1
            py, pz = p[1], p[2]
            best_i = -1; best_x = -1e9
            for i, b in enumerate(blocks):
                min_x, min_y, min_z, max_x, max_y, max_z = get_bounds(b)
                if (min_y <= py <= max_y and min_z <= pz <= max_z):
                    if max_x > best_x: best_x = max_x; best_i = i
            return best_i
        origin, dirv = self._screen_ray(mx, my)
        best_t = None; best_i = -1
        for i, b in enumerate(blocks):
            min_x, min_y, min_z, max_x, max_y, max_z = get_bounds(b)
            t = self._ray_aabb(origin, dirv, (min_x, min_y, min_z), (max_x, max_y, max_z))
            if t is not None and (best_t is None or t < best_t):
                best_t = t; best_i = i
        return best_i

    def _pick_entity(self, mx, my):
        # Generous pick box: entities often stand on the floor and the visible
        # marker is small, which made them hard to grab.
        def bounds(e):
            _etype, ex, ey, ez = e[:4]
            r = 0.85
            return (ex - r, ey - 0.3, ez - r), (ex + r, ey + 2.2, ez + r)
        ents = self.editor.entities
        if self.view_type == "top":
            p = self._screen_to_world_on_plane(mx, my, "y", 0.0)
            if p is None: return -1
            px, pz = p[0], p[2]
            # Top view: the entity's centre X is the only pick target.
            threshold = max(0.30, self._world_per_pixel() * 9.0)
            best_i = -1; best_d = threshold
            for i, e in enumerate(ents):
                _etype, ex, ey, ez = e[:4]
                d = ((float(ex) - px) ** 2 + (float(ez) - pz) ** 2) ** 0.5
                if d < best_d:
                    best_d = d; best_i = i
            return best_i
        if self.view_type == "front":
            p = self._screen_to_world_on_plane(mx, my, "z", 0.0)
            if p is None: return -1
            px, py = p[0], p[1]
            for i, e in enumerate(ents):
                mn, mx_ = bounds(e)
                if mn[0] <= px <= mx_[0] and mn[1] <= py <= mx_[1]: return i
            return -1
        if self.view_type == "side":
            p = self._screen_to_world_on_plane(mx, my, "x", 0.0)
            if p is None: return -1
            py, pz = p[1], p[2]
            for i, e in enumerate(ents):
                mn, mx_ = bounds(e)
                if mn[1] <= py <= mx_[1] and mn[2] <= pz <= mx_[2]: return i
            return -1
        origin, dirv = self._screen_ray(mx, my)
        best_t = None; best_i = -1
        for i, e in enumerate(ents):
            mn, mx_ = bounds(e)
            t = self._ray_aabb(origin, dirv, mn, mx_)
            if t is not None and (best_t is None or t < best_t):
                best_t = t; best_i = i
        if best_i >= 0:
            return best_i
        # 3D fallback: pick whatever entity marker is closest on screen, so a
        # click near the small marker still grabs the NPC instead of nothing.
        proj_view, _, _ = self._make_proj_view()
        w = max(1, self.width()); h = max(1, self.height())
        best_d = 44.0
        for i, e in enumerate(ents):
            _etype, ex, ey, ez = e[:4]
            s = self._project_to_screen((ex, ey + 1.0, ez), proj_view, w, h)
            if s is None:
                continue
            d = ((s[0] - mx) ** 2 + (s[1] - my) ** 2) ** 0.5
            if d < best_d:
                best_d = d; best_i = i
        return best_i

    def _item_pos(self, kind, idx):
        if kind == "block" and 0 <= idx < len(self.editor.blocks):
            b = self.editor.blocks[idx]
            if Brush and isinstance(b, Brush):
                m = b.user_data.get("model", glm.mat4()) if hasattr(b, "user_data") else glm.mat4()
                return (float(m[3][0]), float(m[3][1]), float(m[3][2]))
            return (float(b[0]), float(b[1]), float(b[2]))
        if kind == "entity" and 0 <= idx < len(self.editor.entities):
            e = self.editor.entities[idx]
            return (float(e[1]), float(e[2]), float(e[3]))
        return (0.0, 0.0, 0.0)

    def _begin_drag_existing(self, kind, idx, event, additive=False):
        self.drag_target_type = kind
        self.drag_target_idx = idx
        key = (kind, idx)
        if additive:
            self.editor.toggle_select(kind, idx)
        elif key not in self.editor.selection:
            # Clicking outside the current multi-selection replaces it, but
            # clicking an already selected item keeps the whole group so it can
            # be dragged together.
            self.editor.select_only(kind, idx)
        # Snapshot start positions for the whole selection so a drag moves the
        # group, and remember the pre-drag state for one undo entry.
        self._drag_multi = []
        for k, i in self.editor.selection:
            if k == "block" and 0 <= i < len(self.editor.blocks):
                self._drag_multi.append(("block", i, self._item_pos("block", i)))
            elif k == "entity" and 0 <= i < len(self.editor.entities):
                self._drag_multi.append(("entity", i, self._item_pos("entity", i)))
        if not self._drag_multi:
            self._drag_multi = [(kind, idx, self._item_pos(kind, idx))]
        self.drag_target_start_pos = self._item_pos(kind, idx)
        self._drag_undo = self.editor.snapshot()
        self.drag_start_world = self._world_for_drag(event.x(), event.y(), self.drag_target_start_pos)
        # Small movements (mouse jitter on a click) must not move the object.
        self._drag_press_xy = event.pos()
        self._drag_armed = False
        self._drag_moved = False
        self.mouse_down = True
        self.drag_start = event.pos()
        self.editor.refresh_all_views()
        self.editor._update_status()

    def _end_drag_existing(self):
        if self._drag_undo is not None:
            moved = False
            for kind, idx, start in self._drag_multi:
                cur = self._item_pos(kind, idx)
                if any(abs(cur[j] - start[j]) > 1e-6 for j in range(3)):
                    moved = True
                    break
            if moved:
                self.editor.push_undo_snapshot("Move", self._drag_undo)
            self._drag_undo = None
        # Entities dropped inside a solid would spawn stuck in the wall. Only
        # after a real drag: a plain selection click must never move anything.
        if getattr(self, "_drag_moved", False):
            for kind, idx, _start in self._drag_multi:
                if kind == "entity" and 0 <= idx < len(self.editor.entities):
                    self.editor._push_entity_out_of_solids(
                        self.editor.entities[idx])
        self._drag_armed = True
        self._drag_moved = False
        self._drag_multi = []
        self.drag_target_type = None
        self.drag_target_idx = -1
        self.drag_start_world = None
        self.drag_target_start_pos = None
        self.mouse_down = False
        self.editor.refresh_ui_lists()
        self.editor.refresh_all_views()

    def _start_cut_drag(self, kind, handle, event):
        idx = self.cut_selected_idx
        if not (0 <= idx < len(self.editor.blocks)): return
        b = self.editor.blocks[idx]
        if Brush and isinstance(b, Brush):
            return
        self.cut_drag_kind = kind
        self.cut_drag_handle = handle
        self.cut_drag_start_screen = event.pos()
        self._cut_undo = self.editor.snapshot()
        while len(b) < 8: b.append(None)
        self.cut_drag_block_start = (
            b[0], b[1], b[2], b[3], b[4], b[5],
            [list(c) for c in (b[7] or [])]
        )
        if kind == "corner":
            ci = handle
            cx, cy, cz = CUBE_CORNERS[ci]
            existing = b[7] or []
            has = any(int(c[0]) == cx and int(c[1]) == cy and int(c[2]) == cz
                      for c in existing)
            if not has:
                existing = list(existing) + [[cx, cy, cz, 0.20]]
                b[7] = existing
                self.editor.mark_dirty()
                self.update()

    def _update_cut_drag(self, event):
        idx = self.cut_selected_idx
        if not (0 <= idx < len(self.editor.blocks)): return
        b = self.editor.blocks[idx]
        if self.cut_drag_start_screen is None: return
        dx = event.x() - self.cut_drag_start_screen.x()
        dy = event.y() - self.cut_drag_start_screen.y()
        dpx = (dx*dx + dy*dy) ** 0.5
        w = max(1, self.width()); h = max(1, self.height())
        if self.view_type == "perspective":
            world_per_px = 0.02 * max(1.0, abs(self.cam_y))
        else:
            world_per_px = (2.0 * self.scale) / h
        world_d = dpx * world_per_px

        if self.cut_drag_kind == "corner":
            ci = self.cut_drag_handle
            cx, cy, cz = CUBE_CORNERS[ci]
            sx0, sy0, sz0 = self.cut_drag_block_start[3:6]
            min_dim = max(0.01, min(sx0, sy0, sz0))
            amount = max(0.05, min(0.5, 0.20 + world_d / (min_dim * 3.0)))
            new_cut = [cx, cy, cz, amount]
            others = [list(c) for c in (b[7] or []) if not (int(c[0]) == cx and int(c[1]) == cy and int(c[2]) == cz)]
            b[7] = others + [new_cut]
            self.editor.mark_dirty()
            self.update()
            self.editor.set_status(f"Cut {amount:.2f}")
        elif self.cut_drag_kind == "face":
            fi = self.cut_drag_handle
            axis, sign, _ = self._cut_face_centers(b)[fi]
            sx0, sy0, sz0 = self.cut_drag_block_start[3:6]
            base = [sx0, sy0, sz0][axis]
            new_half = max(0.1, base + sign * world_d)
            if axis == 0: b[3] = new_half
            elif axis == 1: b[4] = new_half
            else: b[5] = new_half
            self.editor.mark_dirty()
            self.update()
            self.editor.set_status(f"Resize {new_half:.2f}")

    def _end_cut_drag(self):
        undo = getattr(self, "_cut_undo", None)
        if undo is not None:
            self.editor.push_undo_snapshot("Cut / resize", undo)
            self._cut_undo = None
        self.cut_drag_kind = None
        self.cut_drag_handle = -1
        self.cut_drag_start_screen = None
        self.cut_drag_block_start = None
        self.editor.refresh_ui_lists()
        self.editor.refresh_all_views()

    def mousePressEvent(self, event):
        self.setFocus()
        shift = bool(event.modifiers() & Qt.ShiftModifier)

        if self.mouse_look_active:
            if event.button() in (Qt.LeftButton, Qt.MiddleButton):
                self._exit_mouse_look()
                return

        if event.button() == Qt.RightButton:
            self._right_press_pos = event.pos()
            self._right_dragged = False
            if self.view_type == "perspective":
                self._enter_mouse_look()
            return

        if self.view_type != "perspective":
            space_held = Qt.Key_Space in self.keys_down
            if event.button() == Qt.LeftButton \
               and self.editor.tool_mode in ("pointer", "group") \
               and not space_held:
                if self._start_resize_drag(event.x(), event.y()):
                    return
                ei = self._pick_entity(event.x(), event.y())
                bi = -1 if ei >= 0 else self._pick_block(event.x(), event.y())
                if ei >= 0:
                    self._begin_drag_existing("entity", ei, event, additive=shift); return
                if bi >= 0:
                    self._begin_drag_existing("block", bi, event, additive=shift); return
                if shift:
                    return
                self.editor.clear_selection()
                self.mouse_down = True
                self.is_panning = True
                self.is_dragging = False
                self.drag_target_type = None
                self.drag_start = event.pos()
                self.setCursor(Qt.ClosedHandCursor)
                self.editor.refresh_all_views()
                return
            if event.button() == Qt.MiddleButton or \
               (event.button() == Qt.LeftButton and space_held):
                self.mouse_down = True
                self.is_panning = True
                self.is_dragging = False
                self.drag_target_type = None
                self.drag_start = event.pos()
                self.setCursor(Qt.ClosedHandCursor)
                return

        if self.editor.tool_mode == "cut":
            if event.button() == Qt.LeftButton:
                proj_view, _, _ = self._make_proj_view()
                w = self.width(); h = self.height()
                if 0 <= self.cut_selected_idx < len(self.editor.blocks):
                    blk = self.editor.blocks[self.cut_selected_idx]
                    kind, handle = self._pick_cut_handle(blk, event.x(), event.y(), proj_view, w, h)
                    if kind is not None:
                        self._start_cut_drag(kind, handle, event)
                        return
                bi = self._pick_block(event.x(), event.y())
                if bi >= 0:
                    self.cut_selected_idx = bi
                    self.editor.select_only("block", bi)
                    self.editor.refresh_all_views()
                else:
                    self.cut_selected_idx = -1
                    self.editor.clear_selection()
                    self.editor.refresh_all_views()
                return
            elif event.button() == Qt.MiddleButton:
                self.mouse_down = True; self.is_panning = True; self.drag_start = event.pos()
                return
            return

        if self.editor.tool_mode == "pointer":
            if event.button() == Qt.LeftButton:
                ei = self._pick_entity(event.x(), event.y())
                bi = -1 if ei >= 0 else self._pick_block(event.x(), event.y())
                if ei >= 0: self._begin_drag_existing("entity", ei, event, additive=shift); return
                if bi >= 0: self._begin_drag_existing("block", bi, event, additive=shift); return
                if shift:
                    return
                self.editor.clear_selection()
                self.editor.refresh_all_views()
                self.editor._update_status()
                return
            elif event.button() == Qt.MiddleButton:
                self.mouse_down = True; self.is_panning = True; self.drag_start = event.pos()
                return
            return

        if event.button() == Qt.LeftButton:
            if self.editor.tool_mode == "entity":
                ei = self._pick_entity(event.x(), event.y())
                if ei >= 0:
                    self.editor.select_only("entity", ei)
                    self.editor.refresh_all_views(); return
                self.mouse_down = True; self.drag_start = event.pos()
                self.drag_world_start = self._world_for_mouse(event.x(), event.y())
                self.drag_world_end = self.drag_world_start
                self.is_dragging = True
                return
            elif self.editor.tool_mode == "block":
                bi = self._pick_block(event.x(), event.y())
                if bi >= 0:
                    if shift:
                        self.editor.toggle_select("block", bi)
                    else:
                        self.editor.select_only("block", bi)
                    self.editor.refresh_all_views(); return
                self.mouse_down = True; self.drag_start = event.pos()
                self.drag_world_start = self._world_for_mouse(event.x(), event.y())
                self.drag_world_end = self.drag_world_start
                self.is_dragging = True
                return
            ei = self._pick_entity(event.x(), event.y())
            bi = -1 if ei >= 0 else self._pick_block(event.x(), event.y())
            if ei >= 0:
                self.editor.select_only("entity", ei)
                self.editor.refresh_all_views(); return
            if bi >= 0:
                self.editor.select_only("block", bi)
                self.editor.refresh_all_views(); return
            self.mouse_down = True; self.drag_start = event.pos()
            self.drag_world_start = self._world_for_mouse(event.x(), event.y())
            self.drag_world_end = self.drag_world_start
            self.is_dragging = True
        elif event.button() == Qt.MiddleButton:
            self.mouse_down = True; self.is_panning = True; self.drag_start = event.pos()

    def mouseMoveEvent(self, event):
        if getattr(self, "_resize_drag", None) is not None:
            self._update_resize(event.x(), event.y())
            return
        if self.mouse_look_active and self.view_type == "perspective":
            if not (event.buttons() & Qt.RightButton):
                self._exit_mouse_look()
            else:
                cur = event.globalPos()
                if self._right_press_pos is not None:
                    manh = abs(event.x() - self._right_press_pos.x()) + abs(event.y() - self._right_press_pos.y())
                    if manh > 4:
                        self._right_dragged = True
                if self._last_mouse_pos is None:
                    self._last_mouse_pos = cur
                else:
                    dx = cur.x() - self._last_mouse_pos.x()
                    dy = cur.y() - self._last_mouse_pos.y()
                    if dx or dy:
                        self.yaw += dx * 0.005
                        self.pitch = max(-1.4, min(1.4, self.pitch - dy * 0.005))
                        center = self.mapToGlobal(self.rect().center())
                        QCursor.setPos(center)
                        self._last_mouse_pos = center
                        self.update()
                return

        if self.cut_drag_kind is not None:
            self._update_cut_drag(event)
            return

        if self.drag_target_type is not None:
            # Ignore mouse jitter until the pointer really moved, then
            # re-anchor the drag so the object does not snap on the first move.
            if not getattr(self, "_drag_armed", True):
                pxy = getattr(self, "_drag_press_xy", None)
                if pxy is not None:
                    ddx = event.x() - pxy.x()
                    ddy = event.y() - pxy.y()
                    if ddx * ddx + ddy * ddy < 16:
                        return
                self._drag_armed = True
                self._drag_moved = True
                self.drag_start_world = self._world_for_drag(
                    event.x(), event.y(), self.drag_target_start_pos)
                return
            p = self._world_for_drag(event.x(), event.y(), self.drag_target_start_pos)
            if p is not None and self.drag_start_world is not None:
                dx = p[0] - self.drag_start_world[0]
                dy = p[1] - self.drag_start_world[1]
                dz = p[2] - self.drag_start_world[2]
                if self.view_type in ("perspective", "top"):
                    d = (dx, 0.0, dz)
                elif self.view_type == "front":
                    d = (dx, dy, 0.0)
                else:
                    d = (0.0, dy, dz)
                for kind, idx, start in self._drag_multi:
                    nx = start[0] + d[0]
                    ny = start[1] + d[1]
                    nz = start[2] + d[2]
                    if kind == "block":
                        if not (0 <= idx < len(self.editor.blocks)):
                            continue
                        b = self.editor.blocks[idx]
                        if Brush and isinstance(b, Brush):
                            if hasattr(b, 'user_data'):
                                old_model = b.user_data.get('model', glm.mat4())
                                new_model = glm.mat4(glm.mat3(old_model))
                                new_model[3] = glm.vec4(nx, ny, nz, 1.0)
                                b.user_data['model'] = new_model
                        else:
                            b[0] = nx; b[1] = ny; b[2] = nz
                    else:
                        if not (0 <= idx < len(self.editor.entities)):
                            continue
                        e = self.editor.entities[idx]
                        e[1] = nx; e[2] = ny; e[3] = nz
                self.editor.mark_dirty()
                self.update()
                # Only the status bar is refreshed while dragging; the lists are
                # rebuilt once on release so dragging stays smooth.
                self.editor.set_status(
                    f"Move Δ=({d[0]:+.2f}, {d[1]:+.2f}, {d[2]:+.2f})   "
                    f"{len(self._drag_multi)} item(s)")
            return

        if self.view_type != "perspective" and not self.is_panning and not self.mouse_look_active:
            if Qt.Key_Space in self.keys_down:
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

        if (not self.mouse_down and not self.mouse_look_active
                and self.drag_target_type is None):
            try:
                self.editor.set_cursor_pos(self._world_for_mouse(event.x(), event.y()))
            except Exception:
                pass

        if not self.mouse_down: return
        if self.view_type == "perspective" and event.buttons() & Qt.LeftButton:
            dx = event.x() - self.drag_start.x(); dy = event.y() - self.drag_start.y()
            self.yaw += dx * 0.008
            self.pitch = max(-1.4, min(1.4, self.pitch + dy * 0.008))
            self.drag_start = event.pos()
        elif self.view_type != "perspective" and self.is_panning:
            dx = event.x() - self.drag_start.x(); dy = event.y() - self.drag_start.y()
            self.offset_x -= dx * (self.scale / 220)
            self.offset_y += dy * (self.scale / 220)
            self.drag_start = event.pos()
        elif self.is_dragging:
            self.drag_world_end = self._world_for_mouse(event.x(), event.y())
        self.update()

    def mouseReleaseEvent(self, event):
        if getattr(self, "_resize_drag", None) is not None:
            self._resize_drag = None
            self.editor.mark_dirty()
            self.editor.refresh_all_views()
            return
        if event.button() == Qt.RightButton:
            was_look = self.mouse_look_active
            dragged = self._right_dragged
            press_pos = self._right_press_pos
            self._exit_mouse_look()
            self._right_press_pos = None
            self._right_dragged = False
            if was_look and not dragged:
                gp = self.mapToGlobal(press_pos) if press_pos is not None else event.globalPos()
                self._show_context_menu(gp)
            return

        if self.cut_drag_kind is not None:
            self._end_cut_drag(); return
        if self.drag_target_type is not None:
            self._end_drag_existing(); return

        if event.button() == Qt.LeftButton:
            if self.is_panning:
                self.mouse_down = False
                self.is_panning = False
                if self.view_type != "perspective":
                    if Qt.Key_Space in self.keys_down:
                        self.setCursor(Qt.OpenHandCursor)
                    else:
                        self.setCursor(Qt.ArrowCursor)
                self.update()
                return
            if self.is_dragging and self.drag_world_start and self.drag_world_end:
                a = np.array(self.drag_world_start, dtype=np.float32)
                b = np.array(self.drag_world_end, dtype=np.float32)
                mode = self.editor.tool_mode
                if mode == "block":
                    if np.linalg.norm(b - a) >= self.grid_step * 0.5:
                        center = (a + b) * 0.5
                        half = np.maximum(np.abs(b - a) * 0.5, np.array([1.0, 1.0, 1.0]))
                        
                        # 恢复为传统的 list 格式方块
                        self.editor.push_undo("Add solid")
                        new_block = [
                            float(center[0]), float(center[1]), float(center[2]),
                            float(half[0]), float(half[1]), float(half[2]),
                            self.editor.current_texture,
                            None,
                            bool(self.editor.current_is_water)
                        ]
                        self.editor.blocks.append(new_block)
                        self.editor.select_only("block", len(self.editor.blocks) - 1)
                        self.editor.mark_dirty()
                        self.editor.refresh_ui_lists(); self.editor.refresh_all_views()
                        self.editor._update_status()
                elif mode == "entity":
                    etype = self.editor.current_entity_type
                    edef = ENTITY_DEFS.get(etype, {})
                    default_params = dict(edef.get("params", {}))
                    # A double-click (or a duplicated release event) used to
                    # place the same NPC twice. Ignore a repeat at the same spot
                    # within a short window.
                    now = time.time()
                    last = getattr(self.editor, "_last_entity_place", None)
                    if last is not None:
                        lx, ly, lz, lt = last
                        if (now - lt < 0.45
                                and abs(lx - float(self.drag_world_end[0])) < 0.3
                                and abs(lz - float(self.drag_world_end[2])) < 0.3):
                            return
                    self.editor._last_entity_place = (
                        float(self.drag_world_end[0]),
                        float(self.drag_world_end[1]),
                        float(self.drag_world_end[2]), now)
                    self.editor.push_undo("Add entity")
                    self.editor.entities.append([
                        etype,
                        float(self.drag_world_end[0]),
                        float(self.drag_world_end[1]),
                        float(self.drag_world_end[2]),
                        None,
                        default_params,
                    ])
                    # Never leave an NPC inside a solid.
                    self.editor._push_entity_out_of_solids(
                        self.editor.entities[-1])
                    self.editor.select_only("entity", len(self.editor.entities) - 1)
                    self.editor.mark_dirty()
                    self.editor.refresh_ui_lists()
                    self.editor.refresh_all_views()
                    self.editor._update_status()
            self.mouse_down = False
            self.is_dragging = False
            self.is_panning = False
            self.drag_world_start = None
            self.drag_world_end = None
        elif event.button() == Qt.MiddleButton:
            self.mouse_down = False
            self.is_panning = False
            if self.view_type != "perspective":
                if Qt.Key_Space in self.keys_down:
                    self.setCursor(Qt.OpenHandCursor)
                else:
                    self.setCursor(Qt.ArrowCursor)
            self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            ei = self._pick_entity(event.x(), event.y())
            if ei >= 0:
                self.edit_entity(ei); return
            bi = self._pick_block(event.x(), event.y())
            if bi >= 0:
                self.resize_block(bi)

    def _show_context_menu(self, global_pos):
        local = self.mapFromGlobal(global_pos)
        mx, my = local.x(), local.y()
        ei = self._pick_entity(mx, my)
        bi = self._pick_block(mx, my)
        menu = QMenu(self)
        if ei >= 0:
            act_edit = menu.addAction(self.editor.texts["edit_entity_menu"])
            act_cut = menu.addAction(self.editor.texts["cut_menu"])
            act_del = menu.addAction(self.editor.texts["delete_entity_menu"])
            act_edit.triggered.connect(lambda: self.edit_entity(ei))
            act_cut.triggered.connect(lambda: self.cut_entity(ei))
            act_del.triggered.connect(lambda: self.delete_entity(ei))
            menu.exec_(global_pos); return
        if bi >= 0:
            b = self.editor.blocks[bi]
            is_brush = Brush and isinstance(b, Brush)
            act_tex = menu.addAction(self.editor.texts["create_entity"])
            act_resize = menu.addAction(self.editor.texts["edit_size"])
            act_cut = menu.addAction(self.editor.texts["cut_menu"])
            if is_brush:
                act_tex.triggered.connect(lambda: self._apply_texture_to_brush(bi))
                act_resize.triggered.connect(lambda: self.resize_block(bi))
                act_cut.setEnabled(False)  # Brush 暂不支持切角
            else:
                act_tex.triggered.connect(lambda: self.apply_texture_to_block(bi))
                act_resize.triggered.connect(lambda: self.resize_block(bi))
                act_cut.triggered.connect(lambda: self.cut_block(bi))
            
            if is_brush:
                is_water = b.user_data.get("is_water", False) if hasattr(b, 'user_data') else False
            else:
                is_water = (len(b) > 8 and b[8]) or is_water_texture(b[6])
            act_water = menu.addAction(
                "取消水方块" if is_water else "💧 设为水方块")
            if is_brush:
                act_water.triggered.connect(lambda: self._toggle_water_brush(bi))
            else:
                act_water.triggered.connect(lambda: self.toggle_water_block(bi))
        else:
            menu.addAction("...").setEnabled(False)
        menu.exec_(global_pos)

    def toggle_water_block(self, index):
        if not (0 <= index < len(self.editor.blocks)):
            return
        b = self.editor.blocks[index]
        if Brush and isinstance(b, Brush):
            self._toggle_water_brush(index)
            return
        self.editor.push_undo("Toggle water")
        while len(b) < 9:
            b.append(None)
        b[8] = not bool(b[8])
        self.editor.mark_dirty()
        self.update()
        self.editor.refresh_ui_lists()

    def _apply_texture_to_brush(self, index):
        if 0 <= index < len(self.editor.blocks):
            b = self.editor.blocks[index]
            if Brush and isinstance(b, Brush) and hasattr(b, 'user_data'):
                self.editor.push_undo("Apply texture")
                b.user_data["texture"] = self.editor.current_texture
                self.editor.mark_dirty()
                self.update(); self.editor.refresh_ui_lists()

    def _toggle_water_brush(self, index):
        if not (0 <= index < len(self.editor.blocks)):
            return
        b = self.editor.blocks[index]
        if Brush and isinstance(b, Brush) and hasattr(b, 'user_data'):
            self.editor.push_undo("Toggle water")
            b.user_data["is_water"] = not b.user_data.get("is_water", False)
            self.editor.mark_dirty()
            self.update()
            self.editor.refresh_ui_lists()

    def cut_block(self, index):
        if not (0 <= index < len(self.editor.blocks)): return
        b = self.editor.blocks[index]
        if Brush and isinstance(b, Brush):
            QMessageBox.information(self, "提示", "Brush 对象暂不支持切角操作，请先转换为基础几何体")
            return
        while len(b) < 8: b.append(None)
        dlg = CutBlockDialog(self.editor, b[7], self)
        if dlg.exec_() == QDialog.Accepted:
            self.editor.push_undo("Cut solid")
            cuts = dlg.get_cuts()
            b[7] = cuts if cuts else None
            self.editor.mark_dirty()
            self.update(); self.editor.refresh_ui_lists()

    def cut_entity(self, index):
        if not (0 <= index < len(self.editor.entities)): return
        e = self.editor.entities[index]
        while len(e) < 5: e.append(None)
        dlg = CutBlockDialog(self.editor, e[4], self)
        if dlg.exec_() == QDialog.Accepted:
            self.editor.push_undo("Cut entity")
            cuts = dlg.get_cuts()
            e[4] = cuts if cuts else None
            self.editor.mark_dirty()
            self.update(); self.editor.refresh_ui_lists()

    def edit_entity(self, index):
        dlg = EntityEditDialog(self.editor, index, self)
        if dlg.exec_() == QDialog.Accepted:
            self.editor.push_undo("Edit entity")
            dlg.apply()
            self.editor.mark_dirty()
            self.update(); self.editor.refresh_ui_lists()

    def delete_entity(self, index):
        if 0 <= index < len(self.editor.entities):
            self.editor.push_undo("Delete entity")
            del self.editor.entities[index]
            self.editor.selection = [
                (k, i if i < index else i - 1) for k, i in self.editor.selection
                if not (k == "entity" and i == index)]
            self.editor.mark_dirty()
            self.update(); self.editor.refresh_ui_lists()

    def resize_block(self, index):
        if not (0 <= index < len(self.editor.blocks)): return
        b = self.editor.blocks[index]
        if Brush and isinstance(b, Brush):
            min_v, max_v = b.get_aabb()
            pos = (min_v + max_v) * 0.5
            half = (max_v - min_v) * 0.5
            
            dlg = QDialog(self)
            dlg.setWindowTitle(self.editor.texts["edit_block_size"].format(index))
            layout = QVBoxLayout(dlg)
            layout.addWidget(QLabel(self.editor.texts["length_x"]))
            spin_sx = QDoubleSpinBox(); spin_sx.setRange(0.1, 500); spin_sx.setValue(half[0]); layout.addWidget(spin_sx)
            layout.addWidget(QLabel(self.editor.texts["height_y"]))
            spin_sy = QDoubleSpinBox(); spin_sy.setRange(0.1, 500); spin_sy.setValue(half[1]); layout.addWidget(spin_sy)
            layout.addWidget(QLabel(self.editor.texts["width_z"]))
            spin_sz = QDoubleSpinBox(); spin_sz.setRange(0.1, 500); spin_sz.setValue(half[2]); layout.addWidget(spin_sz)
            btn_box = QHBoxLayout()
            btn_ok = QPushButton(self.editor.texts["ok"]); btn_cancel = QPushButton(self.editor.texts["cancel"])
            btn_ok.clicked.connect(dlg.accept); btn_cancel.clicked.connect(dlg.reject)
            btn_box.addWidget(btn_ok); btn_box.addWidget(btn_cancel); layout.addLayout(btn_box)
            if dlg.exec_() == QDialog.Accepted:
                self.editor.push_undo("Resize solid")
                new_half = np.array([spin_sx.value(), spin_sy.value(), spin_sz.value()], dtype=np.float32)
                if hasattr(b, 'user_data') and 'model' in b.user_data:
                    model = b.user_data['model']
                    rot = glm.mat3(model)
                    new_model = glm.mat4(rot)
                    new_model = glm.scale(new_model, glm.vec3(*new_half))
                    new_model[3] = model[3]
                    b.user_data['model'] = new_model
                    b._triangulated = None
                    b._aabb = None
                self.editor.mark_dirty()
                self.update(); self.editor.refresh_ui_lists()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(self.editor.texts["edit_block_size"].format(index))
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(self.editor.texts["length_x"]))
        spin_sx = QDoubleSpinBox(); spin_sx.setRange(0.1, 500); spin_sx.setValue(b[3]); layout.addWidget(spin_sx)
        layout.addWidget(QLabel(self.editor.texts["height_y"]))
        spin_sy = QDoubleSpinBox(); spin_sy.setRange(0.1, 500); spin_sy.setValue(b[4]); layout.addWidget(spin_sy)
        layout.addWidget(QLabel(self.editor.texts["width_z"]))
        spin_sz = QDoubleSpinBox(); spin_sz.setRange(0.1, 500); spin_sz.setValue(b[5]); layout.addWidget(spin_sz)
        btn_box = QHBoxLayout()
        btn_ok = QPushButton(self.editor.texts["ok"]); btn_cancel = QPushButton(self.editor.texts["cancel"])
        btn_ok.clicked.connect(dlg.accept); btn_cancel.clicked.connect(dlg.reject)
        btn_box.addWidget(btn_ok); btn_box.addWidget(btn_cancel); layout.addLayout(btn_box)
        if dlg.exec_() == QDialog.Accepted:
            self.editor.push_undo("Resize solid")
            b[3] = spin_sx.value(); b[4] = spin_sy.value(); b[5] = spin_sz.value()
            self.editor.mark_dirty()
            self.update(); self.editor.refresh_ui_lists()

    def wheelEvent(self, event):
        delta = event.angleDelta().y(); fac = 0.9 if delta > 0 else 1.1
        if self.view_type == "perspective":
            forward = self._camera_vectors(); step = 30.0 * (1.0 - fac)
            self.cam_x += forward[0] * step; self.cam_y += forward[1] * step; self.cam_z += forward[2] * step
        else:
            # Zoom about the cursor: keep the world point under it fixed.
            w = max(1, self.width()); h = max(1, self.height())
            aspect = w / h
            if aspect >= 1.0:
                hw0 = self.scale * aspect; hh0 = self.scale
            else:
                hw0 = self.scale; hh0 = self.scale / aspect
            ndc_x = 2.0 * event.x() / w - 1.0
            ndc_y = 1.0 - 2.0 * event.y() / h
            self.scale = float(np.clip(self.scale * fac, 4, 800))
            if aspect >= 1.0:
                hw1 = self.scale * aspect; hh1 = self.scale
            else:
                hw1 = self.scale; hh1 = self.scale / aspect
            self.offset_x -= ndc_x * (hw1 - hw0)
            self.offset_y -= ndc_y * (hh1 - hh0)
        self.update()

# ============================================================
# 实用工具对话框：掏空 / 阵列
# ============================================================
class HollowDialog(QDialog):
    def __init__(self, editor, default_thickness, parent=None):
        super().__init__(parent or editor)
        lang = getattr(editor, "lang", "en")
        self.setWindowTitle(tr_text(lang, "hollow_title"))
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr_text(lang, "wall_thickness")))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(0.1, 64.0)
        self.spin.setDecimals(2)
        self.spin.setValue(max(0.5, float(default_thickness) / 2.0))
        layout.addWidget(self.spin)
        btns = QHBoxLayout(); btns.addStretch()
        ok = QPushButton(tr_text(lang, "ok"))
        cancel = QPushButton(tr_text(lang, "cancel"))
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(cancel)
        layout.addLayout(btns)

    def thickness(self):
        return self.spin.value()


class ArrayDialog(QDialog):
    def __init__(self, editor, step, parent=None):
        super().__init__(parent or editor)
        lang = getattr(editor, "lang", "en")
        self.setWindowTitle(tr_text(lang, "array_title"))
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        form = QGridLayout(); form.setHorizontalSpacing(8)
        form.addWidget(QLabel(tr_text(lang, "count")), 0, 0)
        self.count = QSpinBox(); self.count.setRange(2, 500); self.count.setValue(2)
        form.addWidget(self.count, 0, 1)
        for row, (key, val) in enumerate((("offset_x", step), ("offset_y", 0.0),
                                          ("offset_z", 0.0)), start=1):
            form.addWidget(QLabel(tr_text(lang, key)), row, 0)
            sp = QDoubleSpinBox(); sp.setRange(-9999, 9999); sp.setDecimals(2)
            sp.setValue(float(val))
            setattr(self, key, sp)
            form.addWidget(sp, row, 1)
        layout.addLayout(form)
        btns = QHBoxLayout(); btns.addStretch()
        ok = QPushButton(tr_text(lang, "ok"))
        cancel = QPushButton(tr_text(lang, "cancel"))
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(cancel)
        layout.addLayout(btns)

    def values(self):
        return (int(self.count.value()), self.offset_x.value(),
                self.offset_y.value(), self.offset_z.value())


class DoorwayDialog(QDialog):
    """Width/height of a hole cut through the selected wall."""

    def __init__(self, editor, parent=None):
        super().__init__(parent or editor)
        lang = getattr(editor, "lang", "en")
        self.setWindowTitle(tr_text(lang, "doorway_title"))
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.addWidget(QLabel(tr_text(lang, "doorway_width")), 0, 0)
        self.width = QDoubleSpinBox()
        self.width.setRange(0.4, 16.0)
        self.width.setDecimals(2)
        self.width.setValue(1.4)
        form.addWidget(self.width, 0, 1)
        form.addWidget(QLabel(tr_text(lang, "doorway_height")), 1, 0)
        self.height = QDoubleSpinBox()
        self.height.setRange(0.5, 8.0)
        self.height.setDecimals(2)
        self.height.setValue(1.1)
        form.addWidget(self.height, 1, 1)
        layout.addLayout(form)
        btns = QHBoxLayout(); btns.addStretch()
        ok = QPushButton(tr_text(lang, "ok"))
        cancel = QPushButton(tr_text(lang, "cancel"))
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(cancel)
        layout.addLayout(btns)

    def values(self):
        return self.width.value(), self.height.value()


class TerrainDialog(QDialog):
    """A rolling height field, as real terrain or as a grid of blocks."""

    def __init__(self, editor, parent=None):
        super().__init__(parent or editor)
        lang = getattr(editor, "lang", "en")
        self.setWindowTitle(tr_text(lang, "terrain_title"))
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form = QGridLayout(); form.setHorizontalSpacing(8)
        self.size_x = QDoubleSpinBox(); self.size_x.setRange(4, 200)
        self.size_x.setValue(24.0); self.size_x.setDecimals(1)
        self.size_z = QDoubleSpinBox(); self.size_z.setRange(4, 200)
        self.size_z.setValue(24.0); self.size_z.setDecimals(1)
        self.segments = QSpinBox(); self.segments.setRange(2, 128)
        self.segments.setValue(16)
        self.hill = QDoubleSpinBox(); self.hill.setRange(0.2, 20)
        self.hill.setValue(1.6); self.hill.setDecimals(2)
        self.seed = QSpinBox(); self.seed.setRange(0, 9999)
        self.seed.setValue(1)
        for row, (key, w) in enumerate((
                ("terrain_size", self.size_x), ("terrain_size_z", self.size_z),
                ("terrain_segments", self.segments),
                ("terrain_height", self.hill), ("terrain_seed", self.seed))):
            form.addWidget(QLabel(tr_text(lang, key)), row, 0)
            form.addWidget(w, row, 1)
        # A block grid is stepped: every cell is a solid, so any step taller
        # than the player's 0.45 m step height becomes a wall. The smooth option
        # emits one interpolated height-field surface instead (walkable slopes,
        # one mesh in game, saved in the map's "terrains" list).
        self.smooth = QCheckBox(
            "Smooth surface (height field)" if lang != "zh"
            else "平滑曲面（高度场）")
        self.smooth.setChecked(True)
        form.addWidget(QLabel(""), 5, 0)
        form.addWidget(self.smooth, 5, 1)
        layout.addLayout(form)
        note = QLabel(
            "Smooth: one walkable surface, slopes follow the field.\n"
            "Unticked: a grid of solid blocks (stepped, 0.45 m step height \u2192"
            " taller steps are walls)."
            if lang != "zh" else
            "平滑：单一网格曲面，坡面连续可走。\n"
            "取消勾选：实体方块网格（有台阶，超过 0.45 m 的台阶会成为墙）。")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        layout.addWidget(note)
        btns = QHBoxLayout(); btns.addStretch()
        ok = QPushButton(tr_text(lang, "ok"))
        cancel = QPushButton(tr_text(lang, "cancel"))
        ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        btns.addWidget(ok); btns.addWidget(cancel)
        layout.addLayout(btns)

    def values(self):
        return (self.size_x.value(), self.size_z.value(),
                int(self.segments.value()), self.hill.value(),
                int(self.seed.value()))

    def is_smooth(self):
        return bool(self.smooth.isChecked())


# ============================================================
# Entity Report（Map 菜单里的对象清单）
# ============================================================
class EntityReportDialog(QDialog):
    def __init__(self, editor, parent=None):
        super().__init__(parent or editor)
        self.editor = editor
        self.setWindowTitle("Entity Report")
        self.resize(560, 440)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Solids:"))
        self.block_list = QListWidget()
        self.block_list.itemClicked.connect(self._pick_block)
        self.block_list.itemDoubleClicked.connect(self._edit_block)
        layout.addWidget(self.block_list, stretch=1)
        row = QHBoxLayout()
        b_del = QPushButton("Delete Solid")
        b_edit = QPushButton("Resize...")
        b_del.clicked.connect(self._del_block)
        b_edit.clicked.connect(self._edit_block)
        row.addWidget(b_del); row.addWidget(b_edit); row.addStretch()
        layout.addLayout(row)

        layout.addWidget(QLabel("Entities:"))
        self.ent_list = QListWidget()
        self.ent_list.itemClicked.connect(self._pick_ent)
        self.ent_list.itemDoubleClicked.connect(self._edit_ent)
        layout.addWidget(self.ent_list, stretch=1)
        row2 = QHBoxLayout()
        b_del2 = QPushButton("Delete Entity")
        b_props = QPushButton("Properties...")
        b_del2.clicked.connect(self._del_ent)
        b_props.clicked.connect(self._edit_ent)
        row2.addWidget(b_del2); row2.addWidget(b_props); row2.addStretch()
        layout.addLayout(row2)

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignRight)
        self.refresh()

    def refresh(self):
        ed = self.editor
        self.block_list.clear()
        for i, b in enumerate(ed.blocks):
            if Brush and isinstance(b, Brush):
                self.block_list.addItem(f"#{i}  [Brush]")
            else:
                tex = b[6] if len(b) > 6 else "?"
                self.block_list.addItem(
                    f"#{i}  c=({b[0]:.1f},{b[1]:.1f},{b[2]:.1f})  "
                    f"h=({b[3]:.1f},{b[4]:.1f},{b[5]:.1f})  {tex}")
        self.ent_list.clear()
        for i, e in enumerate(ed.entities):
            self.ent_list.addItem(
                f"#{i}  {e[0]}  ({e[1]:.1f},{e[2]:.1f},{e[3]:.1f})")

    def _pick_block(self, item):
        self.editor.select_only("block", self.block_list.row(item))
        self.editor.refresh_all_views(); self.editor._update_status()

    def _pick_ent(self, item):
        self.editor.select_only("entity", self.ent_list.row(item))
        self.editor.refresh_all_views(); self.editor._update_status()

    def _del_block(self):
        self.editor._delete_block_at(self.block_list.currentRow())
        self.refresh()

    def _del_ent(self):
        self.editor._delete_entity_at(self.ent_list.currentRow())
        self.refresh()

    def _edit_block(self, *a):
        i = self.block_list.currentRow()
        if i >= 0:
            self.editor.view_persp.resize_block(i)

    def _edit_ent(self, *a):
        i = self.ent_list.currentRow()
        if i >= 0:
            self.editor.view_persp.edit_entity(i)
        self.refresh()


# ============================================================
# 主窗口
# ============================================================
class CinematicDialog(QDialog):
    """Author the map's opening cinematic: camera path, title, subtitles.

    Compiles into the ``cinematic`` block that the engine plays after the map
    finishes loading (camera fly-through + big title + timed subtitles).
    """

    def __init__(self, data, view_camera=None, parent=None):
        super().__init__(parent)
        self.lang = getattr(parent, "lang", "en")
        if self.lang not in LANGUAGES:
            self.lang = "en"
        tr = lambda key: tr_text(self.lang, key)  # noqa: E731
        self.setWindowTitle(tr("cinematic_window"))
        self.setMinimumWidth(680)
        self._view_camera = view_camera
        data = data or {}
        lay = QVBoxLayout(self)

        info = QLabel(tr("cinematic_info"))
        info.setWordWrap(True)
        lay.addWidget(info)

        top = QGridLayout()
        top.addWidget(QLabel(tr("cinematic_main_title")), 0, 0)
        self.title_edit = QLineEdit(str(data.get("title", "ANIMA ENGINE")))
        top.addWidget(self.title_edit, 0, 1)
        top.addWidget(QLabel(tr("cinematic_subtitle")), 0, 2)
        self.sub_edit = QLineEdit(str(data.get("subtitle", "")))
        top.addWidget(self.sub_edit, 0, 3)
        top.addWidget(QLabel(tr("cinematic_duration")), 1, 0)
        self.dur_spin = QDoubleSpinBox()
        self.dur_spin.setRange(1.0, 600.0)
        self.dur_spin.setValue(float(data.get("duration", 12.0) or 12.0))
        top.addWidget(self.dur_spin, 1, 1)
        top.addWidget(QLabel(tr("cinematic_fov")), 1, 2)
        self.fov_spin = QDoubleSpinBox()
        self.fov_spin.setRange(0.0, 140.0)
        self.fov_spin.setValue(float(data.get("fov", 0.0) or 0.0))
        top.addWidget(self.fov_spin, 1, 3)
        self.letterbox_check = QCheckBox(tr("cinematic_letterbox"))
        self.letterbox_check.setChecked(bool(data.get("letterbox", True)))
        top.addWidget(self.letterbox_check, 2, 0, 1, 2)
        self.enabled_check = QCheckBox(tr("cinematic_play"))
        self.enabled_check.setChecked(bool(data.get("enabled", True)))
        top.addWidget(self.enabled_check, 2, 2, 1, 2)
        top.addWidget(QLabel("Subtitle position:"), 3, 0)
        self.side_combo = QComboBox()
        self.side_combo.addItem("Bottom (cinema standard)", "bottom")
        self.side_combo.addItem("Right side of screen", "right")
        try:
            idx = self.side_combo.findData(
                str(data.get("subtitle_side", data.get("subtitle_pos",
                                                        "bottom"))).lower())
            self.side_combo.setCurrentIndex(max(0, idx))
        except Exception:
            pass
        top.addWidget(self.side_combo, 3, 1, 1, 3)
        lay.addLayout(top)

        # ---- simple mode -------------------------------------------------
        # The old dialog only had the precise key table, which is why camera
        # sequencing looked hard: every shot needed a typed time + six
        # coordinates.  A shot is now just "the current 3D view for N seconds".
        lay.addWidget(QLabel(
            "Simple shots (timed automatically; use the 3D view to place the "
            "camera):"))
        self.shot_table = QTableWidget(0, 2)
        self.shot_table.setHorizontalHeaderLabels(["Seconds", "Subtitle"])
        self.shot_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self.shot_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.shot_table.verticalHeader().setVisible(False)
        self.shot_table.setMinimumHeight(110)
        lay.addWidget(self.shot_table)
        shb = QHBoxLayout()
        b_shot = QPushButton("Add current 3D view as shot")
        b_shot.clicked.connect(self._add_shot_from_view)
        shb.addWidget(b_shot)
        b_shot0 = QPushButton("Add empty shot")
        b_shot0.clicked.connect(lambda: self._add_shot())
        shb.addWidget(b_shot0)
        b_shot_del = QPushButton("Remove shot")
        b_shot_del.clicked.connect(lambda: self._remove_row(self.shot_table))
        shb.addWidget(b_shot_del)
        shb.addStretch(1)
        lay.addLayout(shb)

        lay.addWidget(QLabel("Advanced: exact key times (optional)"))
        self.key_table = QTableWidget(0, 7)
        self.key_table.setHorizontalHeaderLabels(
            [tr("cinematic_time"),
             f'{tr("cinematic_cam")} X', f'{tr("cinematic_cam")} Y',
             f'{tr("cinematic_cam")} Z',
             f'{tr("cinematic_look")} X', f'{tr("cinematic_look")} Y',
             f'{tr("cinematic_look")} Z'])
        self.key_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.key_table.verticalHeader().setVisible(False)
        lay.addWidget(self.key_table)
        kb = QHBoxLayout()
        b_add = QPushButton(tr("cinematic_add_key"))
        b_add.clicked.connect(lambda: self._add_key())
        kb.addWidget(b_add)
        b_cam = QPushButton(tr("cinematic_cam_key"))
        b_cam.clicked.connect(self._add_key_from_view)
        kb.addWidget(b_cam)
        b_del = QPushButton(tr("cinematic_del_key"))
        b_del.clicked.connect(lambda: self._remove_row(self.key_table))
        kb.addWidget(b_del)
        kb.addStretch(1)
        lay.addLayout(kb)

        lay.addWidget(QLabel(tr("cinematic_subtitles")))
        self.sub_table = QTableWidget(0, 3)
        self.sub_table.setHorizontalHeaderLabels(
            [tr("cinematic_start"), tr("cinematic_end"),
             tr("cinematic_text")])
        self.sub_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents)
        self.sub_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents)
        self.sub_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.sub_table.verticalHeader().setVisible(False)
        lay.addWidget(self.sub_table)
        sb = QHBoxLayout()
        s_add = QPushButton(tr("cinematic_add_sub"))
        s_add.clicked.connect(self._add_subtitle)
        sb.addWidget(s_add)
        s_del = QPushButton(tr("cinematic_del_sub"))
        s_del.clicked.connect(lambda: self._remove_row(self.sub_table))
        sb.addWidget(s_del)
        sb.addStretch(1)
        lay.addLayout(sb)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ok = QPushButton(tr_text(self.lang, "ok"))
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton(tr_text(self.lang, "cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        lay.addLayout(buttons)

        for key in data.get("keys") or []:
            try:
                self._append_key(
                    float(key.get("t", 0.0)),
                    *[float(v) for v in key.get("pos", [0, 2, 0])][:3],
                    *[float(v) for v in key.get("look", [0, 2, 1])][:3])
            except Exception:
                continue
        if self.key_table.rowCount() == 0:
            self._add_key()
        for cue in data.get("subtitles") or []:
            try:
                self._append_subtitle(float(cue.get("start", 0.0)),
                                      float(cue.get("end", 3.0)),
                                      str(cue.get("text", "")))
            except Exception:
                continue
        for shot in data.get("shots") or []:
            try:
                self._append_shot(float(shot.get("seconds", 4.0)),
                                  shot.get("pos", [0.0, 2.0, 0.0]),
                                  shot.get("look", [0.0, 2.0, 1.0]),
                                  str(shot.get("text", "") or ""))
            except Exception:
                continue
        if self.shot_table.rowCount() == 0 and self.key_table.rowCount() <= 1:
            # Fresh dialog: start the simple workflow with the current view.
            pass

    # ---------------------------------------------------------- shot helpers
    def _append_shot(self, seconds, pos, look, text):
        r = self.shot_table.rowCount()
        self.shot_table.insertRow(r)
        self.shot_table.setItem(r, 0, self._cell(f"{float(seconds):.1f}"))
        self.shot_table.setItem(r, 1, QTableWidgetItem(str(text)))
        # Store the camera in the item data so the user never edits numbers.
        item = self.shot_table.item(r, 0)
        item.setData(Qt.UserRole, {
            "pos": [float(v) for v in pos],
            "look": [float(v) for v in look],
        })

    def _add_shot(self):
        self._append_shot(4.0, [0.0, 2.0, 0.0], [0.0, 2.0, 2.0], "")

    def _add_shot_from_view(self):
        pos = look = None
        try:
            if callable(self._view_camera):
                pos, look = self._view_camera()
        except Exception:
            pos = look = None
        if pos is None:
            self._add_shot()
            return
        self._append_shot(4.0, pos, look, "")

    # ---------------------------------------------------------- table utils
    @staticmethod
    def _cell(text):
        item = QTableWidgetItem(str(text))
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def _append_key(self, t, px, py, pz, lx, ly, lz):
        r = self.key_table.rowCount()
        self.key_table.insertRow(r)
        for c, val in enumerate((t, px, py, pz, lx, ly, lz)):
            self.key_table.setItem(r, c, self._cell(f"{float(val):.3f}"))

    def _append_subtitle(self, start, end, text):
        r = self.sub_table.rowCount()
        self.sub_table.insertRow(r)
        self.sub_table.setItem(r, 0, self._cell(f"{float(start):.2f}"))
        self.sub_table.setItem(r, 1, self._cell(f"{float(end):.2f}"))
        self.sub_table.setItem(r, 2, QTableWidgetItem(str(text)))

    def _remove_row(self, table):
        row = table.currentRow()
        if row < 0 and table.rowCount():
            row = table.rowCount() - 1
        if row >= 0:
            table.removeRow(row)

    def _add_key(self):
        last_t = 0.0
        if self.key_table.rowCount():
            try:
                last_t = float(self.key_table.item(
                    self.key_table.rowCount() - 1, 0).text())
            except Exception:
                last_t = 0.0
        self._append_key(last_t + 3.0, 0.0, 2.0, 0.0, 0.0, 2.0, 1.0)

    def _add_key_from_view(self):
        pos = look = None
        try:
            if callable(self._view_camera):
                pos, look = self._view_camera()
        except Exception:
            pos = look = None
        if pos is None:
            self._add_key()
            return
        last_t = 0.0
        if self.key_table.rowCount():
            try:
                last_t = float(self.key_table.item(
                    self.key_table.rowCount() - 1, 0).text())
            except Exception:
                last_t = 0.0
        self._append_key(last_t + 3.0, pos[0], pos[1], pos[2],
                         look[0], look[1], look[2])

    def _add_subtitle(self):
        start = 1.0
        if self.sub_table.rowCount():
            try:
                start = float(self.sub_table.item(
                    self.sub_table.rowCount() - 1, 1).text())
            except Exception:
                start = 0.0
        self._append_subtitle(start + 0.5, start + 3.5, "")

    # -------------------------------------------------------------- result
    def values(self):
        keys = []
        for r in range(self.key_table.rowCount()):
            try:
                vals = [float(self.key_table.item(r, c).text())
                        for c in range(7)]
            except Exception:
                continue
            keys.append({"t": vals[0], "pos": vals[1:4], "look": vals[4:7]})
        keys.sort(key=lambda k: k["t"])
        subs = []
        for r in range(self.sub_table.rowCount()):
            try:
                start = float(self.sub_table.item(r, 0).text())
                end = float(self.sub_table.item(r, 1).text())
            except Exception:
                continue
            text = self.sub_table.item(r, 2)
            text = text.text() if text is not None else ""
            if text.strip():
                subs.append({"start": start, "end": end,
                             "text": text.strip()})
        shots = []
        for r in range(self.shot_table.rowCount()):
            try:
                secs = float(self.shot_table.item(r, 0).text())
            except Exception:
                secs = 4.0
            item = self.shot_table.item(r, 0)
            cam = item.data(Qt.UserRole) if item is not None else None
            if not isinstance(cam, dict):
                continue
            txt = self.shot_table.item(r, 1)
            txt = txt.text().strip() if txt is not None else ""
            shots.append({"seconds": max(0.5, secs),
                          "pos": cam.get("pos", [0.0, 2.0, 0.0]),
                          "look": cam.get("look", [0.0, 2.0, 1.0]),
                          "text": txt})
        data = {
            "enabled": self.enabled_check.isChecked(),
            "title": self.title_edit.text().strip(),
            "subtitle": self.sub_edit.text().strip(),
            "duration": float(self.dur_spin.value()),
            "fov": float(self.fov_spin.value()),
            "letterbox": self.letterbox_check.isChecked(),
            "subtitle_side": self.side_combo.currentData() or "bottom",
            "keys": keys,
            "subtitles": subs,
        }
        # Simple shots win: the engine derives the timeline from them.  The
        # advanced table stays available for precise per-key timing.
        if shots:
            data["shots"] = shots
            if not self.side_combo.currentData() == "bottom":
                data["subtitle_side"] = self.side_combo.currentData()
        return data


class _ViewportCaption(QLabel):
    """Viewport title bar. Double-clicking it maximises / restores the view."""

    def __init__(self, editor, view, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.view = view

    def mouseDoubleClickEvent(self, event):
        try:
            self.editor.toggle_maximize_view(self.view)
        except Exception:
            pass
        event.accept()


class HammerMainWindow(QMainWindow):
    def __init__(self, lang="en"):
        super().__init__()
        self.lang = lang if lang in LANGUAGES else "en"
        self.texts = LANGUAGES[self.lang]
        self._i18n = []
        _s = load_settings()
        self.theme = _s.get("theme") if _s.get("theme") in THEME_QSS else "dark"
        self.render_mode = "Textured"
        self.game_config = _s.get("game_config", GAME_CONFIGS[0])

        screen_geo = QApplication.primaryScreen().availableGeometry()
        sw, sh = screen_geo.width(), screen_geo.height()
        win_w = max(1100, min(int(sw * 0.92), sw - 40))
        win_h = max(700, min(int(sh * 0.90), sh - 60))
        self.resize(win_w, win_h)
        self.move(screen_geo.center() - self.rect().center())

        self.blocks = []; self.entities = []
        # Smooth height-field terrain patches (see engine_pkg.world.terrain).
        # They live beside the block list: {"pos", "half", "segments",
        # "heights", "texture"}.
        self.terrains = []
        self.spawn = (0.0, 0.0)
        # Optional cinematic intro (camera fly-through + title + subtitles).
        # Kept exactly as authored; Hammer writes it into the .hmmap and
        # compiles it into the .amap "cinematic" block.
        self.cinematic = {}
        s = load_settings()
        self.sky_name = s.get("sky", "day_clear.txt")
        self.current_map_path = None
        mats = scan_materials()
        # Prefer a neutral dev texture over whatever happens to sort first.
        preferred = ["dev/dev.png", "dev/dev_measurewall01b.png",
                     "metal/metalfloor003a.png"]
        default_tex = next((p for p in preferred if p in mats),
                           mats[0] if mats else "dev.png")
        saved_tex = s.get("texture")
        self.current_texture = saved_tex if saved_tex in mats else default_tex
        self.current_entity_type = ENTITY_TYPES[0] if ENTITY_TYPES else "info_player_start"
        self.tool_mode = "block"
        # Selection is a list of ("block"|"entity", index); the single-index
        # attributes below are kept in sync through properties.
        self.selection = []
        self.dirty = False
        self.current_is_water = False
        self.water_material = "sea"
        self.grid_size = int(s.get("grid", 8) or 8)
        self.snap_enabled = bool(s.get("snap", True))
        self.show_grid = bool(s.get("show_grid", True))
        self._undo = []
        self._redo = []
        self._undo_limit = 100
        self.clipboard = []
        self._paste_offset = 0

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._apply_theme()
        self._retranslate()
        self._install_shortcuts()
        self._apply_view_settings()
        self._restore_geometry(s)
        self._set_tool(self.tool_mode)
        self._update_window_title()
        # Hammer's Messages panel starts with the search paths it loaded.
        self.log_message(f"Search Path (PLATFORM): {get_materials_dir()}")
        self.log_message(f"Search Path (GAME):     {get_map_dir()}")
        self.log_message(f"Game configuration:     {self.game_config}")
        self.set_status(self.texts["ready_msg"])

    # ---------- 语言 / 主题 ----------
    def str(self, key, *args):
        return tr_text(self.lang, key, *args)

    def _reg(self, widget, setter, key):
        """Register a widget whose text follows the UI language."""
        self._i18n.append((widget, setter, key))
        try:
            getattr(widget, setter)(self.str(key))
        except Exception:
            pass
        return widget

    def _retranslate(self):
        self.texts = LANGUAGES.get(self.lang, LANGUAGES["en"])
        for widget, setter, key in self._i18n:
            try:
                getattr(widget, setter)(self.str(key))
            except Exception:
                pass
        for i, key in enumerate(getattr(self, "_vis_tab_keys", [])):
            try:
                self.vis_tabs.setTabText(i, self.str(key))
            except Exception:
                pass
        for lw in getattr(self, "_vis_lists", []):
            try:
                if lw.count():
                    lw.item(0).setText(self.str("vis_empty"))
            except Exception:
                pass
        self._update_status()
        self._update_window_title()
        self._refresh_captions()

    def _refresh_captions(self):
        """Show the map name and cinematic title in the 3D viewport caption.

        The window title bar is easy to miss (and is not visible in a
        maximised / fullscreen layout); this is the on-screen title the map
        author asked about.
        """
        for bar, key in getattr(self, "_caption_keys", []):
            try:
                text = self.str(key)
                if key == "camera":
                    name = "Untitled"
                    if self.current_map_path:
                        name = os.path.splitext(
                            os.path.basename(self.current_map_path))[0]
                    text = f"{text}  ·  {name}"
                    title = str((getattr(self, "cinematic", {}) or {}).get(
                        "title", "") or "").strip()
                    if title:
                        text = f"{text}  ·  “{title}”"
                bar.setText(text)
            except Exception:
                pass

    def _apply_theme(self):
        qss = THEME_QSS.get(self.theme, DARK_QSS)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss)
        self.setStyleSheet(qss)
        colors = THEME_COLORS.get(self.theme, THEME_COLORS["dark"])
        # Viewport chrome (captions, tool strip) is styled inline, so refresh it.
        for bar in getattr(self, "_caption_labels", []):
            bar.setStyleSheet(
                f"background-color:{colors['caption']};"
                f"color:{colors['caption_fg']};"
                f"border-bottom:1px solid {colors['caption_border']};"
                "padding-left:6px; font-size:8pt;")
        if hasattr(self, "_strip"):
            self._strip.setStyleSheet(
                f"background-color:{colors['strip']};"
                f"border-right:1px solid {colors['strip_border']};")
        if hasattr(self, "_msg_header"):
            self._msg_header.setStyleSheet(
                f"background-color:{colors['caption']};"
                f"color:{colors['caption_fg']};"
                f"border-top:1px solid {colors['caption_border']};"
                f"border-bottom:1px solid {colors['caption_border']};")
        for v in self._views():
            v.viewport_bg = colors["viewport_bg"]
            v.grid_color = colors["grid"]
            v.update()
        # Icons are drawn in code, so redraw them in the theme's foreground.
        for kind, btn in getattr(self, "_tool_icon_buttons", []):
            btn.setIcon(_tool_icon(kind, fg=colors["icon"]))
        for kind, act in getattr(self, "_toolbar_icon_actions", []):
            act.setIcon(_tool_icon(kind, fg=colors["icon"]))
        self.update()

    def slot_settings(self):
        dlg = SettingsDialog(self.lang, self.theme, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        lang, theme = dlg.values()
        changed = (lang != self.lang) or (theme != self.theme)
        self.lang = lang if lang in LANGUAGES else "en"
        self.theme = theme if theme in THEME_QSS else "dark"
        save_settings({"lang": self.lang, "theme": self.theme})
        self._apply_theme()
        self._retranslate()
        self.log_message(f"Settings: language={self.lang} theme={self.theme}")

    # ---------- 选择 ----------
    def _sync_from_selection(self):
        pass

    @property
    def selected_block_indices(self):
        return [i for k, i in self.selection if k == "block"]

    @property
    def selected_entity_indices(self):
        return [i for k, i in self.selection if k == "entity"]

    @property
    def selected_block_idx(self):
        for k, i in reversed(self.selection):
            if k == "block":
                return i
        return -1

    @selected_block_idx.setter
    def selected_block_idx(self, value):
        self.selection = [(k, i) for k, i in self.selection if k != "block"]
        if value is not None and int(value) >= 0:
            self.selection.append(("block", int(value)))

    @property
    def selected_entity_idx(self):
        for k, i in reversed(self.selection):
            if k == "entity":
                return i
        return -1

    @selected_entity_idx.setter
    def selected_entity_idx(self, value):
        self.selection = [(k, i) for k, i in self.selection if k != "entity"]
        if value is not None and int(value) >= 0:
            self.selection.append(("entity", int(value)))

    def select_only(self, kind, idx):
        self.selection = [(kind, int(idx))] if idx is not None and int(idx) >= 0 else []

    def toggle_select(self, kind, idx):
        key = (kind, int(idx))
        if key in self.selection:
            self.selection.remove(key)
        else:
            self.selection.append(key)

    def clear_selection(self):
        self.selection = []

    def select_all(self):
        self.selection = ([("block", i) for i in range(len(self.blocks))]
                          + [("entity", i) for i in range(len(self.entities))])
        self.refresh_all_views()
        self._update_status()

    # ---------- 撤销 / 重做 ----------
    def snapshot(self):
        try:
            return {
                "blocks": copy.deepcopy(self.blocks),
                "entities": copy.deepcopy(self.entities),
                "terrains": copy.deepcopy(self.terrains),
                "spawn": tuple(self.spawn),
                "sky": self.sky_name,
                "cinematic": copy.deepcopy(getattr(self, "cinematic", {})),
            }
        except Exception:
            return {
                "blocks": [copy.copy(b) for b in self.blocks],
                "entities": [copy.copy(e) for e in self.entities],
                "terrains": [copy.copy(t) for t in self.terrains],
                "spawn": tuple(self.spawn),
                "sky": self.sky_name,
                "cinematic": copy.deepcopy(getattr(self, "cinematic", {})),
            }

    def _apply_snapshot(self, snap):
        self.blocks = copy.deepcopy(snap["blocks"])
        self.entities = copy.deepcopy(snap["entities"])
        self.terrains = copy.deepcopy(snap.get("terrains", []))
        self.spawn = tuple(snap["spawn"])
        self.sky_name = snap["sky"]
        self.cinematic = copy.deepcopy(snap.get("cinematic",
                                                getattr(self, "cinematic", {})))
        try:
            self.sky_combo.setCurrentText(self.sky_name)
            self.spawn_label.setText(f"{self.spawn[0]:.1f}, {self.spawn[1]:.1f}")
        except Exception:
            pass
        self.clear_selection()
        self._drop_geometry_caches()
        self.mark_dirty()
        self.refresh_ui_lists()
        self.refresh_all_views()
        self._update_status()

    def push_undo(self, label=""):
        self._undo.append((label, self.snapshot()))
        if len(self._undo) > self._undo_limit:
            self._undo.pop(0)
        self._redo.clear()

    def push_undo_snapshot(self, label, snap):
        self._undo.append((label, snap))
        if len(self._undo) > self._undo_limit:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self):
        if not self._undo:
            self.set_status("Nothing to undo")
            return
        label, snap = self._undo.pop()
        self._redo.append((label, self.snapshot()))
        self._apply_snapshot(snap)
        self.set_status(f"Undo: {label}" if label else "Undo")

    def redo(self):
        if not self._redo:
            self.set_status("Nothing to redo")
            return
        label, snap = self._redo.pop()
        self._undo.append((label, self.snapshot()))
        self._apply_snapshot(snap)
        self.set_status(f"Redo: {label}" if label else "Redo")

    def _drop_geometry_caches(self):
        for v in self._views():
            try:
                v.clear_caches()
            except Exception:
                pass

    def _views(self):
        return (self.view_persp, self.view_top, self.view_front, self.view_side)

    def _restore_geometry(self, s):
        g = s.get("geometry")
        if not (isinstance(g, list) and len(g) == 4):
            return
        try:
            x, y, w, h = [int(v) for v in g]
            scr = QApplication.primaryScreen().availableGeometry()
            if w < 400 or h < 300:
                return
            # ignore saved geometry that would land off-screen
            if (x > scr.right() - 120 or y > scr.bottom() - 120
                    or x + w < scr.left() + 120 or y + h < scr.top() + 120):
                return
            self.setGeometry(x, y, w, h)
        except Exception:
            pass

    def set_status(self, msg, timeout=3000):
        """Show a transient hint in the centre of Hammer's status bar."""
        try:
            self.sel_label.setText(str(msg))
            self._status_timer.start(int(timeout))
        except Exception:
            pass

    def log_message(self, msg):
        """Append a line to the Messages panel."""
        try:
            self.messages.appendPlainText(str(msg))
        except Exception:
            pass

    def set_cursor_pos(self, p):
        try:
            text = (f"X {p[0]:+8.2f}   Y {p[1]:+8.2f}   Z {p[2]:+8.2f}")
            if self.coord_label.text() != text:
                self.coord_label.setText(text)
        except Exception:
            pass

    def _update_status(self):
        nb = len(self.selected_block_indices)
        ne = len(self.selected_entity_indices)
        total = nb + ne
        zh = self.lang == "zh"
        sep = "" if zh else " "
        joiner = "、" if zh else ", "
        try:
            if total == 0:
                self.sel_label.setText(self.str("no_selection"))
            else:
                parts = []
                if nb:
                    parts.append(f"{nb}{sep}{self.str('solid_suffix')}")
                if ne:
                    parts.append(f"{ne}{sep}{self.str('ent_suffix')}")
                self.sel_label.setText(
                    joiner.join(parts) + sep + self.str("selected_suffix"))
            self.snap_label.setText(
                f"{self.str('snap')}: "
                f"{self.str('on') if self.snap_enabled else self.str('off')}"
                f"   {self.str('grid')}: {float(self.grid_size):.1f}")
        except Exception:
            pass

    def mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self._update_window_title()

    def clear_dirty(self):
        self.dirty = False
        self._update_window_title()

    def _update_window_title(self):
        name = "Untitled"
        if self.current_map_path:
            name = os.path.splitext(os.path.basename(self.current_map_path))[0]
        star = " *" if self.dirty else ""
        self.setWindowTitle(
            f"Hammer - 32 Bit - [{name}] - [{self.render_mode}]{star}")
        try:
            self._refresh_captions()
        except Exception:
            pass

    def _prompt_save_if_dirty(self):
        if not self.dirty: return True
        box = QMessageBox(self)
        box.setWindowTitle(self.texts["save_prompt_title"])
        box.setText(self.texts["save_prompt_text"])
        btn_yes = box.addButton(self.texts["save_btn_yes"], QMessageBox.YesRole)
        btn_no  = box.addButton(self.texts["save_btn_no"],  QMessageBox.NoRole)
        box.setDefaultButton(btn_yes)
        box.exec_()
        if box.clickedButton() == btn_yes:
            self.slot_save_map()
            return not self.dirty
        return True

    def _viewport_frame(self, view, key):
        frame = QWidget()
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        bar = _ViewportCaption(self, view)
        bar.setFixedHeight(18)
        bar.setStyleSheet("background-color:#2b2b2b; color:#d8d8d8; padding-left:6px;"
                          "border-bottom:1px solid #1e1e1e; font-size:8pt;")
        self._reg(bar, "setText", key)
        self._caption_labels.append(bar)
        try:
            self._caption_keys.append((bar, key))
        except Exception:
            self._caption_keys = [(bar, key)]
        lay.addWidget(bar)
        lay.addWidget(view, stretch=1)
        try:
            self._view_frames[view] = frame
        except Exception:
            pass
        return frame

    def toggle_maximize_view(self, view):
        """Maximise / restore one viewport (Hammer's double-click behaviour)."""
        frames = getattr(self, "_view_frames", {}) or {}
        top_h = getattr(self, "_top_h", None)
        bot_h = getattr(self, "_bot_h", None)
        views_split = getattr(self, "_views_split", None)
        if not frames or top_h is None or bot_h is None:
            return
        if getattr(self, "_maximized_view", None) is view:
            for f in frames.values():
                f.show()
            top_h.show(); bot_h.show()
            top_h.setSizes([500, 500]); bot_h.setSizes([500, 500])
            if views_split is not None:
                views_split.setSizes([500, 500])
            self._maximized_view = None
            return
        # start from the restored layout
        for f in frames.values():
            f.show()
        top_h.show(); bot_h.show()
        if view in (self.view_persp, self.view_top):
            bot_h.hide()
            other = self.view_top if view is self.view_persp else self.view_persp
            if other in frames:
                frames[other].hide()
            top_h.setSizes([1000, 1] if view is self.view_persp else [1, 1000])
        else:
            top_h.hide()
            other = self.view_side if view is self.view_front else self.view_front
            if other in frames:
                frames[other].hide()
            bot_h.setSizes([1000, 1] if view is self.view_front else [1, 1000])
        self._maximized_view = view

    def _build_ui(self):
        self._caption_labels = []
        self._caption_keys = []
        self._tool_icon_buttons = []
        self._vis_tab_keys = []
        self._view_frames = {}
        self._maximized_view = None
        self.setStyleSheet(DARK_QSS)
        central = QWidget(); self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        body_split = QSplitter(Qt.Horizontal)
        body_split.setChildrenCollapsible(False)
        body_split.setHandleWidth(6)

        # -------- left tool strip (Hammer's icon column) --------
        strip = QWidget(); strip.setFixedWidth(30)
        self._strip = strip
        sl = QVBoxLayout(strip); sl.setContentsMargins(3, 3, 3, 3); sl.setSpacing(1)
        self.tool_buttons = {}
        for kind, mode, tip_key in [
                ("select", "pointer", "tip_select"),
                ("zoom", "zoom", "tip_zoom"),
                ("block", "block", "tip_block"),
                ("entity", "entity", "tip_entity"),
                ("cut", "cut", "tip_cut"),
                ("texture", "texture", "tip_texture"),
                ("light", "light", "tip_light"),
                ("water", "water", "tip_water"),
                ("player", "player", "tip_player"),
                ("grid", "grid", "tip_grid"),
                ("snap", "snap", "tip_snap"),
                ("camera", "camera", "tip_camera"),
                ("hand", "hand", "tip_hand")]:
            btn = QToolButton()
            btn.setIcon(_tool_icon(kind))
            btn.setIconSize(QSize(20, 20))
            btn.setFixedSize(24, 24)
            self._reg(btn, "setToolTip", tip_key)
            btn.setCheckable(mode in ("pointer", "block", "entity", "cut"))
            btn.clicked.connect(lambda _, m=mode: self._tool_button(m))
            if mode in ("pointer", "block", "entity", "cut"):
                self.tool_buttons[mode] = btn
            self._tool_icon_buttons.append((kind, btn))
            sl.addWidget(btn)
        sl.addStretch()
        body_split.addWidget(strip)

        # -------- viewports in nested splitters (each pane is resizable) ----
        self.view_persp = HammerGLWidget("perspective", self)
        self.view_top = HammerGLWidget("top", self)
        self.view_front = HammerGLWidget("front", self)
        self.view_side = HammerGLWidget("side", self)
        top_h = QSplitter(Qt.Horizontal)
        top_h.setChildrenCollapsible(False)
        top_h.setHandleWidth(6)
        top_h.addWidget(self._viewport_frame(self.view_persp, "camera"))
        top_h.addWidget(self._viewport_frame(self.view_top, "top"))
        bot_h = QSplitter(Qt.Horizontal)
        bot_h.setChildrenCollapsible(False)
        bot_h.setHandleWidth(6)
        bot_h.addWidget(self._viewport_frame(self.view_front, "front"))
        bot_h.addWidget(self._viewport_frame(self.view_side, "side"))
        views_split = QSplitter(Qt.Vertical)
        views_split.setChildrenCollapsible(False)
        views_split.setHandleWidth(6)
        views_split.addWidget(top_h)
        views_split.addWidget(bot_h)
        views_split.setSizes([500, 500])
        for sp in (top_h, bot_h):
            sp.setSizes([500, 500])
        self._top_h = top_h
        self._bot_h = bot_h
        self._views_split = views_split

        center = QSplitter(Qt.Vertical)
        center.setChildrenCollapsible(False)
        center.setHandleWidth(6)
        center.addWidget(views_split)

        msg_wrap = QWidget(); ml = QVBoxLayout(msg_wrap)
        ml.setContentsMargins(0, 0, 0, 0); ml.setSpacing(0)
        mh = QLabel(); mh.setFixedHeight(18)
        self._reg(mh, "setText", "messages")
        mh.setStyleSheet("background-color:#2b2b2b; color:#d8d8d8; padding-left:6px;"
                         "border-top:1px solid #1e1e1e; border-bottom:1px solid #1e1e1e;")
        self._msg_header = mh
        ml.addWidget(mh)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMinimumHeight(40)
        ml.addWidget(self.messages)
        center.addWidget(msg_wrap)
        center.setStretchFactor(0, 1)
        center.setStretchFactor(1, 0)
        center.setSizes([700, 100])
        body_split.addWidget(center)

        # -------- right panel --------
        right = QWidget(); right.setMinimumWidth(200)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6); rl.setSpacing(6)

        gb_sel = QGroupBox()
        self._reg(gb_sel, "setTitle", "select")
        gsl = QVBoxLayout(gb_sel); gsl.setContentsMargins(6, 14, 6, 6); gsl.setSpacing(3)
        self.select_buttons = {}
        for key, mode in (("groups", "group"), ("objects", "entity"),
                          ("solids", "block")):
            b = QPushButton(); b.setCheckable(True); b.setFixedHeight(22)
            self._reg(b, "setText", key)
            b.clicked.connect(lambda _, m=mode: self._set_tool(m))
            self.select_buttons[mode] = b
            gsl.addWidget(b)
        rl.addWidget(gb_sel)

        gb_tex = QGroupBox()
        self._reg(gb_tex, "setTitle", "current_texture")
        gtl = QVBoxLayout(gb_tex); gtl.setContentsMargins(6, 14, 6, 6); gtl.setSpacing(3)
        self.tex_combo = QComboBox(); self.tex_combo.addItems(scan_materials())
        if self.tex_combo.findText(self.current_texture) >= 0:
            self.tex_combo.setCurrentText(self.current_texture)
        self.tex_combo.currentTextChanged.connect(self._on_texture_changed)
        gtl.addWidget(self.tex_combo)
        self.tex_size = QLabel("")
        self.tex_size.setAlignment(Qt.AlignRight)
        gtl.addWidget(self.tex_size)
        self.tex_preview = QLabel()
        self.tex_preview.setMinimumSize(180, 92)
        self.tex_preview.setAlignment(Qt.AlignCenter)
        self.tex_preview.setStyleSheet(
            "background-color:#2b2b2b; border:1px solid #1e1e1e;")
        gtl.addWidget(self.tex_preview)
        trow = QHBoxLayout()
        b1 = QPushButton(); self._reg(b1, "setText", "browse")
        b2 = QPushButton(); self._reg(b2, "setText", "replace")
        b1.clicked.connect(self._browse_texture)
        b2.clicked.connect(self._replace_texture)
        trow.addWidget(b1); trow.addWidget(b2)
        gtl.addLayout(trow)
        rl.addWidget(gb_tex)
        self._update_texture_preview(self.current_texture)

        gb_vis = QGroupBox()
        self._reg(gb_vis, "setTitle", "visgroups")
        gvl = QVBoxLayout(gb_vis); gvl.setContentsMargins(6, 14, 6, 6); gvl.setSpacing(3)
        self.vis_tabs = QTabWidget()
        self._vis_lists = []
        for key in ("user", "auto", "cordon"):
            page = QWidget(); pl = QVBoxLayout(page)
            pl.setContentsMargins(2, 2, 2, 2)
            lw = QListWidget(); lw.setMinimumHeight(62)
            lw.addItem(self.str("vis_empty"))
            lw.setEnabled(False)
            pl.addWidget(lw)
            self._vis_lists.append(lw)
            self.vis_tabs.addTab(page, self.str(key))
            self._vis_tab_keys.append(key)
        gvl.addWidget(self.vis_tabs)
        vrow = QHBoxLayout()
        for key in ("show", "edit", "mark"):
            b = QPushButton(); b.setFixedHeight(20)
            self._reg(b, "setText", key)
            b.clicked.connect(lambda _, n=key: self._visgroup_action(key))
            vrow.addWidget(b)
        up = QPushButton("▲"); up.setFixedSize(22, 20)
        down = QPushButton("▼"); down.setFixedSize(22, 20)
        up.clicked.connect(lambda: self._visgroup_action("Move Up"))
        down.clicked.connect(lambda: self._visgroup_action("Move Down"))
        vrow.addWidget(up); vrow.addWidget(down)
        gvl.addLayout(vrow)
        rl.addWidget(gb_vis)

        mv = QHBoxLayout()
        mv.addWidget(self._reg(QLabel(), "setText", "move_selected"))
        b_tw = QPushButton(); self._reg(b_tw, "setText", "to_world")
        b_te = QPushButton(); self._reg(b_te, "setText", "to_entity")
        b_tw.clicked.connect(lambda: self._convert_selection(False))
        b_te.clicked.connect(lambda: self._convert_selection(True))
        mv.addWidget(b_tw); mv.addWidget(b_te)
        rl.addLayout(mv)

        cat = QHBoxLayout()
        cat.addWidget(self._reg(QLabel(), "setText", "categories"))
        self.cat_combo = QComboBox()
        self.cat_combo.addItems(self._entity_categories())
        self.cat_combo.currentTextChanged.connect(self._on_category_changed)
        cat.addWidget(self.cat_combo, stretch=1)
        rl.addLayout(cat)

        obj = QHBoxLayout()
        obj.addWidget(self._reg(QLabel(), "setText", "objects_label"))
        self.ent_combo = QComboBox()
        self.ent_combo.setMinimumWidth(150)
        self.ent_combo.currentTextChanged.connect(self._on_entity_type_changed)
        obj.addWidget(self.ent_combo, stretch=1)
        rl.addLayout(obj)
        self._on_category_changed(self.cat_combo.currentText())

        prow = QHBoxLayout()
        self.prefab_spin = QSpinBox(); self.prefab_spin.setRange(0, 999)
        self.prefab_spin.setFixedWidth(60)
        prow.addWidget(self.prefab_spin)
        b_prefab = QPushButton(); self._reg(b_prefab, "setText", "create_prefab")
        b_prefab.clicked.connect(self._create_prefab)
        prow.addWidget(b_prefab, stretch=1)
        rl.addLayout(prow)
        rl.addStretch()
        body_split.addWidget(right)
        body_split.setStretchFactor(0, 0)
        body_split.setStretchFactor(1, 1)
        body_split.setStretchFactor(2, 0)
        body_split.setSizes([30, 1000, 292])
        outer.addWidget(body_split, stretch=1)

        # -------- hidden compatibility widgets --------
        # Sky / spawn live in Map Properties and the object lists in Entity
        # Report; these keep the older code paths and syncing working.
        self.sky_combo = QComboBox(self)
        self.sky_combo.addItems(self._scan_skies())
        self.sky_combo.setCurrentText(self.sky_name)
        self.sky_combo.currentTextChanged.connect(self._on_sky_changed)
        self.spawn_label = QLabel(f"{self.spawn[0]:.1f}, {self.spawn[1]:.1f}", self)
        self.block_list = QListWidget(self)
        self.ent_list = QListWidget(self)
        for w in (self.sky_combo, self.spawn_label, self.block_list, self.ent_list):
            w.hide()

        # -------- status bar (For Help | selection | snap/grid) --------
        self.status = QStatusBar(); self.setStatusBar(self.status)
        self.status.setSizeGripEnabled(False)
        self.help_label = QLabel()
        self._reg(self.help_label, "setText", "for_help")
        self.sel_label = QLabel()
        self.snap_label = QLabel("")
        self.status.addWidget(self.help_label)
        self.status.addWidget(self.sel_label, 1)
        self.status.addPermanentWidget(self.snap_label)
        self.coord_label = QLabel("")
        self.coord_label.hide()
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._update_status)
        self._update_status()

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        st = self.style()
        self._toolbar_icon_actions = []

        def act(icon, key, fn, short=None):
            a = QAction(icon, "", self)
            self._reg(a, "setText", key)
            self._reg(a, "setToolTip", key)
            a.triggered.connect(fn)
            if short:
                a.setShortcut(QKeySequence(short))
            tb.addAction(a)
            return a

        act(st.standardIcon(QStyle.SP_FileIcon), "new", self.slot_new_map)
        act(st.standardIcon(QStyle.SP_DialogOpenButton), "open", self.slot_open_map)
        act(st.standardIcon(QStyle.SP_DialogSaveButton), "save", self.slot_save_map)
        tb.addSeparator()
        act(st.standardIcon(QStyle.SP_ArrowBack), "undo", self.undo)
        act(st.standardIcon(QStyle.SP_ArrowForward), "redo", self.redo)
        tb.addSeparator()
        for kind, mode, key in [("select", "pointer", "select"),
                                ("block", "block", "solids"),
                                ("entity", "entity", "objects"),
                                ("cut", "cut", "cut")]:
            a = QAction(_tool_icon(kind), "", self)
            self._reg(a, "setText", key)
            a.triggered.connect(lambda _, m=mode: self._set_tool(m))
            tb.addAction(a)
            self._toolbar_icon_actions.append((kind, a))
        tb.addSeparator()
        act(st.standardIcon(QStyle.SP_BrowserReload), "fit_view", self.fit_view)
        tb.addSeparator()
        tb.addWidget(self._reg(QLabel(" "), "setText", "grid_label"))
        self.grid_combo = QComboBox()
        self.grid_combo.addItems([str(v) for v in GRID_SIZES])
        self.grid_combo.setCurrentText(str(self.grid_size))
        self.grid_combo.setFixedWidth(64)
        self.grid_combo.currentTextChanged.connect(self._on_grid_size_changed)
        tb.addWidget(self.grid_combo)
        self.snap_check = QCheckBox()
        self._reg(self.snap_check, "setText", "snap")
        self.snap_check.setChecked(self.snap_enabled)
        self.snap_check.stateChanged.connect(self._on_snap_toggled)
        tb.addWidget(self.snap_check)
        self.grid_show_check = QCheckBox()
        self._reg(self.grid_show_check, "setText", "grid")
        self.grid_show_check.setChecked(self.show_grid)
        self.grid_show_check.stateChanged.connect(self._on_show_grid_toggled)
        tb.addWidget(self.grid_show_check)
        self.water_check = QCheckBox()
        self._reg(self.water_check, "setText", "toggle_water")
        self.water_check.setToolTip("New solids become water volumes")
        self.water_check.stateChanged.connect(self._on_water_toggle)
        tb.addWidget(self.water_check)
        tb.addSeparator()
        act(st.standardIcon(QStyle.SP_MediaPlay), "compile", self.slot_compile_map)
        act(st.standardIcon(QStyle.SP_FileDialogDetailedView), "settings",
            self.slot_settings)

    def _install_shortcuts(self):
        def sc(seq, fn):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(fn)
            return s
        sc("Ctrl+Z", self.undo)
        sc("Ctrl+Y", self.redo)
        sc("Ctrl+Shift+Z", self.redo)
        sc("Ctrl+A", self.select_all)
        sc("Ctrl+C", self.copy_selection)
        sc("Ctrl+V", self.paste_selection)
        sc("Ctrl+D", self.duplicate_selection)
        sc("Ctrl+T", self._replace_texture)
        sc("Ctrl+S", self.slot_save_map)
        sc("Ctrl+Shift+S", self.slot_save_as_map)
        sc("Ctrl+O", self.slot_open_map)
        sc("Ctrl+N", self.slot_new_map)
        sc("Ctrl+E", self._edit_selected_entity)
        sc("F", self.fit_view)
        sc("[", lambda: self._nudge_grid(-1))
        sc("]", lambda: self._nudge_grid(+1))
        sc("Esc", self._on_escape)

    def _apply_view_settings(self):
        for v in self._views():
            v.grid_step = self.grid_size
            v.snap_enabled = self.snap_enabled
            v.show_grid = self.show_grid
            v.update()

    def _on_escape(self):
        for v in self._views():
            try:
                v._exit_mouse_look()
            except Exception:
                pass
        self.clear_selection()
        self.refresh_all_views()
        self._update_status()

    # ---------- grid ----------
    def _on_grid_size_changed(self, text):
        try:
            size = int(float(text))
        except Exception:
            return
        self.grid_size = max(1, size)
        self._apply_view_settings()
        save_settings({"grid": self.grid_size})
        self._update_status()

    def _nudge_grid(self, direction):
        try:
            i = GRID_SIZES.index(self.grid_size)
        except ValueError:
            i = GRID_SIZES.index(8) if 8 in GRID_SIZES else 0
        i = max(0, min(len(GRID_SIZES) - 1, i + direction))
        self.grid_combo.setCurrentText(str(GRID_SIZES[i]))

    def _on_snap_toggled(self, state):
        self.snap_enabled = bool(state)
        self._apply_view_settings()
        save_settings({"snap": self.snap_enabled})
        self._update_status()

    def _on_show_grid_toggled(self, state):
        self.show_grid = bool(state)
        self._apply_view_settings()
        save_settings({"show_grid": self.show_grid})

    def _on_entity_type_changed(self, name):
        self.current_entity_type = name
        self._set_tool("entity")

    # ---------- list selection ----------
    def _on_block_list_clicked(self, item):
        row = self.block_list.row(item)
        if 0 <= row < len(self.blocks):
            self.select_only("block", row)
            self.refresh_all_views()
            self._update_status()

    def _on_block_list_double(self, item):
        row = self.block_list.row(item)
        if 0 <= row < len(self.blocks):
            self.view_persp.resize_block(row)

    def _on_ent_list_clicked(self, item):
        row = self.ent_list.row(item)
        if 0 <= row < len(self.entities):
            self.select_only("entity", row)
            self.refresh_all_views()
            self._update_status()

    def _on_ent_list_double(self, item):
        row = self.ent_list.row(item)
        if 0 <= row < len(self.entities):
            self.view_persp.edit_entity(row)

    def _edit_selected_entity(self):
        idx = self.selected_entity_idx
        if idx < 0:
            self.set_status("Select an entity first")
            return
        self.view_persp.edit_entity(idx)

    # ---------- clipboard ----------
    def copy_selection(self):
        items = []
        for i in self.selected_block_indices:
            if 0 <= i < len(self.blocks):
                items.append(("block", copy.deepcopy(self.blocks[i])))
        for i in self.selected_entity_indices:
            if 0 <= i < len(self.entities):
                items.append(("entity", copy.deepcopy(self.entities[i])))
        self.clipboard = items
        self._paste_offset = 0
        self.set_status(f"Copied {len(items)} item(s)")

    def paste_selection(self):
        if not self.clipboard:
            self.set_status("Clipboard is empty")
            return
        self.push_undo("Paste")
        self._paste_offset += self.grid_size
        off = self._paste_offset
        new_sel = []
        for kind, obj in self.clipboard:
            if kind == "block":
                b = copy.deepcopy(obj)
                if Brush and isinstance(b, Brush):
                    if hasattr(b, "user_data"):
                        m = b.user_data.get("model", glm.mat4())
                        m[3] = glm.vec4(m[3][0] + off, m[3][1], m[3][2] + off, 1.0)
                        b.user_data["model"] = m
                else:
                    b[0] += off; b[2] += off
                self.blocks.append(b)
                new_sel.append(("block", len(self.blocks) - 1))
            else:
                e = copy.deepcopy(obj)
                e[1] += off; e[3] += off
                self.entities.append(e)
                new_sel.append(("entity", len(self.entities) - 1))
        self.selection = new_sel
        self.mark_dirty()
        self.refresh_ui_lists()
        self.refresh_all_views()
        self._update_status()

    def duplicate_selection(self):
        self.copy_selection()
        self._paste_offset = 0
        self.paste_selection()

    def delete_selection(self):
        blocks = sorted(self.selected_block_indices, reverse=True)
        ents = sorted(self.selected_entity_indices, reverse=True)
        if not blocks and not ents:
            return
        self.push_undo("Delete")
        for i in blocks:
            if 0 <= i < len(self.blocks):
                del self.blocks[i]
        for i in ents:
            if 0 <= i < len(self.entities):
                del self.entities[i]
        self.clear_selection()
        self.mark_dirty()
        self.refresh_ui_lists()
        self.refresh_all_views()
        self._update_status()

    def rotate_selection(self):
        blocks = self.selected_block_indices
        if not blocks:
            self.set_status("Select a block to rotate")
            return
        self.push_undo("Rotate 90°")
        for i in blocks:
            b = self.blocks[i]
            if Brush and isinstance(b, Brush):
                continue
            # swap X/Z half extents (90° around Y)
            b[3], b[5] = b[5], b[3]
        self.mark_dirty()
        self.refresh_ui_lists()
        self.refresh_all_views()
        self._update_status()

    def fit_view(self):
        bounds = []
        for b in self.blocks:
            try:
                if Brush and isinstance(b, Brush):
                    mn, mx = b.get_aabb()
                    bounds.append((float(mn[0]), float(mn[1]), float(mn[2]),
                                   float(mx[0]), float(mx[1]), float(mx[2])))
                else:
                    x, y, z, sx, sy, sz = b[:6]
                    bounds.append((x - sx, y - sy, z - sz, x + sx, y + sy, z + sz))
            except Exception:
                pass
        for e in self.entities:
            try:
                bounds.append((e[1] - 0.5, e[2], e[3] - 0.5,
                               e[1] + 0.5, e[2] + 1.0, e[3] + 0.5))
            except Exception:
                pass
        if not bounds:
            self.set_status("Nothing to fit")
            return
        mnx = min(b[0] for b in bounds); mny = min(b[1] for b in bounds)
        mnz = min(b[2] for b in bounds)
        mxx = max(b[3] for b in bounds); mxy = max(b[4] for b in bounds)
        mxz = max(b[5] for b in bounds)
        cx = (mnx + mxx) * 0.5; cy = (mny + mxy) * 0.5; cz = (mnz + mxz) * 0.5
        span = max(mxx - mnx, mxy - mny, mxz - mnz, 1.0)
        self.view_top.offset_x = cx; self.view_top.offset_y = cz
        self.view_front.offset_x = cx; self.view_front.offset_y = cy
        self.view_side.offset_x = cz; self.view_side.offset_y = cy
        for v in (self.view_top, self.view_front, self.view_side):
            v.scale = span * 0.8
            v.update()
        p = self.view_persp
        dist = span * 1.6
        d = np.array([0.55, 0.62, 0.55], dtype=np.float32)
        d /= max(1e-6, float(np.linalg.norm(d)))
        p.cam_x = cx + float(d[0]) * dist
        p.cam_y = cy + float(d[1]) * dist
        p.cam_z = cz + float(d[2]) * dist
        # Aim the free camera at the scene centre (the camera has no look-at
        # target, only yaw/pitch, so derive them from the look vector).
        look = -d
        p.pitch = float(np.arcsin(np.clip(look[1], -1.0, 1.0)))
        p.yaw = float(np.arctan2(look[0], look[2]))
        p.update()
        self.set_status("Fit view")

    def _build_menu(self):
        menubar = self.menuBar()

        def add(menu, key, slot, shortcut=None):
            a = QAction("", self)
            self._reg(a, "setText", key)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            menu.addAction(a)
            return a

        def sub(menu, key):
            m = menu.addMenu("")
            self._reg(m, "setTitle", key)
            return m

        fm = sub(menubar, "menu_file")
        add(fm, "new", self.slot_new_map, "Ctrl+N")
        add(fm, "open", self.slot_open_map, "Ctrl+O")
        add(fm, "save", self.slot_save_map, "Ctrl+S")
        add(fm, "save_as", self.slot_save_as_map, "Ctrl+Shift+S")
        fm.addSeparator()
        add(fm, "close", self.slot_close_map)
        fm.addSeparator()
        add(fm, "exit", self.close)

        em = sub(menubar, "menu_edit")
        add(em, "undo", self.undo, "Ctrl+Z")
        add(em, "redo", self.redo, "Ctrl+Y")
        em.addSeparator()
        add(em, "cut", self.copy_selection)
        add(em, "copy", self.copy_selection, "Ctrl+C")
        add(em, "paste", self.paste_selection, "Ctrl+V")
        add(em, "delete", self.delete_selection, "Del")
        add(em, "duplicate", self.duplicate_selection, "Ctrl+D")
        em.addSeparator()
        add(em, "select_all", self.select_all, "Ctrl+A")
        add(em, "clear_selection", self._on_escape)
        em.addSeparator()
        add(em, "properties", self._edit_selected_entity, "Ctrl+E")

        mm = sub(menubar, "menu_map")
        add(mm, "map_properties", self.slot_map_properties)
        add(mm, "cinematic", self.slot_cinematic)
        add(mm, "entity_report", self.slot_entity_report)
        add(mm, "check_problems", self.slot_check_problems)
        mm.addSeparator()
        add(mm, "compile", self.slot_compile_map, "F9")
        add(mm, "run_map", self.slot_run_map, "F5")

        vm = sub(menubar, "menu_view")
        add(vm, "fit_view", self.fit_view, "F")
        vm.addSeparator()
        add(vm, "show_grid", lambda: self.grid_show_check.toggle())
        add(vm, "snap_to_grid", lambda: self.snap_check.toggle())
        add(vm, "grid_larger", lambda: self._nudge_grid(+1))
        add(vm, "grid_smaller", lambda: self._nudge_grid(-1))
        vm.addSeparator()
        add(vm, "textured", lambda: self._set_render_mode("Textured"))
        add(vm, "wireframe", lambda: self._set_render_mode("Wireframe"))

        tm = sub(menubar, "menu_tools")
        add(tm, "apply_texture", self._replace_texture, "Ctrl+T")
        add(tm, "rotate90", self.rotate_selection)
        tm.addSeparator()
        add(tm, "hollow", self.slot_hollow)
        add(tm, "carve", self.slot_carve)
        add(tm, "cut_doorway", self.slot_cut_doorway)
        add(tm, "terrain", self.slot_terrain)
        add(tm, "array", self.slot_array)
        add(tm, "mirror", self.slot_mirror)
        align_menu = sub(tm, "align")
        for axis in ("x", "y", "z"):
            for edge in ("min", "center", "max"):
                key = f"align_{axis}_{edge}"
                add(align_menu, key,
                    lambda a=axis, e=edge: self.align_selection(a, e))
        add(tm, "snap_selection", self.snap_selection_to_grid)
        tm.addSeparator()
        add(tm, "load_prefab", self.slot_load_prefab)
        add(tm, "toggle_water", lambda: self.water_check.toggle())
        add(tm, "water_sea", lambda: self.set_water_material("sea"))
        add(tm, "water_river", lambda: self.set_water_material("river"))
        add(tm, "edit_entity", self._edit_selected_entity)
        tm.addSeparator()
        add(tm, "settings", self.slot_settings, "Ctrl+,")

        im = sub(menubar, "menu_instancing")
        self.instancing_action = QAction("", self)
        self._reg(self.instancing_action, "setText", "enable_instancing")
        self.instancing_action.setCheckable(True)
        self.instancing_action.triggered.connect(
            lambda on: self.set_status(
                f"Instancing {'enabled' if on else 'disabled'} (preview only)"))
        im.addAction(self.instancing_action)

        wm = sub(menubar, "menu_window")
        add(wm, "reset_layout", self._reset_layout)
        add(wm, "messages", lambda: self.messages.setVisible(
            not self.messages.isVisible()))

        hm = sub(menubar, "menu_help")
        add(hm, "shortcuts", self._show_shortcuts)
        add(hm, "about", self._show_about)

    def slot_map_properties(self):
        dlg = MapPropertiesDialog(self, self._scan_skies(), self.sky_name,
                                  self.spawn, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        sky, spawn = dlg.values()
        self.push_undo("Map properties")
        self.sky_name = sky
        self.spawn = (float(spawn[0]), float(spawn[1]))
        self.spawn_label.setText(f"{self.spawn[0]:.1f}, {self.spawn[1]:.1f}")
        self.sky_combo.setCurrentText(sky)
        self._on_sky_changed(sky)
        self.mark_dirty()
        self.refresh_all_views()
        self.log_message(f"Map properties: sky={sky} spawn={self.spawn}")

    def slot_cinematic(self):
        dlg = CinematicDialog(self.cinematic, self._view_camera, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.values()
        self.push_undo("Cinematic intro")
        self.cinematic = data
        self.mark_dirty()
        self.log_message(
            f"Cinematic: {len(data.get('keys', []))} camera key(s), "
            f"{len(data.get('subtitles', []))} subtitle(s), "
            f"title={data.get('title')!r}")
        self.set_status("Cinematic intro updated - compile with F9")

    def _view_camera(self):
        """Current 3D viewport eye + a look-at point 12 m down the view ray."""
        v = self.view_persp
        pos = (float(v.cam_x), float(v.cam_y), float(v.cam_z))
        d = v._camera_vectors()
        dist = 12.0
        look = (pos[0] + float(d[0]) * dist,
                pos[1] + float(d[1]) * dist,
                pos[2] + float(d[2]) * dist)
        return pos, look

    def slot_entity_report(self):
        EntityReportDialog(self).exec_()

    def slot_check_problems(self):
        issues = []
        if not any(e[0] == "info_player_start" for e in self.entities):
            issues.append("No info_player_start: the map has no spawn point.")
        if not any(e[0] == "light_environment" for e in self.entities):
            issues.append("No light_environment: lighting will fall back to ambient.")
        if not self.blocks:
            issues.append("The map has no solids.")
        for i, b in enumerate(self.blocks):
            if Brush and isinstance(b, Brush):
                continue
            if any(float(v) <= 0.0 for v in b[3:6]):
                issues.append(f"Solid #{i} has a zero or negative size.")
            tex = b[6] if len(b) > 6 else None
            if not tex or not os.path.exists(material_full_path(str(tex))):
                issues.append(f"Solid #{i} uses a missing texture: {tex}")
        self.log_message(f"Check for Problems: {len(issues)} issue(s)")
        if issues:
            QMessageBox.warning(self, "Check for Problems",
                                "\n".join(issues[:24]))
        else:
            QMessageBox.information(self, "Check for Problems",
                                    "No problems found.")

    def slot_run_map(self):
        self._do_compile(quiet=True)
        name = ""
        if self.current_map_path:
            name = os.path.splitext(os.path.basename(self.current_map_path))[0]
        self.log_message(f'Run Map: compile "{name}" then use  map {name}  in the console.')
        self.set_status(f'Run Map: use  map {name}  in the engine console')

    def _show_shortcuts(self):
        QMessageBox.information(self, "Keyboard Shortcuts", "\n".join([
            "Ctrl+Z / Ctrl+Y   undo / redo",
            "Ctrl+A            select all",
            "Ctrl+C / Ctrl+V   copy / paste",
            "Ctrl+D            duplicate",
            "Delete            delete selection",
            "Shift+Click       add / remove from selection",
            "F                 fit view",
            "[ / ]             grid size down / up",
            "F9                compile map",
            "F5                run map",
            "Ctrl+T            apply current texture to selection",
            "Ctrl+E            edit selected entity",
            "Esc               clear selection",
            "",
            "3D view: RMB drag look · WASD fly · Space / Shift up / down",
            "2D views: LMB drag object move · MMB or Space drag pan",
            "Wheel             zoom about the cursor",
        ]))

    def _scan_skies(self):
        sky_dir = get_sky_dir(); names = []
        if os.path.isdir(sky_dir):
            for f in sorted(os.listdir(sky_dir)):
                if f.lower().endswith(".txt"): names.append(f)
        return names if names else ["day_clear.txt"]

    def _set_tool(self, mode):
        if mode not in ("block", "entity", "group", "pointer", "cut"): mode = "block"
        self.tool_mode = mode
        for v in self._views():
            try: v._exit_mouse_look()
            except Exception: pass
            if mode != "cut":
                v.cut_selected_idx = -1
            v.setCursor(Qt.ArrowCursor)
        for m, btn in getattr(self, "tool_buttons", {}).items():
            btn.setChecked(m == mode)
        for m, btn in getattr(self, "select_buttons", {}).items():
            btn.setChecked(m == mode)
        self._update_status()

    # ---------- Hammer tool strip ----------
    def _tool_button(self, mode):
        if mode == "zoom":
            self.set_status("Magnify: use the mouse wheel to zoom the 2D views")
            return
        if mode == "texture":
            self._replace_texture(); return
        if mode == "light":
            self._place_entity_quick("light_environment"); return
        if mode == "water":
            self.water_check.toggle(); return
        if mode == "player":
            self._place_entity_quick("info_player_start"); return
        if mode == "grid":
            self._nudge_grid(+1); return
        if mode == "snap":
            self.snap_check.toggle(); return
        if mode == "camera":
            self.view_persp.setFocus()
            self.set_status("3D camera: RMB drag to look, WASD to fly")
            return
        if mode == "hand":
            self._set_tool("pointer"); return
        self._set_tool(mode)

    def _view_center_world(self):
        v = self.view_top
        return (float(v.offset_x), 0.0, float(v.offset_y))

    def _place_entity_quick(self, etype):
        self.current_entity_type = etype
        idx = self.ent_combo.findText(etype)
        if idx >= 0:
            self.ent_combo.blockSignals(True)
            self.ent_combo.setCurrentIndex(idx)
            self.ent_combo.blockSignals(False)
        self._set_tool("entity")
        p = self._view_center_world()
        self.push_undo("Add entity")
        edef = ENTITY_DEFS.get(etype, {})
        self.entities.append([etype, p[0], p[1], p[2], None,
                              dict(edef.get("params", {}))])
        self._push_entity_out_of_solids(self.entities[-1])
        self.select_only("entity", len(self.entities) - 1)
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(f"Placed {etype} at ({p[0]:.1f}, {p[1]:.1f}, {p[2]:.1f})")

    def _entity_categories(self):
        cats = set()
        for d in ENTITY_DEFS.values():
            c = d.get("category")
            if c:
                cats.add(c)
        return ["All"] + sorted(cats)

    def _on_category_changed(self, cat):
        if not hasattr(self, "ent_combo"):
            return
        cur = self.ent_combo.currentText()
        self.ent_combo.blockSignals(True)
        self.ent_combo.clear()
        for name in ENTITY_TYPES:
            if cat and cat != "All":
                if ENTITY_DEFS.get(name, {}).get("category") != cat:
                    continue
            self.ent_combo.addItem(name)
        idx = self.ent_combo.findText(cur)
        if idx >= 0:
            self.ent_combo.setCurrentIndex(idx)
        self.ent_combo.blockSignals(False)
        if self.ent_combo.count():
            self.current_entity_type = self.ent_combo.currentText()

    def _visgroup_action(self, name):
        self.set_status(f"VisGroups: {name} (this map format has no visgroups)")

    def _convert_selection(self, to_entity):
        self.set_status("To World / To Entity: not supported by the .amap format")

    def _create_prefab(self):
        if not self.selection:
            self.set_status("Select something to create a prefab from")
            return
        prefab_dir = os.path.join(PROJECT_ROOT, "prefabs")
        os.makedirs(prefab_dir, exist_ok=True)
        n = int(self.prefab_spin.value())
        path = os.path.join(prefab_dir, f"prefab_{n:03d}.hprefab")
        blocks = [copy.deepcopy(self.blocks[i]) for i in self.selected_block_indices]
        ents = [copy.deepcopy(self.entities[i]) for i in self.selected_entity_indices]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(_jsonable({"blocks": blocks, "entities": ents}),
                          f, indent=2)
            self.prefab_spin.setValue(n + 1)
            self.log_message(f"Prefab saved: {path}")
            self.set_status(f"Prefab saved: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "Create Prefab", str(e))

    # ---------- 实用工具：掏空 / 阵列 / 对齐 / 吸附 ----------
    def slot_hollow(self):
        idx = self.selected_block_idx
        if idx < 0 or idx >= len(self.blocks):
            self.set_status("Select one solid to hollow")
            return
        b = self.blocks[idx]
        if Brush and isinstance(b, Brush):
            self.set_status("Hollow: Brush objects are not supported")
            return
        dlg = HollowDialog(self, self.grid_size, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        t = max(0.1, float(dlg.thickness()))
        x, y, z, sx, sy, sz = b[:6]
        tex = b[6] if len(b) > 6 else self.current_texture
        if min(sx, sy, sz) <= t * 1.2:
            self.set_status("Hollow: the solid is too small for that thickness")
            return
        self.push_undo("Hollow")
        parts = [
            [x, y - sy + t / 2, z, sx, t / 2, sz],
            [x, y + sy - t / 2, z, sx, t / 2, sz],
            [x - sx + t / 2, y, z, t / 2, max(t / 2, sy - t), max(t / 2, sz - t)],
            [x + sx - t / 2, y, z, t / 2, max(t / 2, sy - t), max(t / 2, sz - t)],
            [x, y, z - sz + t / 2, max(t / 2, sx - t), max(t / 2, sy - t), t / 2],
            [x, y, z + sz - t / 2, max(t / 2, sx - t), max(t / 2, sy - t), t / 2],
        ]
        del self.blocks[idx]
        start = len(self.blocks)
        for p in parts:
            self.blocks.append([float(v) for v in p] + [tex, None, False])
        self.selection = [("block", i) for i in range(start, len(self.blocks))]
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(f"Hollow: wall thickness {t:.2f}, 6 solids")

    def slot_carve(self):
        """Subtract the selected solid from every solid it overlaps.

        Axis-aligned blocks are split into up to six non-overlapping pieces
        around the cutter, which is exactly what a box-based map needs and
        avoids pulling a full CSG triangulation into the editor.
        """
        idx = self.selected_block_idx
        if idx < 0 or idx >= len(self.blocks):
            self.set_status("Select the cutting solid first")
            return
        cutter = self.blocks[idx]
        if Brush and isinstance(cutter, Brush):
            self.set_status("Carve: Brush objects are not supported")
            return
        cx, cy, cz, chx, chy, chz = (float(v) for v in cutter[:6])
        self.push_undo("Carve")
        out = []
        carved = 0
        for i, b in enumerate(self.blocks):
            if i == idx:
                continue
            if Brush and isinstance(b, Brush):
                out.append(b)
                continue
            x, y, z, hx, hy, hz = (float(v) for v in b[:6])
            ix0 = max(x - hx, cx - chx); ix1 = min(x + hx, cx + chx)
            iy0 = max(y - hy, cy - chy); iy1 = min(y + hy, cy + chy)
            iz0 = max(z - hz, cz - chz); iz1 = min(z + hz, cz + chz)
            if ix0 >= ix1 or iy0 >= iy1 or iz0 >= iz1:
                out.append(b)
                continue
            tex = b[6] if len(b) > 6 else self.current_texture
            carved += 1

            def piece(x0, x1, y0, y1, z0, z1):
                if (x1 - x0) <= 1e-4 or (y1 - y0) <= 1e-4 or (z1 - z0) <= 1e-4:
                    return
                out.append([
                    (x0 + x1) * 0.5, (y0 + y1) * 0.5, (z0 + z1) * 0.5,
                    (x1 - x0) * 0.5, (y1 - y0) * 0.5, (z1 - z0) * 0.5,
                    tex, None, False])

            piece(x - hx, ix0, y - hy, y + hy, z - hz, z + hz)   # -X
            piece(ix1, x + hx, y - hy, y + hy, z - hz, z + hz)   # +X
            piece(ix0, ix1, y - hy, y + hy, z - hz, iz0)         # -Z
            piece(ix0, ix1, y - hy, y + hy, iz1, z + hz)         # +Z
            piece(ix0, ix1, y - hy, iy0, iz0, iz1)               # -Y
            piece(ix0, ix1, iy1, y + hy, iz0, iz1)               # +Y

        self.blocks[:] = out
        self.selected_block_idx = -1
        self.selection = []
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(f"Carve: {carved} solid(s) cut")

    def slot_mirror(self):
        if not self.selection:
            self.set_status("Select something to mirror")
            return
        # Mirror across the map's X = 0 plane: flipping a symmetric selection
        # around its own centre would just duplicate everything in place.
        cx = 0.0
        self.push_undo("Mirror")
        new_sel = []
        for kind, i in list(self.selection):
            if kind == "block" and 0 <= i < len(self.blocks):
                b = copy.deepcopy(self.blocks[i])
                if Brush and isinstance(b, Brush):
                    continue
                b[0] = 2.0 * cx - float(b[0])
                self.blocks.append(b)
                new_sel.append(("block", len(self.blocks) - 1))
            elif kind == "entity" and 0 <= i < len(self.entities):
                e = copy.deepcopy(self.entities[i])
                e[1] = 2.0 * cx - float(e[1])
                self.entities.append(e)
                new_sel.append(("entity", len(self.entities) - 1))
        self.selection = new_sel
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(f"Mirror: {len(new_sel)} object(s) across X={cx:.1f}")

    @staticmethod
    def _subtract_box(box, cutter):
        """Split `box` around `cutter`, returning up to six boxes (or None)."""
        x, y, z, sx, sy, sz = (float(v) for v in box)
        cx, cy, cz, chx, chy, chz = (float(v) for v in cutter)
        ix0 = max(x - sx, cx - chx); ix1 = min(x + sx, cx + chx)
        iy0 = max(y - sy, cy - chy); iy1 = min(y + sy, cy + chy)
        iz0 = max(z - sz, cz - chz); iz1 = min(z + sz, cz + chz)
        if ix0 >= ix1 or iy0 >= iy1 or iz0 >= iz1:
            return None
        out = []

        def piece(x0, x1, y0, y1, z0, z1):
            if x1 - x0 <= 1e-4 or y1 - y0 <= 1e-4 or z1 - z0 <= 1e-4:
                return
            out.append(((x0 + x1) * 0.5, (y0 + y1) * 0.5, (z0 + z1) * 0.5,
                        (x1 - x0) * 0.5, (y1 - y0) * 0.5, (z1 - z0) * 0.5))

        piece(x - sx, ix0, y - sy, y + sy, z - sz, z + sz)
        piece(ix1, x + sx, y - sy, y + sy, z - sz, z + sz)
        piece(ix0, ix1, y - sy, y + sy, z - sz, iz0)
        piece(ix0, ix1, y - sy, y + sy, iz1, z + sz)
        piece(ix0, ix1, y - sy, iy0, iz0, iz1)
        piece(ix0, ix1, iy1, y + sy, iz0, iz1)
        return out

    def slot_cut_doorway(self):
        """Cut a crawl-through opening through the selected wall."""
        sel = self.selected_block_indices
        if len(sel) != 1 or not (0 <= sel[0] < len(self.blocks)):
            self.set_status("Select one wall to cut a doorway through")
            return
        b = self.blocks[sel[0]]
        dlg = DoorwayDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        width, height = dlg.values()
        x, y, z, sx, sy, sz = (float(v) for v in b[:6])
        tex = b[6] if len(b) > 6 else self.current_texture
        # The cutter runs through the wall's thin horizontal axis.
        if sx <= sz:
            chx = sx + 0.2
            chz = min(width * 0.5, max(0.1, sz * 0.45))
        else:
            chx = min(width * 0.5, max(0.1, sx * 0.45))
            chz = sz + 0.2
        cy = (y - sy) + height * 0.5
        cutter = (x, cy, z, chx, height * 0.5, chz)
        parts = self._subtract_box((x, y, z, sx, sy, sz), cutter)
        if parts is None:
            self.set_status("The doorway does not intersect that wall")
            return
        self.push_undo("Cut doorway")
        idx = self.blocks.index(b)
        del self.blocks[idx]
        start = len(self.blocks)
        for p in parts:
            self.blocks.append([float(v) for v in p] + [tex, None, False])
        self.selection = [("block", k) for k in range(start, len(self.blocks))]
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(
            f"Doorway {width:.1f} x {height:.1f} cut ({len(parts)} solids)")

    def slot_terrain(self):
        """Create a grid of blocks with a rolling height field."""
        dlg = TerrainDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        size_x, size_z, seg, hill, seed = dlg.values()
        seg = max(2, int(seg))
        self.push_undo("Create terrain")
        tex = self.current_texture
        if dlg.is_smooth():
            self._create_smooth_terrain(float(size_x), float(size_z),
                                        max(4, min(128, seg)), float(hill),
                                        int(seed), tex)
            return
        base = -2.0
        cell_x = (2.0 * float(size_x)) / seg
        cell_z = (2.0 * float(size_z)) / seg
        start = len(self.blocks)
        for j in range(seg):
            for i in range(seg):
                wx = -float(size_x) + (i + 0.5) * cell_x
                wz = -float(size_z) + (j + 0.5) * cell_z
                h = float(hill) * (
                    0.55
                    + 0.45 * math.sin(wx * 0.35 + seed)
                    * math.cos(wz * 0.31 - seed)
                    + 0.25 * math.sin(wx * 0.9 + wz * 0.6 + seed * 1.7))
                top = max(0.15, h)
                cy = (top + base) * 0.5
                hy = (top - base) * 0.5
                self.blocks.append([wx, cy, wz, cell_x * 0.5, hy,
                                    cell_z * 0.5, tex, None, False])
        self.selection = [("block", k) for k in range(start, len(self.blocks))]
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(
            f"Terrain {size_x:.0f} x {size_z:.0f}, {seg}x{seg} cells")

    def _create_smooth_terrain(self, size_x, size_z, seg, hill, seed, tex):
        """Create a height-field terrain patch (walkable slopes, one mesh)."""
        heights = []
        for j in range(seg + 1):
            wz = -size_z + (2.0 * size_z) * j / seg
            row = []
            for i in range(seg + 1):
                wx = -size_x + (2.0 * size_x) * i / seg
                h = hill * (
                    0.55
                    + 0.45 * math.sin(wx * 0.35 + seed)
                    * math.cos(wz * 0.31 - seed)
                    + 0.25 * math.sin(wx * 0.9 + wz * 0.6 + seed * 1.7))
                # fade to 0 at the patch border so it blends into the floor
                ex = abs(wx) / max(1e-6, size_x)
                ez = abs(wz) / max(1e-6, size_z)
                edge = max(0.0, 1.0 - max(ex, ez) ** 6)
                row.append(max(0.0, h * edge))
            heights.append(row)
        self.terrains.append({
            "pos": [0.0, 0.0, 0.0],
            "half": [float(size_x), float(hill), float(size_z)],
            "segments": int(seg),
            "heights": heights,
            "texture": tex or "nature/meadow_grass.png",
        })
        self.mark_dirty()
        self._drop_geometry_caches()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(
            f"Smooth terrain {size_x:.0f} x {size_z:.0f}, {seg}x{seg} cells, "
            f"hill {hill:.1f}")

    def set_water_material(self, mat):
        """Choose the water material (sea / river) for new and selected brushes."""
        mat = str(mat or "sea").lower()
        if mat not in ("sea", "river"):
            mat = "sea"
        self.water_material = mat
        n = 0
        for kind, i in list(self.selection):
            if kind != "block" or not (0 <= i < len(self.blocks)):
                continue
            b = self.blocks[i]
            if not isinstance(b, list):
                continue
            is_water = ((len(b) > 8 and b[8])
                        or (len(b) > 6 and is_water_texture(b[6])))
            if not is_water:
                continue
            while len(b) < 10:
                b.append(None)
            b[9] = mat
            n += 1
        self.mark_dirty()
        try:
            self.set_status(f"Water material: {mat} ({n} selected)")
        except Exception:
            pass
        self.log_message(
            f"Water material = {mat}; {n} selected water brush(es) updated")

    def _push_entity_out_of_solids(self, e):
        """Nudge an entity out of any solid it was dropped or spawned inside.

        NPCs placed inside a wall were stuck in game; this snaps them to the
        nearest free side of the block instead.
        """
        try:
            ex, ey, ez = float(e[1]), float(e[2]), float(e[3])
        except Exception:
            return False

        def bounds(b):
            if Brush and isinstance(b, Brush):
                if hasattr(b, "user_data") and "model" in b.user_data:
                    m = b.user_data["model"]
                    c = (float(m[3][0]), float(m[3][1]), float(m[3][2]))
                    hx = float(glm.length(glm.vec3(m[0][0], m[1][0], m[2][0])))
                    hy = float(glm.length(glm.vec3(m[0][1], m[1][1], m[2][1])))
                    hz = float(glm.length(glm.vec3(m[0][2], m[1][2], m[2][2])))
                else:
                    try:
                        mn, mx = b.get_aabb()
                        c = (float((mn[0] + mx[0]) * 0.5),
                             float((mn[1] + mx[1]) * 0.5),
                             float((mn[2] + mx[2]) * 0.5))
                        hx = float((mx[0] - mn[0]) * 0.5)
                        hy = float((mx[1] - mn[1]) * 0.5)
                        hz = float((mx[2] - mn[2]) * 0.5)
                    except Exception:
                        return None
                return (c[0], c[1], c[2], hx, hy, hz)
            if isinstance(b, list) and len(b) >= 6:
                return (float(b[0]), float(b[1]), float(b[2]),
                        float(b[3]), float(b[4]), float(b[5]))
            return None

        r = 0.45
        head = 1.8
        moved = False
        for _ in range(6):
            hit = None
            for b in self.blocks:
                bd = bounds(b)
                if bd is None:
                    continue
                cx, cy, cz, hx, hy, hz = bd
                if not (ey < cy + hy and ey + head > cy - hy):
                    continue
                if (ex + r > cx - hx and ex - r < cx + hx and
                        ez + r > cz - hz and ez - r < cz + hz):
                    hit = bd
                    break
            if hit is None:
                break
            cx, cy, cz, hx, hy, hz = hit
            opts = [
                ((cx - hx) - r - 0.03 - ex, "x", (cx - hx) - r - 0.03),
                ((cx + hx) + r + 0.03 - ex, "x", (cx + hx) + r + 0.03),
                ((cz - hz) - r - 0.03 - ez, "z", (cz - hz) - r - 0.03),
                ((cz + hz) + r + 0.03 - ez, "z", (cz + hz) + r + 0.03),
            ]
            opts.sort(key=lambda o: abs(o[0]))
            best = opts[0]
            if best[1] == "x":
                ex = float(best[2])
            else:
                ez = float(best[2])
            moved = True

        if moved:
            e[1] = ex
            e[3] = ez
            self.mark_dirty()
            self.log_message(
                f"Moved {e[0]} out of a solid to "
                f"({ex:.1f}, {ez:.1f})")
        return moved

    def slot_array(self):
        if not self.selection:
            self.set_status("Select something to array")
            return
        dlg = ArrayDialog(self, self.grid_size, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        count, ox, oy, oz = dlg.values()
        self.push_undo("Array")
        base_blocks = [copy.deepcopy(self.blocks[i])
                       for i in self.selected_block_indices]
        base_ents = [copy.deepcopy(self.entities[i])
                     for i in self.selected_entity_indices]
        new_sel = []
        for n in range(1, count):
            for b in base_blocks:
                nb = copy.deepcopy(b)
                if Brush and isinstance(nb, Brush):
                    if hasattr(nb, "user_data"):
                        m = nb.user_data.get("model", glm.mat4())
                        m[3] = glm.vec4(m[3][0] + ox * n, m[3][1] + oy * n,
                                        m[3][2] + oz * n, 1.0)
                        nb.user_data["model"] = m
                else:
                    nb[0] += ox * n; nb[1] += oy * n; nb[2] += oz * n
                self.blocks.append(nb)
                new_sel.append(("block", len(self.blocks) - 1))
            for e in base_ents:
                ne = copy.deepcopy(e)
                ne[1] += ox * n; ne[2] += oy * n; ne[3] += oz * n
                self.entities.append(ne)
                new_sel.append(("entity", len(self.entities) - 1))
        self.selection = new_sel
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(f"Array: {count} copies at ({ox}, {oy}, {oz})")

    def align_selection(self, axis, edge):
        idxs = self.selected_block_indices
        if len(idxs) < 2:
            self.set_status("Select at least two solids to align")
            return
        ai = {"x": 0, "y": 1, "z": 2}[axis]
        extents = []
        for i in idxs:
            b = self.blocks[i]
            c, h = float(b[ai]), float(b[3 + ai])
            extents.append((c - h, c + h))
        lo = min(e[0] for e in extents)
        hi = max(e[1] for e in extents)
        if edge == "min":
            target = lo
        elif edge == "max":
            target = hi
        else:
            target = (lo + hi) * 0.5
        self.push_undo("Align")
        for i, (elo, ehi) in zip(idxs, extents):
            b = self.blocks[i]
            h = float(b[3 + ai])
            if edge == "min":
                b[ai] = float(target + h)
            elif edge == "max":
                b[ai] = float(target - h)
            else:
                b[ai] = float(target)
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(f"Align {axis}/{edge}: {len(idxs)} solids")

    def snap_selection_to_grid(self):
        g = max(1, int(self.grid_size))
        idxs = self.selected_block_indices
        ents = self.selected_entity_indices
        if not idxs and not ents:
            self.set_status("Nothing selected")
            return
        self.push_undo("Snap to grid")
        for i in idxs:
            b = self.blocks[i]
            if Brush and isinstance(b, Brush):
                continue
            for k in (0, 1, 2):
                b[k] = round(float(b[k]) / g) * g
            for k in (3, 4, 5):
                b[k] = max(g / 2.0, round(float(b[k]) / g) * g)
        for i in ents:
            e = self.entities[i]
            for k in (1, 2, 3):
                e[k] = round(float(e[k]) / g) * g
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(f"Snapped {len(idxs) + len(ents)} object(s) to grid {g}")

    def slot_load_prefab(self):
        prefab_dir = os.path.join(PROJECT_ROOT, "prefabs")
        start = prefab_dir if os.path.isdir(prefab_dir) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Prefab", start,
            "Hammer Prefab (*.hprefab);;All files (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Load Prefab", str(e))
            return
        self.push_undo("Load prefab")
        new_sel = []
        for b in data.get("blocks", []):
            if Brush and isinstance(b, dict) and b.get("__type__") == "Brush":
                brush = Brush.from_dict(b["data"])
                brush.user_data = self._deserialize_user_data(b.get("user_data", {}))
                self.blocks.append(brush)
            else:
                self.blocks.append(list(b))
            new_sel.append(("block", len(self.blocks) - 1))
        for e in data.get("entities", []):
            self.entities.append(list(e))
            new_sel.append(("entity", len(self.entities) - 1))
        self.selection = new_sel
        self.mark_dirty()
        self.refresh_ui_lists(); self.refresh_all_views()
        self.log_message(f"Loaded prefab: {path}")

    def _set_render_mode(self, mode):
        self.render_mode = mode
        try:
            self.view_persp.wireframe = (mode == "Wireframe")
            self.view_persp.update()
        except Exception:
            pass
        self._update_window_title()
        self.log_message(f"3D view mode: {mode}")

    def _reset_layout(self):
        self.fit_view()
        self.log_message("Layout reset")

    def _show_about(self):
        QMessageBox.about(
            self, "About Hammer",
            "Hammer - 32 Bit\n"
            "Brush / entity map editor for Anima Engine.\n\n"
            "Compiles to a playable .amap map.")

    def _on_texture_changed(self, name):
        self.current_texture = name
        if hasattr(self, "tex_preview"):
            self._update_texture_preview(name)
        save_settings({"texture": name})

    def _on_water_toggle(self, state):
        self.current_is_water = bool(state)
        self.set_status(
            "水方块模式：新画的方块会变成水" if state else "普通方块模式")

    def _on_sky_changed(self, name):
        self.sky_name = name
        save_settings({"sky": self.sky_name})
        for v in self._views():
            v.reload_sky()

    def _update_texture_preview(self, tex_name):
        try:
            self.tex_size.setText("")
        except Exception:
            pass
        if not tex_name:
            self.tex_preview.clear(); self.tex_preview.setText("No Texture"); return
        path = material_full_path(tex_name)
        if os.path.exists(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.tex_preview.setPixmap(
                    pix.scaled(250, 92, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation))
                try:
                    self.tex_size.setText(f"{pix.width()}x{pix.height()}")
                except Exception:
                    pass
                return
        self.tex_preview.clear(); self.tex_preview.setText("No Texture")

    def _browse_texture(self):
        dlg = TextureBrowserDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            selected = dlg.get_selected_texture()
            if selected:
                self.current_texture = selected; self._update_texture_preview(selected)
                if self.tex_combo.findText(selected) == -1: self.tex_combo.addItem(selected)
                self.tex_combo.setCurrentText(selected)
                save_settings({"texture": selected})
                self.set_status(self.texts["selected_tex"].format(selected))

    def _replace_texture(self):
        """Apply the current texture to every selected solid."""
        blocks = self.selected_block_indices
        if not blocks:
            self.set_status("Select one or more solids first")
            return
        self.push_undo("Apply texture")
        for i in blocks:
            if not (0 <= i < len(self.blocks)):
                continue
            b = self.blocks[i]
            if Brush and isinstance(b, Brush):
                if hasattr(b, "user_data"):
                    b.user_data["texture"] = self.current_texture
            else:
                while len(b) < 7:
                    b.append(None)
                b[6] = self.current_texture
        self.mark_dirty()
        self.refresh_ui_lists()
        self.refresh_all_views()
        self.set_status(f"Applied '{self.current_texture}' to {len(blocks)} solid(s)")

    def _set_spawn(self):
        block_row = self.selected_block_idx
        ent_row = self.selected_entity_idx
        if block_row < 0:
            block_row = self.block_list.currentRow()
        if ent_row < 0:
            ent_row = self.ent_list.currentRow()
        self.push_undo("Set spawn")
        if 0 <= block_row < len(self.blocks):
            b = self.blocks[block_row]
            if Brush and isinstance(b, Brush):
                if hasattr(b, 'user_data') and 'model' in b.user_data:
                    model = b.user_data['model']
                    pos = glm.vec3(model[3][0], model[3][1], model[3][2])
                    self.spawn = (float(pos.x), float(pos.z))
                else:
                    min_v, max_v = b.get_aabb()
                    pos = (min_v + max_v) * 0.5
                    self.spawn = (float(pos[0]), float(pos[2]))
            else:
                self.spawn = (float(b[0]), float(b[2]))
        elif 0 <= ent_row < len(self.entities):
            e = self.entities[ent_row]; self.spawn = (float(e[1]), float(e[3]))
        else:
            x, ok1 = QInputDialog.getDouble(self, "Spawn X", "X:", float(self.spawn[0]), -9999, 9999, 2)
            if not ok1: return
            z, ok2 = QInputDialog.getDouble(self, "Spawn Z", "Z:", float(self.spawn[1]), -9999, 9999, 2)
            if not ok2: return
            self.spawn = (float(x), float(z))
        self.spawn_label.setText(f"{self.spawn[0]:.1f}, {self.spawn[1]:.1f}")
        self.mark_dirty(); self.refresh_all_views()
        self.set_status(f"Spawn set to {self.spawn[0]:.1f}, {self.spawn[1]:.1f}")

    def _delete_block(self):
        self._delete_block_at(self.block_list.currentRow())

    def _delete_block_at(self, row):
        if 0 <= row < len(self.blocks):
            self.push_undo("Delete solid")
            del self.blocks[row]
            self.selection = [(k, i if i < row else i - 1)
                              for k, i in self.selection
                              if not (k == "block" and i == row)]
            self.mark_dirty()
            self.refresh_ui_lists(); self.refresh_all_views(); self._update_status()

    def _delete_entity(self):
        self._delete_entity_at(self.ent_list.currentRow())

    def _delete_entity_at(self, row):
        if 0 <= row < len(self.entities):
            self.push_undo("Delete entity")
            del self.entities[row]
            self.selection = [(k, i if i < row else i - 1)
                              for k, i in self.selection
                              if not (k == "entity" and i == row)]
            self.mark_dirty()
            self.refresh_ui_lists(); self.refresh_all_views(); self._update_status()

    def refresh_ui_lists(self):
        # Drop selection entries that no longer exist.
        self.selection = [
            (k, i) for (k, i) in self.selection
            if (k == "block" and 0 <= i < len(self.blocks))
            or (k == "entity" and 0 <= i < len(self.entities))]
        sel_b = set(self.selected_block_indices)
        sel_e = set(self.selected_entity_indices)
        self.block_list.clear()
        for i, b in enumerate(self.blocks):
            if Brush and isinstance(b, Brush):
                # Brush 对象
                is_water = b.user_data.get("is_water", False) if hasattr(b, 'user_data') else False
                tex = b.user_data.get("texture", "dev.png") if hasattr(b, 'user_data') else "dev.png"
                # 从 model 矩阵或 AABB 获取位置和大小
                if hasattr(b, 'user_data') and 'model' in b.user_data:
                    model = b.user_data['model']
                    pos = glm.vec3(model[3][0], model[3][1], model[3][2])
                    sx = glm.length(glm.vec3(model[0][0], model[1][0], model[2][0]))
                    sy = glm.length(glm.vec3(model[0][1], model[1][1], model[2][1]))
                    sz = glm.length(glm.vec3(model[0][2], model[1][2], model[2][2]))
                else:
                    min_v, max_v = b.get_aabb()
                    pos = (min_v + max_v) * 0.5
                    sx, sy, sz = (max_v - min_v) * 0.5
                tag = "💧 " if is_water else ""
                text = (f"{tag}#{i} [Brush] c=({pos.x:.1f},{pos.y:.1f},{pos.z:.1f}) "
                        f"h=({sx:.1f},{sy:.1f},{sz:.1f}) {tex}")
            else:
                # 旧格式
                cut_info = f" [cuts:{len(b[7])}]" if len(b) > 7 and b[7] else ""
                is_water = (len(b) > 8 and b[8]) or is_water_texture(b[6])
                tag = "💧 " if is_water else ""
                tex = b[6] if len(b) > 6 else "?"
                if not isinstance(tex, str):
                    tex = str(tex)
                text = (f"{tag}#{i} c=({b[0]:.1f},{b[1]:.1f},{b[2]:.1f}) "
                        f"h=({b[3]:.1f},{b[4]:.1f},{b[5]:.1f}) {tex}{cut_info}")
            item = QListWidgetItem(text)
            item.setSelected(i in sel_b)
            self.block_list.addItem(item)
        self.ent_list.clear()
        for i, e in enumerate(self.entities):
            cut_info = f" [cuts:{len(e[4])}]" if len(e) > 4 and e[4] else ""
            item = QListWidgetItem(
                f"#{i} {e[0]} ({e[1]:.1f},{e[2]:.1f},{e[3]:.1f}){cut_info}")
            item.setSelected(i in sel_e)
            self.ent_list.addItem(item)
        for v in self._views():
            try:
                v.prune_brush_cache()
            except Exception:
                pass

    def refresh_all_views(self):
        for v in self._views():
            v.update()

    def slot_new_map(self):
        if not self._prompt_save_if_dirty(): return
        self.blocks.clear(); self.entities.clear(); self.terrains.clear()
        self.cinematic = {}
        self.spawn = (0.0, 0.0)
        self.clear_selection()
        self._undo.clear(); self._redo.clear(); self.clipboard = []
        self.spawn_label.setText("0.0, 0.0")
        self.current_map_path = None
        self.clear_dirty()
        self._drop_geometry_caches()
        self.refresh_ui_lists(); self.refresh_all_views(); self._update_status()

    def slot_open_map(self):
        if not self._prompt_save_if_dirty(): return
        start_dir = os.path.dirname(self.current_map_path) if self.current_map_path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open", start_dir, "Hammer Map (*.hmmap)")
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f: data = json.load(f)
            
            # 处理 Brush 反序列化 (兼容旧地图)
            raw_blocks = data.get("blocks", [])
            self.blocks = []
            for b in raw_blocks:
                if Brush and isinstance(b, dict) and b.get("__type__") == "Brush":
                    brush = Brush.from_dict(b["data"])
                    brush.user_data = self._deserialize_user_data(b.get("user_data", {}))
                    self.blocks.append(brush)
                else:
                    self.blocks.append(list(b))
            
            self.entities = [list(e) for e in data.get("entities", [])]
            self.terrains = [dict(t) for t in data.get("terrains", [])]
            self.spawn = tuple(data.get("spawn", [0.0, 0.0]))
            self.sky_name = data.get("sky", self.sky_name)
            self.cinematic = dict(data.get("cinematic") or {})
            self.current_map_path = path
            self.clear_selection()
            self._undo.clear(); self._redo.clear(); self.clipboard = []
            self.spawn_label.setText(f"{self.spawn[0]:.1f}, {self.spawn[1]:.1f}")
            
            # 兼容旧格式的 cuts 处理
            for b in self.blocks:
                if not (Brush and isinstance(b, Brush)):
                    if len(b) > 7 and b[7]:
                        b[7] = normalize_cuts(b[7])
                    else:
                        while len(b) < 8:
                            b.append(None)
                    while len(b) < 9:
                        b.append(False)
            for e in self.entities:
                if len(e) > 4 and e[4]: e[4] = normalize_cuts(e[4])
                else:
                    while len(e) < 5: e.append(None)
            
            self.sky_combo.setCurrentText(self.sky_name)
            self.clear_dirty()
            self._drop_geometry_caches()
            self.refresh_ui_lists(); self.refresh_all_views()
            self._on_sky_changed(self.sky_name)
            self._update_status()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open map:\n{e}")

    def _serialize_user_data(self, user_data):
        """将 user_data 中的 glm.mat4 转换为可 JSON 序列化的格式"""
        if not user_data:
            return {}
        return {str(k): _jsonable(v) for k, v in user_data.items()}

    def _deserialize_user_data(self, user_data):
        """将 JSON 反序列化回 glm.mat4"""
        if not user_data:
            return {}
        result = {}
        for k, v in user_data.items():
            if k == "model" and isinstance(v, list) and len(v) == 16:
                # 从列主序列表恢复 glm.mat4
                m = glm.mat4()
                for j in range(4):
                    for i in range(4):
                        m[i][j] = v[j * 4 + i]
                result[k] = m
            else:
                result[k] = v
        return result

    def _map_payload(self):
        blocks_to_save = []
        for b in self.blocks:
            if Brush and isinstance(b, Brush):
                blocks_to_save.append({
                    "__type__": "Brush",
                    "data": b.to_dict(),
                    "user_data": self._serialize_user_data(getattr(b, 'user_data', {}))
                })
            else:
                blocks_to_save.append(b)
        return _jsonable({
            "blocks": blocks_to_save,
            "entities": self.entities,
            "terrains": self.terrains,
            "spawn": list(self.spawn),
            "sky": self.sky_name,
            "cinematic": getattr(self, "cinematic", {}),
        })

    def _write_map_to(self, path):
        if not path.lower().endswith(".hmmap"):
            path += ".hmmap"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._map_payload(), f, indent=2)
        self.current_map_path = path
        self.clear_dirty()
        save_settings({"last_dir": os.path.dirname(path)})
        self.set_status(f"Saved {path}")

    def _default_map_dir(self):
        d = load_settings().get("last_dir")
        if isinstance(d, str) and os.path.isdir(d):
            return d
        return ""

    def slot_save_map(self):
        if self.current_map_path is None:
            name = "untitled.hmmap"
            path, _ = QFileDialog.getSaveFileName(
                self, "Save", os.path.join(self._default_map_dir(), name),
                "Hammer Map (*.hmmap)")
            if not path: return
            try:
                self._write_map_to(path)
            except Exception as e:
                QMessageBox.warning(self, "Save error", str(e))
            return
        try:
            self._write_map_to(self.current_map_path)
        except Exception as e:
            QMessageBox.warning(self, "Save error", str(e))

    def slot_save_as_map(self):
        cur = self.current_map_path
        name = os.path.basename(cur) if cur else "untitled.hmmap"
        start = os.path.dirname(cur) if cur else self._default_map_dir()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save As", os.path.join(start, name), "Hammer Map (*.hmmap)")
        if not path: return
        try:
            self._write_map_to(path)
        except Exception as e:
            QMessageBox.warning(self, "Save error", str(e))

    def slot_close_map(self):
        if not self._prompt_save_if_dirty(): return
        self.blocks.clear(); self.entities.clear(); self.terrains.clear()
        self.current_map_path = None
        self.clear_selection()
        self._undo.clear(); self._redo.clear(); self.clipboard = []
        self.clear_dirty()
        self._drop_geometry_caches()
        self.refresh_ui_lists(); self.refresh_all_views(); self._update_status()

    def slot_set_sky(self):
        skies = self._scan_skies()
        name, ok = QInputDialog.getItem(self, "Set Sky", "Sky config:", skies, 0, False)
        if ok and name:
            self.sky_name = name
            self.sky_combo.setCurrentText(name)
            save_settings({"sky": self.sky_name})
            self.mark_dirty()
            self._on_sky_changed(name)

    def slot_compile_map(self):
        try:
            self._do_compile()
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            print(err)
            self.log_message(f"Compile failed: {e}")
            QMessageBox.critical(self, "Compile Error", err)

    def _do_compile(self, quiet=False):
        if not self.blocks and not self.entities:
            if not quiet:
                QMessageBox.information(self, self.texts["msg_compile_title"],
                                        self.texts["msg_empty_map"])
            return
        if self.current_map_path is None or self.dirty:
            if quiet:
                return
            reply = QMessageBox.question(
                self, "Compile",
                "The map must be saved before compiling. Save it now?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            self.slot_save_map()
            if self.current_map_path is None:
                return

        map_stem = os.path.splitext(os.path.basename(self.current_map_path))[0]
        out_dir = get_map_dir()
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{map_stem}.amap")

        map_data = {
            "version": 2,
            "meta": {
                "name": map_stem,
                "source": os.path.basename(self.current_map_path),
                "blocks": len(self.blocks),
                "entities": len(self.entities),
            },
            "sky": {"config": self.sky_name},
            "spawn": [float(self.spawn[0]), float(self.spawn[1])],
            "geometry": [],
            "entities": [],
            "addons": [],
        }
        # Opening cinematic (camera path + title + subtitles), played by the
        # engine right after the map finishes loading.
        if getattr(self, "cinematic", None):
            map_data["cinematic"] = _jsonable(self.cinematic)

        # ---- blocks ----
        for b in self.blocks:
            if Brush and isinstance(b, Brush):
                # 兼容旧版的 Brush
                is_water = b.user_data.get("is_water", False) if hasattr(b, 'user_data') else False
                tex = b.user_data.get("texture", "dev.png") if hasattr(b, 'user_data') else "dev.png"
                model = b.user_data.get("model", glm.mat4()) if hasattr(b, 'user_data') else glm.mat4()
                
                pos = glm.vec3(model[3][0], model[3][1], model[3][2])
                sx = glm.length(glm.vec3(model[0][0], model[1][0], model[2][0]))
                sy = glm.length(glm.vec3(model[0][1], model[1][1], model[2][1]))
                sz = glm.length(glm.vec3(model[0][2], model[1][2], model[2][2]))
                
                if is_water:
                    map_data["geometry"].append({
                        "type": "waterbox",
                        "pos": [float(pos.x), float(pos.y), float(pos.z)],
                        "half": [float(sx), float(sy), float(sz)],
                        "material": (b.user_data.get("water_material")
                                     if hasattr(b, "user_data") else None)
                        or getattr(self, "water_material", "sea"),
                    })
                else:
                    item = {
                        "type": "brush",
                        "pos": [float(pos.x), float(pos.y), float(pos.z)],
                        "half": [float(sx), float(sy), float(sz)],
                        "texture": tex,
                        "brush_data": b.to_dict(),
                    }
                    map_data["geometry"].append(item)
            else:
                # 旧格式
                x, y, z, sx, sy, sz, tex = b[:7]
                # Editor-only helper textures (ground-node hints, overlays) must
                # not become solid, invisible walls in the compiled map.
                if str(tex or "").lower().replace("\\", "/").startswith("editor/"):
                    continue
                cuts = b[7] if len(b) > 7 else None
                is_water = (len(b) > 8 and b[8]) or is_water_texture(tex)

                if is_water:
                    mat = (b[9] if len(b) > 9 and b[9] else None)
                    map_data["geometry"].append({
                        "type": "waterbox",
                        "pos": [float(x), float(y), float(z)],
                        "half": [float(sx), float(sy), float(sz)],
                        "material": mat or getattr(self, "water_material", "sea"),
                    })
                else:
                    item = {
                        "type": "box",
                        "pos": [float(x), float(y), float(z)],
                        "half": [float(sx), float(sy), float(sz)],
                        "texture": tex,
                    }
                    if cuts:
                        item["cuts"] = [
                            [int(c[0]), int(c[1]), int(c[2]), float(c[3])]
                            for c in cuts
                        ]
                    map_data["geometry"].append(item)

        # ---- smooth terrain ----
        for t in self.terrains:
            try:
                heights = t.get("heights") or []
                if not heights:
                    continue
                map_data["geometry"].append({
                    "type": "terrain",
                    "pos": [float(v) for v in t.get("pos", [0, 0, 0])],
                    "half": [float(v) for v in t.get("half", [10, 1, 10])],
                    "segments": int(t.get("segments", len(heights) - 1)),
                    "heights": [[round(float(v), 3) for v in row]
                                for row in heights],
                    "texture": t.get("texture") or "nature/meadow_grass.png",
                })
            except Exception as e:
                self.log_message(f"Terrain skipped: {e}")

        # ---- entities ----
        KNOWN_ENTITIES = {
            "info_player_start", "light", "trigger",
            "weapon_spawn", "item_spawn",
        }

        for e in self.entities:
            etype = e[0]
            x, y, z = e[1], e[2], e[3]
            params = e[5] if len(e) > 5 and e[5] else {}

            # 太阳光：进 sky.sun，不放 entities
            if etype == "light_environment":
                if "sun" not in map_data["sky"]:
                    map_data["sky"]["sun"] = {
                        "yaw": float(params.get("yaw", 45)),
                        "pitch": float(params.get("pitch", -35)),
                        "color": [
                            float(params.get("r", 1.0)),
                            float(params.get("g", 0.97)),
                            float(params.get("b", 0.9)),
                        ],
                        "intensity": float(params.get("intensity", 1.0)),
                    }
                continue

            # 标准实体
            if etype in KNOWN_ENTITIES:
                map_data["entities"].append({
                    "type": etype,
                    "pos": [float(x), float(y), float(z)],
                    "params": dict(params) if params else {},
                })
                continue

            # 其他一律当 addon 模型（baseball、crate 之类）
            map_data["addons"].append({
                "type": "model",
                "name": etype,
                "pos": [float(x), float(y), float(z)],
            })

        # ========================================================
        # 加密保存
        # ========================================================
        from engine_pkg.resources.map_binary import save_amap

        try:
            save_amap(_jsonable(map_data), out_path)
        except Exception as e:
            self.log_message(f"Compile failed writing {out_path}: {e}")
            if not quiet:
                QMessageBox.warning(self, "Compile Failed",
                                    f"Could not write .amap:\n{e}")
            return

        self.log_message(
            f"Compiled {os.path.basename(out_path)}: "
            f"{len(self.blocks)} solid(s), {len(self.entities)} ent(s)")
        self.set_status(f"Compiled: {out_path}")
        if not quiet:
            QMessageBox.information(
                self, self.texts["msg_compile_title"],
                f"Compile finished.\n"
                f"Solids: {len(self.blocks)}\n"
                f"Entities: {len(self.entities)}\n"
                f"Output: {out_path}")

    def closeEvent(self, event):
        if not self._prompt_save_if_dirty():
            event.ignore(); return
        for v in self._views():
            try: v._exit_mouse_look()
            except Exception: pass
        try:
            g = self.geometry()
            save_settings({
                "sky": self.sky_name,
                "grid": self.grid_size,
                "snap": self.snap_enabled,
                "show_grid": self.show_grid,
                "texture": self.current_texture,
                "lang": self.lang,
                "geometry": [g.x(), g.y(), g.width(), g.height()],
            })
        except Exception:
            pass
        event.accept()

# ============================================================
# 入口
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    settings = load_settings()

    # Hammer's start-up "Select a game configuration to use" prompt.
    game_config = settings.get("game_config")
    if game_config not in GAME_CONFIGS:
        cfg_dlg = GameConfigDialog()
        if cfg_dlg.exec_() != QDialog.Accepted:
            return 0
        game_config = cfg_dlg.get_config()
        save_settings({"game_config": game_config})

    saved_lang = settings.get("lang")
    lang = saved_lang if saved_lang in LANGUAGES else "en"
    win = HammerMainWindow(lang)
    win.game_config = game_config
    win.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())