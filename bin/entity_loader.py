"""Entity loader for Hammer editor — 从 addons 扫描实体 + 内置实体。"""
import os
from pathlib import Path


def _read_kv(path):
    kv = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("//"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    kv[k.strip()] = v.strip().strip('"')
    except Exception:
        pass
    return kv


# ============================================================
# 内置实体
# ============================================================
def builtin_entities():
    return {
        "info_player_start": {
            "label": "info_player_start",
            "category": "Spawn",
            "color": (80, 220, 100),
            "size": 0.5,
            "params": {},
        },
        "light_environment": {
            "label": "light_environment",
            "category": "Lighting",
            "color": (255, 240, 160),
            "size": 0.5,
            "params": {
                "yaw": 45.0,
                "pitch": -35.0,
                "r": 1.0, "g": 0.97, "b": 0.90,
                "intensity": 1.0,
            },
        },
        "light": {
            "label": "light",
            "category": "Lighting",
            "color": (255, 240, 160),
            "size": 0.4,
            "params": {
                "r": 1.0, "g": 1.0, "b": 1.0,
                "intensity": 1.0,
            },
        },
        "weapon_spawn": {
            "label": "weapon_spawn",
            "category": "Item",
            "color": (200, 100, 255),
            "size": 0.4,
            "params": {"weapon": "pistol"},
        },
        "item_spawn": {
            "label": "item_spawn",
            "category": "Item",
            "color": (100, 200, 120),
            "size": 0.35,
            "params": {"item": ""},
        },
        "npc": {
            "label": "npc",
            "category": "NPC",
            "color": (220, 80, 80),
            "size": 0.5,
            "params": {"npc": ""},
        },
        "prop_static": {
            "label": "prop_static",
            "category": "Prop",
            "color": (200, 200, 200),
            "size": 0.4,
            "params": {"model": ""},
        },
        "trigger": {
            "label": "trigger",
            "category": "Trigger",
            "color": (220, 180, 60),
            "size": 0.45,
            "params": {"name": ""},
        },
    }


# ============================================================
# 扫描 addons
# ============================================================
def scan_addons(addons_root):
    out = {}
    root = Path(addons_root)
    if not root.exists():
        return out

    cat_map = {
        "models":      "Model",
        "npc":         "NPC",
        "weapon":      "Weapon",
        "items":       "Item",
        "ammo":        "Ammo",
        "water":       "Water",
        "trigger":     "Trigger",
        "interactive": "Interactive",
        "faction":     "Faction",
    }

    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        cat = cat_map.get(folder.name, folder.name.capitalize())
        for f in sorted(folder.glob("*.txt")):
            kv = _read_kv(f)
            reg = kv.get("reg_name", "").strip() or f.stem

            # 颜色
            col = (180, 180, 220)
            try:
                sc = kv.get("color", "")
                if sc:
                    parts = [float(x.strip()) for x in sc.replace("，", ",").split(",")]
                    if len(parts) >= 3:
                        col = tuple(int(p * 255) if p <= 1 else int(p) for p in parts[:3])
            except Exception:
                pass

            # 尺寸
            try:
                sz = float(kv.get("scale", 0.5))
            except Exception:
                sz = 0.5
            sz = max(0.15, min(1.0, sz))

            out[reg] = {
                "label":    reg,
                "folder":   folder.name,
                "file":     str(f),
                "category": cat,
                "color":    col,
                "size":     sz,
                "params":   kv,
            }
    return out


def load_all_entities(addons_root):
    ents = builtin_entities()
    ents.update(scan_addons(addons_root))
    return ents


def find_addons_root(start_dir):
    p = Path(start_dir).resolve()
    for _ in range(6):
        cand = p / "addons"
        if cand.is_dir():
            return cand
        p = p.parent
    return None


def entity_to_map_line(etype, pos, params):
    """转成地图 txt 一行。"""
    x, y, z = pos
    params = params or {}

    if etype == "light_environment":
        return (f"light_environment {params.get('yaw', 45)} "
                f"{params.get('pitch', -35)} "
                f"{params.get('r', 1)} {params.get('g', 0.97)} "
                f"{params.get('b', 0.9)} {params.get('intensity', 1)}")

    if etype == "light":
        return (f"light {x:.4f} {y:.4f} {z:.4f} "
                f"{params.get('r', 1)} {params.get('g', 1)} "
                f"{params.get('b', 1)} {params.get('intensity', 1)}")

    if etype == "weapon_spawn":
        return f"weapon_spawn {x:.4f} {y:.4f} {z:.4f} {params.get('weapon', '')}"

    if etype == "item_spawn":
        return f"item_spawn {x:.4f} {y:.4f} {z:.4f} {params.get('item', '')}"

    if etype == "npc":
        return f"npc {x:.4f} {y:.4f} {z:.4f} {params.get('npc', '')}"

    if etype == "prop_static":
        return f"models {params.get('model', '')} {x:.4f} {y:.4f} {z:.4f} 1.0"

    if etype == "trigger":
        return f"trigger {params.get('name', '')} {x:.4f} {y:.4f} {z:.4f}"

    if etype == "info_player_start":
        return f"entity info_player_start {x:.4f} {y:.4f} {z:.4f}"

    # addon 实体（models/npc 里注册过的 reg_name）
    return f"{etype} {x:.4f} {y:.4f} {z:.4f}"


__all__ = [
    "builtin_entities", "scan_addons", "load_all_entities",
    "find_addons_root", "entity_to_map_line",
]