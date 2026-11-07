"""Anima Studio - 统一编辑器 (Hammer + Unity-style Runtime)

集成了：
  - Hammer 地图编辑（四视图、实体、纹理、编译）
  - Unity 风格运行环境（Play / Stop、脚本挂载、60Hz 更新）
  - Hierarchy / Inspector / Project 面板，并支持实时联动
  - Game 视图（独立渲染，不影响地图编辑）
  - 支持选择文件夹作为 Mod 项目根目录，递归读取所有脚本
"""
import os
import sys
import copy
import math
import time
import importlib
import importlib.util
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QDockWidget,
    QTreeWidget, QTreeWidgetItem, QLabel, QLineEdit, QDoubleSpinBox,
    QSpinBox, QPushButton, QFormLayout, QGroupBox, QCheckBox, QComboBox,
    QListWidget, QListWidgetItem, QDialog, QPlainTextEdit, QScrollArea,
    QMenu, QInputDialog, QMessageBox, QToolBar, QAction, QFileDialog
)
from PyQt5.QtGui import (
    QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat, QColor
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtOpenGL import QGLWidget

# 必须先 import hammer
import hammer
from hammer import (
    HammerMainWindow, Brush, LANGUAGES, load_settings, save_settings,
    GameConfigDialog, ENTITY_TYPES
)

from OpenGL.GL import *
from OpenGL.GLU import *

# ============================================================
# anima.py 运行时 API
# ============================================================
ANIMA_API_SOURCE = '''"""Anima Runtime API - import this in your scripts."""
from typing import List, Optional

class Vector3:
    __slots__ = ("x", "y", "z")
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x); self.y = float(y); self.z = float(z)
    def __repr__(self):
        return f"Vector3({self.x:.2f}, {self.y:.2f}, {self.z:.2f})"
    def __add__(self, o):
        if isinstance(o, Vector3): return Vector3(self.x+o.x, self.y+o.y, self.z+o.z)
        return Vector3(self.x+o, self.y+o, self.z+o)
    def __sub__(self, o):
        if isinstance(o, Vector3): return Vector3(self.x-o.x, self.y-o.y, self.z-o.z)
        return Vector3(self.x-o, self.y-o, self.z-o)
    def __mul__(self, s): return Vector3(self.x*s, self.y*s, self.z*s)
    def __iter__(self): return iter((self.x, self.y, self.z))
    def copy(self): return Vector3(self.x, self.y, self.z)
    @property
    def length(self): return (self.x**2 + self.y**2 + self.z**2) ** 0.5
    def normalized(self):
        l = self.length or 1.0
        return Vector3(self.x/l, self.y/l, self.z/l)
    @staticmethod
    def zero(): return Vector3(0, 0, 0)

class Transform:
    def __init__(self):
        self.position = Vector3(0, 0, 0)
        self.rotation = Vector3(0, 0, 0)
        self.scale = Vector3(1, 1, 1)

class Script:
    def __init__(self):
        self.game_object = None
        self.enabled = True
    def on_awake(self): pass
    def on_start(self): pass
    def on_update(self): pass
    def on_destroy(self): pass
    @property
    def transform(self):
        return self.game_object.transform if self.game_object else None
    def log(self, *a):
        _log_impl(" ".join(str(x) for x in a))
    def find(self, name):
        scene = _get_scene_impl()
        return scene.find(name) if scene else None

class GameObject:
    def __init__(self, name="GameObject"):
        self.name = name
        self.transform = Transform()
        self.components = []
        self.children = []
        self.parent = None
        self.active = True
        self.tag = "Untagged"
    def add_component(self, c):
        comp = c() if isinstance(c, type) and issubclass(c, Script) else c
        comp.game_object = self
        if not hasattr(comp, "enabled"):
            comp.enabled = True
        self.components.append(comp)
        return comp
    def get_component(self, cls):
        for c in self.components:
            if isinstance(c, cls): return c
        return None

class Time:
    delta_time = 0.0
    time = 0.0
    frame_count = 0

class Scene:
    def __init__(self, name="Untitled"):
        self.name = name
        self.objects = []
    def add_object(self, name="GameObject", parent=None):
        obj = GameObject(name)
        if parent:
            obj.parent = parent
            parent.children.append(obj)
        else:
            self.objects.append(obj)
        return obj
    def remove_object(self, obj):
        if obj in self.objects: self.objects.remove(obj)
        elif obj.parent and obj in obj.parent.children:
            obj.parent.children.remove(obj)
    def find(self, name):
        for o in self.all_objects():
            if o.name == name: return o
        return None
    def find_by_tag(self, tag):
        return [o for o in self.all_objects() if o.tag == tag]
    def all_objects(self):
        out = []
        def walk(objs):
            for o in objs:
                out.append(o); walk(o.children)
        walk(self.objects)
        return out

_scene = None
_log_impl = lambda msg: print("[anima]", msg)
_get_scene_impl = lambda: _scene
def _set_scene(s):
    global _scene; _scene = s
def _set_log(fn):
    global _log_impl; _log_impl = fn
def _set_scene_getter(fn):
    global _get_scene_impl; _get_scene_impl = fn
'''

def ensure_example_scripts(scripts_dir):
    """确保示例脚本存在"""
    scripts_dir = Path(scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    ex = scripts_dir / "rotator.py"
    if not ex.exists():
        ex.write_text(
            '"""Example: rotate the GameObject around Y."""\n'
            "from anima import Script, Time\n\n"
            "class Rotator(Script):\n"
            "    speed = 90.0\n\n"
            "    def on_update(self):\n"
            "        self.transform.rotation.y += self.speed * Time.delta_time\n",
            encoding="utf-8")

def ensure_anima_module(scripts_dir):
    """加载或重新加载指定目录下的 anima 模块"""
    scripts_dir = Path(scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    api = scripts_dir / "anima.py"
    if not api.exists():
        api.write_text(ANIMA_API_SOURCE, encoding="utf-8")
    
    if str(scripts_dir) in sys.path:
        sys.path.remove(str(scripts_dir))
    sys.path.insert(0, str(scripts_dir))
    
    try:
        if "anima" in sys.modules:
            del sys.modules["anima"]
        import anima
        return sys.modules["anima"]
    except Exception as e:
        print(f"[anima] load failed: {e}")
        return None

# ============================================================
# 场景构建器
# ============================================================
def build_scene_from_map(anima, blocks, entities, spawn):
    if anima is None: return None
    scene = anima.Scene("PlayScene")
    for i, b in enumerate(blocks):
        try:
            if Brush and isinstance(b, Brush): continue
            x, y, z, sx, sy, sz = (float(v) for v in b[:6])
        except Exception: continue
        obj = scene.add_object(f"Solid_{i}")
        obj.tag = "Solid"
        obj.transform.position = anima.Vector3(x, y, z)
        obj.transform.scale = anima.Vector3(sx * 2.0, sy * 2.0, sz * 2.0)

    for i, e in enumerate(entities):
        try:
            etype = e[0]
            x, y, z = float(e[1]), float(e[2]), float(e[3])
        except Exception: continue
        obj = scene.add_object(f"{etype}_{i}")
        obj.tag = etype
        obj.transform.position = anima.Vector3(x, y, z)
        if etype == "info_player_start":
            obj.tag = "PlayerStart"
            obj.transform.scale = anima.Vector3(0.4, 1.8, 0.4)
        elif etype == "light_environment":
            obj.transform.scale = anima.Vector3(0.4, 0.4, 0.4)
        else:
            obj.transform.scale = anima.Vector3(1.0, 1.0, 1.0)

    cam = scene.add_object("GameCamera")
    cam.tag = "Camera"
    sx, sz = float(spawn[0]), float(spawn[1])
    cam.transform.position = anima.Vector3(sx, 3.0, sz + 6.0)
    return scene


# ============================================================
# Game 视图
# ============================================================
class GameGLWidget(QGLWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.cam_pos = [0.0, 3.0, 8.0]
        self.cam_yaw = 0.0
        self.cam_pitch = -0.15
        self._keys = set()
        self._last_mouse = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(33)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def initializeGL(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glLightfv(GL_LIGHT0, GL_POSITION, [3.0, 6.0, 4.0, 0.0])
        glLightfv(GL_LIGHT0, GL_AMBIENT, [0.35, 0.36, 0.42, 1.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.95, 0.94, 0.88, 1.0])
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glEnable(GL_NORMALIZE)
        glShadeModel(GL_SMOOTH)

    def resizeGL(self, w, h):
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION); glLoadIdentity()
        gluPerspective(70.0, w / max(1, h), 0.05, 1000.0)
        glMatrixMode(GL_MODELVIEW)

    def set_play_camera(self, spawn):
        self.cam_pos = [float(spawn[0]), 3.0, float(spawn[1]) + 6.0]
        self.cam_yaw = 0.0
        self.cam_pitch = -0.15

    def paintGL(self):
        glClearColor(0.13, 0.15, 0.20, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()

        cy = math.radians(self.cam_yaw)
        cp = math.radians(self.cam_pitch)
        look = (math.sin(cy) * math.cos(cp), math.sin(cp), math.cos(cy) * math.cos(cp))
        gluLookAt(self.cam_pos[0], self.cam_pos[1], self.cam_pos[2],
                  self.cam_pos[0] + look[0], self.cam_pos[1] + look[1], self.cam_pos[2] + look[2],
                  0, 1, 0)

        glDisable(GL_LIGHTING)
        glColor3f(0.28, 0.30, 0.34)
        glBegin(GL_LINES)
        for i in range(-40, 41, 2):
            glVertex3f(i, 0, -40); glVertex3f(i, 0, 40)
            glVertex3f(-40, 0, i); glVertex3f(40, 0, i)
        glEnd()
        glEnable(GL_LIGHTING)

        scene = getattr(self.editor, "anima_scene", None)
        if scene is None: return
        for obj in scene.all_objects():
            if not obj.active: continue
            self._draw_obj(obj)

    def _draw_obj(self, obj):
        p = obj.transform.position
        r = obj.transform.rotation
        s = obj.transform.scale
        glPushMatrix()
        glTranslatef(p.x, p.y, p.z)
        glRotatef(r.y, 0, 1, 0)
        glRotatef(r.x, 1, 0, 0)
        glRotatef(r.z, 0, 0, 1)
        glScalef(max(s.x, 0.001), max(s.y, 0.001), max(s.z, 0.001))
        if obj is getattr(self.editor, "selected_object", None):
            glColor3f(1.0, 0.82, 0.20)
        elif obj.tag == "Camera":
            glColor3f(0.30, 0.75, 0.35)
        elif obj.tag == "PlayerStart":
            glColor3f(0.20, 0.90, 0.30)
        elif obj.tag == "Solid":
            glColor3f(0.62, 0.62, 0.70)
        else:
            glColor3f(0.75, 0.75, 0.40)
        self._cube()
        glPopMatrix()

    def _cube(self):
        glBegin(GL_QUADS)
        glNormal3f(0, 0, 1); glVertex3f(-1, -1, 1); glVertex3f(1, -1, 1); glVertex3f(1, 1, 1); glVertex3f(-1, 1, 1)
        glNormal3f(0, 0, -1); glVertex3f(1, -1, -1); glVertex3f(-1, -1, -1); glVertex3f(-1, 1, -1); glVertex3f(1, 1, -1)
        glNormal3f(0, 1, 0); glVertex3f(-1, 1, 1); glVertex3f(1, 1, 1); glVertex3f(1, 1, -1); glVertex3f(-1, 1, -1)
        glNormal3f(0, -1, 0); glVertex3f(-1, -1, -1); glVertex3f(1, -1, -1); glVertex3f(1, -1, 1); glVertex3f(-1, -1, 1)
        glNormal3f(1, 0, 0); glVertex3f(1, -1, 1); glVertex3f(1, -1, -1); glVertex3f(1, 1, -1); glVertex3f(1, 1, 1)
        glNormal3f(-1, 0, 0); glVertex3f(-1, -1, -1); glVertex3f(-1, -1, 1); glVertex3f(-1, 1, 1); glVertex3f(-1, 1, -1)
        glEnd()

    def mousePressEvent(self, e):
        self._last_mouse = e.pos(); self.setFocus()
    def mouseMoveEvent(self, e):
        if self._last_mouse is None: return
        dx = e.x() - self._last_mouse.x()
        dy = e.y() - self._last_mouse.y()
        self._last_mouse = e.pos()
        if e.buttons() & Qt.RightButton:
            self.cam_yaw -= dx * 0.4
            self.cam_pitch = max(-89.0, min(89.0, self.cam_pitch - dy * 0.4))
    def mouseReleaseEvent(self, e):
        self._last_mouse = None
    def keyPressEvent(self, e):
        self._keys.add(e.key()); self._fly()
    def keyReleaseEvent(self, e):
        self._keys.discard(e.key()); self._fly()
    def _fly(self):
        step = 0.35
        cy = math.radians(self.cam_yaw); cp = math.radians(self.cam_pitch)
        fwd = (math.sin(cy) * math.cos(cp), math.sin(cp), math.cos(cy) * math.cos(cp))
        right = (math.cos(cy), 0.0, -math.sin(cy))
        dx = dy = dz = 0.0
        if Qt.Key_W in self._keys: dx += fwd[0]; dy += fwd[1]; dz += fwd[2]
        if Qt.Key_S in self._keys: dx -= fwd[0]; dy -= fwd[1]; dz -= fwd[2]
        if Qt.Key_D in self._keys: dx += right[0]; dz += right[2]
        if Qt.Key_A in self._keys: dx -= right[0]; dz -= right[2]
        if Qt.Key_Space in self._keys: dy += 1.0
        if Qt.Key_Shift in self._keys: dy -= 1.0
        self.cam_pos[0] += dx * step; self.cam_pos[1] += dy * step; self.cam_pos[2] += dz * step
        self.update()


# ============================================================
# Hierarchy 面板
# ============================================================
class HierarchyPanel(QWidget):
    object_selected = pyqtSignal(object)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self._play_mode = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self.tree, 1)
        self.refresh()

    def set_play_mode(self, on):
        self._play_mode = bool(on)
        self.refresh()

    def refresh(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        if self._play_mode:
            scene = getattr(self.editor, "anima_scene", None)
            if scene is not None:
                for o in scene.objects:
                    self._add_gameobj(None, o)
        else:
            root = QTreeWidgetItem(self.tree, ["Map"])
            solids = QTreeWidgetItem(root, ["Solids"])
            for i, b in enumerate(self.editor.blocks):
                if Brush and isinstance(b, Brush): continue
                item = QTreeWidgetItem(solids, [f"Solid_{i}"])
                item.setData(0, Qt.UserRole, ("block", i))
                if ("block", i) in self.editor.selection:
                    item.setSelected(True)
            
            ents = QTreeWidgetItem(root, ["Entities"])
            for i, e in enumerate(self.editor.entities):
                item = QTreeWidgetItem(ents, [f"{e[0]}_{i}"])
                item.setData(0, Qt.UserRole, ("entity", i))
                if ("entity", i) in self.editor.selection:
                    item.setSelected(True)
                    
            root.setExpanded(True)
            solids.setExpanded(True)
            ents.setExpanded(True)
        self.tree.blockSignals(False)

    def _add_gameobj(self, parent, obj):
        item = QTreeWidgetItem(parent if parent else self.tree, [obj.name])
        item.setData(0, Qt.UserRole, ("gameobject", obj))
        for c in obj.children:
            self._add_gameobj(item, c)

    def _on_select(self):
        items = self.tree.selectedItems()
        if not items:
            self.editor.clear_selection()
            self.editor.refresh_all_views()
            self.object_selected.emit(None)
            return
        data = items[0].data(0, Qt.UserRole)
        if data and data[0] == "gameobject":
            self.object_selected.emit(data[1])
        elif data:
            kind, idx = data
            self.editor.select_only(kind, idx)
            self.editor.refresh_all_views()
            self.object_selected.emit(data)


# ============================================================
# Inspector 面板
# ============================================================
class InspectorPanel(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self._play_mode = False
        self._current = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.inner = QWidget()
        self.inner_layout = QVBoxLayout(self.inner)
        self.inner_layout.setContentsMargins(4, 4, 4, 4)
        self.inner_layout.setSpacing(4)
        self.inner_layout.addStretch()
        self.scroll.setWidget(self.inner)
        layout.addWidget(self.scroll, 1)
        self.refresh()

    def set_play_mode(self, on):
        self._play_mode = bool(on)
        self._current = None
        self.refresh()

    def set_object(self, obj):
        self._current = obj
        self.refresh()

    def refresh(self):
        while self.inner_layout.count():
            it = self.inner_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if self._play_mode:
            self._build_play(self._current)
        else:
            self._build_edit(self._current)
        self.inner_layout.addStretch()

    def _build_edit(self, sel):
        if sel is None:
            b = self.editor.selected_block_indices
            e = self.editor.selected_entity_indices
            if b: sel = ("block", b[0])
            elif e: sel = ("entity", e[0])
        if sel is None:
            self.inner_layout.addWidget(QLabel("(no selection)"))
            return
        kind, idx = sel
        if kind == "block" and 0 <= idx < len(self.editor.blocks):
            b = self.editor.blocks[idx]
            if Brush and isinstance(b, Brush):
                self.inner_layout.addWidget(QLabel(f"Solid #{idx} [Brush]"))
                return
            box = QGroupBox(f"Solid #{idx}")
            form = QFormLayout(box)
            for k, lab in enumerate(("X", "Y", "Z")):
                sp = QDoubleSpinBox(); sp.setRange(-1e6, 1e6)
                sp.setDecimals(3); sp.setValue(float(b[k]))
                sp.valueChanged.connect(lambda v, b=b, k=k: self._set_block_field(b, k, v))
                form.addRow(lab, sp)
            for k, lab in enumerate(("HalfX", "HalfY", "HalfZ")):
                sp = QDoubleSpinBox(); sp.setRange(0.01, 1e6)
                sp.setDecimals(3); sp.setValue(float(b[3 + k]))
                sp.valueChanged.connect(lambda v, b=b, k=k: self._set_block_field(b, 3 + k, v))
                form.addRow(lab, sp)
            tex = b[6] if len(b) > 6 else "dev.png"
            form.addRow("Texture", QLabel(str(tex)))
            self.inner_layout.addWidget(box)
        elif kind == "entity" and 0 <= idx < len(self.editor.entities):
            e = self.editor.entities[idx]
            box = QGroupBox(f"Entity #{idx}: {e[0]}")
            form = QFormLayout(box)
            for k, lab in enumerate(("X", "Y", "Z")):
                sp = QDoubleSpinBox(); sp.setRange(-1e6, 1e6)
                sp.setDecimals(3); sp.setValue(float(e[1 + k]))
                sp.valueChanged.connect(lambda v, e=e, k=k: self._set_entity_field(e, 1 + k, v))
                form.addRow(lab, sp)
            params = e[5] if len(e) > 5 and isinstance(e[5], dict) else {}
            for k, v in params.items():
                form.addRow(str(k), QLabel(str(v)))
            self.inner_layout.addWidget(box)

    def _set_block_field(self, b, k, v):
        b[k] = float(v)
        self.editor.mark_dirty()
        self.editor.refresh_all_views()

    def _set_entity_field(self, e, k, v):
        e[k] = float(v)
        self.editor.mark_dirty()
        self.editor.refresh_all_views()

    def _build_play(self, obj):
        if obj is None or not hasattr(obj, "transform"):
            self.inner_layout.addWidget(QLabel("(no GameObject selected)"))
            return
        head = QGroupBox(f"GameObject: {obj.name}")
        form = QFormLayout(head)
        name_edit = QLineEdit(obj.name)
        name_edit.textChanged.connect(lambda s, o=obj: setattr(o, "name", s))
        form.addRow("Name", name_edit)
        active = QCheckBox(); active.setChecked(obj.active)
        active.toggled.connect(lambda v, o=obj: setattr(o, "active", v))
        form.addRow("Active", active)
        tag_edit = QLineEdit(obj.tag)
        tag_edit.textChanged.connect(lambda s, o=obj: setattr(o, "tag", s))
        form.addRow("Tag", tag_edit)
        self.inner_layout.addWidget(head)

        tbox = QGroupBox("Transform")
        tform = QFormLayout(tbox)
        for attr, label in (("position", "Position"), ("rotation", "Rotation"), ("scale", "Scale")):
            vec = getattr(obj.transform, attr)
            row = QHBoxLayout(); row.setSpacing(2)
            for ax in ("x", "y", "z"):
                sp = QDoubleSpinBox(); sp.setRange(-1e6, 1e6)
                sp.setDecimals(3); sp.setValue(getattr(vec, ax))
                sp.setPrefix(f"{ax} ")
                sp.valueChanged.connect(lambda v, vec=vec, ax=ax: setattr(vec, ax, float(v)))
                row.addWidget(sp)
            w = QWidget(); w.setLayout(row)
            tform.addRow(label, w)
        self.inner_layout.addWidget(tbox)

        for comp in obj.components:
            self.inner_layout.addWidget(self._comp_box(comp))

        add_btn = QPushButton("+ Add Script")
        add_btn.clicked.connect(self._add_comp_menu)
        self.inner_layout.addWidget(add_btn)

    def _comp_box(self, comp):
        box = QGroupBox(type(comp).__name__)
        box.setCheckable(True); box.setChecked(getattr(comp, "enabled", True))
        box.toggled.connect(lambda v, c=comp: setattr(c, "enabled", v))
        form = QFormLayout(box)
        for name, val in self._fields(comp):
            w = self._editor_for(comp, name, val)
            if w is not None: form.addRow(name, w)
        rm = QPushButton("Remove")
        rm.clicked.connect(lambda: self._remove_comp(comp))
        form.addRow(rm)
        return box

    def _fields(self, comp):
        out = []; seen = set()
        for cls in type(comp).__mro__:
            if cls is object: continue
            for name, value in cls.__dict__.items():
                if name.startswith("_") or name in seen: continue
                if name in ("enabled", "game_object"): continue
                if callable(value) or isinstance(value, (property, staticmethod, classmethod)): continue
                if isinstance(value, (bool, int, float, str)):
                    seen.add(name)
                    out.append((name, getattr(comp, name, value)))
        return out

    def _editor_for(self, comp, name, value):
        if isinstance(value, bool):
            w = QCheckBox(); w.setChecked(value)
            w.toggled.connect(lambda v: setattr(comp, name, bool(v)))
            return w
        if isinstance(value, int):
            w = QSpinBox(); w.setRange(-10**9, 10**9); w.setValue(value)
            w.valueChanged.connect(lambda v: setattr(comp, name, int(v)))
            return w
        if isinstance(value, float):
            w = QDoubleSpinBox(); w.setRange(-1e9, 1e9)
            w.setDecimals(3); w.setValue(value)
            w.valueChanged.connect(lambda v: setattr(comp, name, float(v)))
            return w
        if isinstance(value, str):
            w = QLineEdit(value)
            w.textChanged.connect(lambda s: setattr(comp, name, s))
            return w
        return None

    def _add_comp_menu(self):
        if self._current is None or self.editor.anima is None: return
        menu = QMenu(self)
        classes = self.editor.get_script_classes()
        if not classes: menu.addAction("(no scripts in scripts/)").setEnabled(False)
        for name, cls in classes:
            menu.addAction(name, lambda c=cls: self._attach(c))
        menu.exec_(self.mapToGlobal(self.rect().center()))

    def _attach(self, cls):
        if self._current is None: return
        try:
            self._current.add_component(cls)
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Attach Error", str(e))

    def _remove_comp(self, comp):
        if self._current and comp in self._current.components:
            self._current.components.remove(comp)
            self.refresh()


# ============================================================
# Project 面板 (支持 Mod 项目文件夹)
# ============================================================
class ProjectPanel(QWidget):
    open_script = pyqtSignal(str)
    request_open_project = pyqtSignal(str)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.current_project = Path.cwd()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        
        self.path_label = QLabel("Project: (none)")
        self.path_label.setStyleSheet("color: #8ab4f8; padding: 4px;")
        layout.addWidget(self.path_label)
        
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_dbl_click)
        layout.addWidget(self.tree, 1)
        
        row = QHBoxLayout()
        btn_open = QPushButton("打开项目 (Open Project)")
        btn_open.clicked.connect(self._open_project)
        btn_new_script = QPushButton("+ 新建脚本 (New Script)")
        btn_new_script.clicked.connect(self._new_script)
        btn_refresh = QPushButton("刷新 (Refresh)")
        btn_refresh.clicked.connect(self.refresh)
        row.addWidget(btn_open)
        row.addWidget(btn_new_script)
        row.addWidget(btn_refresh)
        layout.addLayout(row)

    def _open_project(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择 Mod 项目文件夹", str(self.current_project))
        if folder:
            self.request_open_project.emit(folder)

    def set_ui_project(self, folder_path):
        self.current_project = Path(folder_path)
        self.path_label.setText(f"Project: {self.current_project.name} ({self.current_project})")
        self.refresh()

    def refresh(self):
        self.tree.clear()
        if not self.current_project or not self.current_project.exists():
            return
            
        def add_items(parent, path):
            for child in sorted(path.iterdir()):
                if child.name.startswith(".") or child.name == "__pycache__":
                    continue
                if child.is_dir():
                    item = QTreeWidgetItem(parent, [child.name])
                    item.setData(0, Qt.UserRole, str(child))
                    add_items(item, child)
                elif child.suffix == ".py":
                    item = QTreeWidgetItem(parent, [child.name])
                    item.setData(0, Qt.UserRole, str(child))
        
        add_items(self.tree, self.current_project)
        self.tree.expandAll()

    def _on_dbl_click(self, item, _col):
        p = item.data(0, Qt.UserRole)
        if p and Path(p).suffix == ".py":
            self.open_script.emit(p)

    def _new_script(self):
        name, ok = QInputDialog.getText(self, "New Script", "Name:")
        if not ok or not name.strip(): return
        if not name.endswith(".py"): name += ".py"
        
        scripts_dir = self.current_project / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        path = scripts_dir / name
        
        if path.exists():
            QMessageBox.warning(self, "Exists", "File already exists.")
            return
        cls = "".join(w.capitalize() for w in name[:-3].split("_")) or "NewScript"
        path.write_text(
            f'"""New script."""\n'
            f"from anima import Script, Time\n\n\n"
            f"class {cls}(Script):\n"
            f"    pass\n",
            encoding="utf-8")
        self.refresh()
        self.editor.reload_scripts()
        self.open_script.emit(str(path))


# ============================================================
# Python 语法高亮 + 脚本编辑器
# ============================================================
class PythonHighlighter(QSyntaxHighlighter):
    KEYWORDS = (
        "and as assert async await break class continue def del elif else "
        "except False finally for from global if import in is lambda None "
        "nonlocal not or pass raise return True try while with yield self"
    ).split()

    def __init__(self, doc):
        super().__init__(doc)
        self.rules = []
        kw = QTextCharFormat(); kw.setForeground(QColor("#c586c0"))
        kw.setFontWeight(QFont.Bold)
        for w in self.KEYWORDS:
            self.rules.append((self._word(w), kw))
        num = QTextCharFormat(); num.setForeground(QColor("#b5cea8"))
        self.rules.append(("num", num))
        st = QTextCharFormat(); st.setForeground(QColor("#ce9178"))
        self.rules.append(("str", st))
        com = QTextCharFormat(); com.setForeground(QColor("#6a9955"))
        self.rules.append(("com", com))
        fn = QTextCharFormat(); fn.setForeground(QColor("#dcdcaa"))
        self.rules.append(("def", fn))
        cl = QTextCharFormat(); cl.setForeground(QColor("#4ec9b0"))
        self.rules.append(("cls", cl))

    @staticmethod
    def _word(w): return ("word", w)

    def highlightBlock(self, text):
        import re
        for kind, fmt in self.rules:
            if kind == "word": pass
            elif kind == "num":
                for m in re.finditer(r"\b\d+(?:\.\d+)?\b", text):
                    self.setFormat(m.start(), m.end() - m.start(), fmt)
            elif kind == "str":
                for pat in (r'"[^"\\]*(?:\\.[^"\\]*)*"', r"'[^'\\]*(?:\\.[^'\\]*)*'"):
                    for m in re.finditer(pat, text):
                        self.setFormat(m.start(), m.end() - m.start(), fmt)
            elif kind == "com":
                m = re.search(r"#.*$", text)
                if m: self.setFormat(m.start(), m.end() - m.start(), fmt)
            elif kind == "def":
                m = re.search(r"\bdef\s+([A-Za-z_]\w*)", text)
                if m: self.setFormat(m.start(1), m.end(1) - m.start(1), fmt)
            elif kind == "cls":
                m = re.search(r"\bclass\s+([A-Za-z_]\w*)", text)
                if m: self.setFormat(m.start(1), m.end(1) - m.start(1), fmt)
        for kind, w in self.rules:
            if kind != "word": continue
            for m in re.finditer(rf"\b{re.escape(w)}\b", text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)


class ScriptEditorDialog(QDialog):
    def __init__(self, path, editor, parent=None):
        super().__init__(parent or editor)
        self.path = path
        self.editor = editor
        self.setWindowTitle(f"Script - {Path(path).name}")
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        self.code = QPlainTextEdit()
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(10)
        self.code.setFont(mono)
        self.code.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; border:none;"
            "selection-background-color:#264f78;")
        self.code.setTabStopDistance(self.code.fontMetrics().horizontalAdvance(" ") * 4)
        self.hl = PythonHighlighter(self.code.document())
        try:
            self.code.setPlainText(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
        layout.addWidget(self.code, 1)
        row = QHBoxLayout()
        save = QPushButton("Save (Ctrl+S)")
        save.setShortcut("Ctrl+S")
        save.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addStretch(); row.addWidget(save); row.addWidget(close)
        layout.addLayout(row)

    def _save(self):
        try:
            Path(self.path).write_text(self.code.toPlainText(), encoding="utf-8")
            self.editor.log_message(f"Saved script: {self.path}")
            self.editor.reload_scripts()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


# ============================================================
# AnimaStudio 主窗口
# ============================================================
class AnimaStudio(HammerMainWindow):
    def __init__(self, lang="en"):
        super().__init__(lang)
        self.setWindowTitle(f"Anima Studio - {self.windowTitle()}")

        self.anima = None
        self.anima_scene = None
        self.anima_scene_backup = None
        self.is_playing = False
        self.selected_object = None
        self._last_sel = None
        self.project_dir = Path(__file__).resolve().parent
        self.scripts_dir = self.project_dir / "scripts"
        self.old_scripts_dir = None

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._on_play_tick)

        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._sync_inspector)
        self._sync_timer.start(200)

        self._build_docks()
        self._install_play_actions()
        
        # 初始化项目
        self._set_project(self.project_dir)
        
        self.log_message("[studio] Anima Studio ready")

    def _set_project(self, folder_path):
        self.project_dir = Path(folder_path)
        self.scripts_dir = self.project_dir / "scripts"
        
        # 确保 scripts 目录存在
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        
        # 清理旧的 sys.path
        if self.old_scripts_dir and str(self.old_scripts_dir) in sys.path:
            sys.path.remove(str(self.old_scripts_dir))
        self.old_scripts_dir = self.scripts_dir
        
        ensure_example_scripts(self.scripts_dir)
        
        # 加载新项目的 anima 模块
        self.anima = ensure_anima_module(self.scripts_dir)
        
        if self.anima is not None:
            try:
                self.anima._set_log(self.log_message)
                self.anima._set_scene_getter(lambda: self.anima_scene)
            except Exception:
                pass
                
        # 同步给 UI（不再触发信号导致死循环）
        self.project_panel.set_ui_project(self.project_dir)
        self.log_message(f"[project] 切换项目到 {self.project_dir}")

    def _build_docks(self):
        # 左侧：Hierarchy
        self.hierarchy_panel = HierarchyPanel(self)
        self.hierarchy_panel.object_selected.connect(self._on_hierarchy_select)
        self.hierarchy_dock = QDockWidget("Hierarchy", self)
        self.hierarchy_dock.setWidget(self.hierarchy_panel)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.hierarchy_dock)

        # 右侧：Inspector
        self.inspector_panel = InspectorPanel(self)
        self.inspector_dock = QDockWidget("Inspector", self)
        self.inspector_dock.setWidget(self.inspector_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, self.inspector_dock)

        # 底部：Project
        self.project_panel = ProjectPanel(self)
        self.project_panel.open_script.connect(self._open_script)
        self.project_panel.request_open_project.connect(self._set_project)
        self.project_dock = QDockWidget("Project", self)
        self.project_dock.setWidget(self.project_panel)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.project_dock)

        # 底部：Game 视图
        self.game_view = GameGLWidget(self)
        self.game_dock = QDockWidget("Game (Play)", self)
        self.game_dock.setWidget(self.game_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.game_dock)
        self.game_dock.hide()

        # 深色样式覆盖
        dock_qss = """
        QDockWidget { color: #d8d8d8; background-color: #3c3c3c; }
        QDockWidget::title { background-color: #2b2b2b; padding-left: 6px; text-align: left; border-bottom: 1px solid #1e1e1e; }
        QTreeWidget { background-color: #2b2b2b; color: #d8d8d8; border: 1px solid #1e1e1e; }
        QTreeWidget::item:hover { background-color: #4a4a4a; }
        QTreeWidget::item:selected { background-color: #4a6a8a; color: #ffffff; }
        QScrollArea { background-color: #3c3c3c; border: none; }
        QScrollArea > QWidget > QWidget { background-color: #3c3c3c; }
        """
        self.hierarchy_dock.setStyleSheet(dock_qss)
        self.inspector_dock.setStyleSheet(dock_qss)
        self.project_dock.setStyleSheet(dock_qss)
        self.game_dock.setStyleSheet(dock_qss)

    def _install_play_actions(self):
        toolbars = self.findChildren(QToolBar)
        target = toolbars[0] if toolbars else None
        if target is not None:
            target.addSeparator()
            self._act_play = QAction("▶ Play", self)
            self._act_play.triggered.connect(self.on_play)
            target.addAction(self._act_play)
            self._act_stop = QAction("■ Stop", self)
            self._act_stop.triggered.connect(self.on_stop)
            self._act_stop.setEnabled(False)
            target.addAction(self._act_stop)

        for m in self.menuBar().findChildren(QMenu):
            if m.title() in ("Window", "窗口"):
                m.addSeparator()
                m.addAction("Toggle Hierarchy", lambda: self.hierarchy_dock.setVisible(not self.hierarchy_dock.isVisible()))
                m.addAction("Toggle Inspector", lambda: self.inspector_dock.setVisible(not self.inspector_dock.isVisible()))
                m.addAction("Toggle Project", lambda: self.project_dock.setVisible(not self.project_dock.isVisible()))
                m.addAction("Toggle Game View", lambda: self.game_dock.setVisible(not self.game_dock.isVisible()))
                break

    def get_script_classes(self):
        if self.anima is None: return []
        self.reload_scripts()
        out = []
        for f in sorted(self.scripts_dir.rglob("*.py")):
            if f.name == "anima.py": continue
            cls = self._load_script_class(f)
            if cls is not None: out.append((cls.__name__, cls))
        return out

    def reload_scripts(self):
        if not self.scripts_dir.exists(): return
        try:
            if "anima" in sys.modules:
                importlib.reload(sys.modules["anima"])
            else:
                import anima
            self.anima = sys.modules["anima"]
            self.anima._set_log(self.log_message)
            self.anima._set_scene_getter(lambda: self.anima_scene)
        except Exception as e:
            self.log_message(f"[anima] reload failed: {e}")

    def _load_script_class(self, path):
        try:
            mod_name = f"_anima_user_{path.stem}_{int(time.time() * 1000)}_{id(path)}"
            spec = importlib.util.spec_from_file_location(mod_name, str(path))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if isinstance(obj, type) and issubclass(obj, self.anima.Script) and obj is not self.anima.Script:
                    return obj
        except Exception as e:
            self.log_message(f"[script] {path.name}: {e}")
        return None

    def _open_script(self, path):
        dlg = ScriptEditorDialog(path, self, self)
        dlg.exec_()
        self.reload_scripts()
        self.project_panel.refresh()

    def _on_hierarchy_select(self, data):
        if data is None:
            self.selected_object = None
            self.inspector_panel.set_object(None)
            return
        if isinstance(data, tuple) and data[0] == "gameobject":
            self.selected_object = data[1]
            self.inspector_panel.set_object(data[1])
        elif isinstance(data, tuple) and data[0] in ("block", "entity"):
            self.inspector_panel.set_object(data)

    def _sync_inspector(self):
        if self.is_playing: return
        sel = (tuple(self.selected_block_indices), tuple(self.selected_entity_indices))
        if sel != self._last_sel:
            self._last_sel = sel
            self.inspector_panel.set_object(None)
            if hasattr(self, "hierarchy_panel"):
                self.hierarchy_panel.refresh()

    def refresh_ui_lists(self):
        super().refresh_ui_lists()
        if hasattr(self, "hierarchy_panel"):
            self.hierarchy_panel.refresh()

    def on_play(self):
        if self.is_playing: return
        if self.anima is None:
            QMessageBox.warning(self, "Play", "anima.py module not available.")
            return
        try:
            self.anima_scene_backup = copy.deepcopy(self.anima_scene) if self.anima_scene else None
        except Exception:
            self.anima_scene_backup = None

        self.anima_scene = build_scene_from_map(self.anima, self.blocks, self.entities, self.spawn)
        if self.anima_scene is None:
            QMessageBox.warning(self, "Play", "Scene build failed.")
            return

        try:
            self.anima._set_scene(self.anima_scene)
            T = self.anima.Time
            T.time = 0.0
            T.delta_time = 0.0
            T.frame_count = 0
        except Exception: pass

        for obj in self.anima_scene.all_objects():
            for c in obj.components:
                try: c.on_awake()
                except Exception as e: self.log_message(f"[Awake] {type(c).__name__}: {e}")
        for obj in self.anima_scene.all_objects():
            for c in obj.components:
                try: c.on_start()
                except Exception as e: self.log_message(f"[Start] {type(c).__name__}: {e}")

        self.is_playing = True
        self._act_play.setEnabled(False)
        self._act_stop.setEnabled(True)
        self._play_timer.start(16)
        self.hierarchy_panel.set_play_mode(True)
        self.inspector_panel.set_play_mode(True)
        self.game_dock.show()
        self.game_view.set_play_camera(self.spawn)
        self.game_view.setFocus()
        self.log_message("[play] scene running")

    def on_stop(self):
        if not self.is_playing: return
        self._play_timer.stop()
        if self.anima_scene is not None:
            for obj in self.anima_scene.all_objects():
                for c in obj.components:
                    try: c.on_destroy()
                    except Exception: pass
        self.anima_scene = self.anima_scene_backup
        self.anima_scene_backup = None
        try: self.anima._set_scene(self.anima_scene)
        except Exception: pass
        self.is_playing = False
        self.selected_object = None
        self._act_play.setEnabled(True)
        self._act_stop.setEnabled(False)
        self.hierarchy_panel.set_play_mode(False)
        self.inspector_panel.set_play_mode(False)
        self.game_dock.hide()
        self.log_message("[play] stopped")

    def _on_play_tick(self):
        if not self.is_playing or self.anima_scene is None: return
        dt = 1.0 / 60.0
        try:
            T = self.anima.Time
            T.delta_time = dt
            T.time += dt
            T.frame_count += 1
        except Exception: pass
        for obj in self.anima_scene.all_objects():
            if not obj.active: continue
            for c in obj.components:
                if not getattr(c, "enabled", True): continue
                try: c.on_update()
                except Exception as e:
                    self.log_message(f"[Update] {type(c).__name__}: {e}")
                    c.enabled = False

    def _write_map_to(self, path):
        super()._write_map_to(path)
        self.hierarchy_panel.refresh()

    def _on_sky_changed(self, name):
        super()._on_sky_changed(name)


# ============================================================
# 入口
# ============================================================
def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(hammer.DARK_QSS)

    settings = load_settings()
    game_config = settings.get("game_config")
    if game_config not in hammer.GAME_CONFIGS:
        dlg = GameConfigDialog()
        if dlg.exec_() != QDialog.Accepted: return 0
        game_config = dlg.get_config()
        save_settings({"game_config": game_config})

    saved_lang = settings.get("lang")
    lang = saved_lang if saved_lang in LANGUAGES else "en"
    win = AnimaStudio(lang)
    win.game_config = game_config
    win.show()
    return app.exec_()

if __name__ == "__main__":
    sys.exit(main())