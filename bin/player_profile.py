"""玩家资料：名称 / 皮肤 / 颜色，保存到 bin/player_profile.json"""
import json
from pathlib import Path

PROFILE_PATH = Path(__file__).parent / "player_profile.json"

DEFAULT = {
    "name": "Player",
    "skin": "",
    "color": [0.2, 0.6, 1.0],
}


class Profile:
    def __init__(self):
        self.data = dict(DEFAULT)
        self.load()

    def load(self):
        try:
            if PROFILE_PATH.exists():
                raw = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
                self.data = {**DEFAULT, **raw}
        except Exception as e:
            print(f"[profile] load failed: {e}")

    def save(self):
        try:
            PROFILE_PATH.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception as e:
            print(f"[profile] save failed: {e}")

    @property
    def name(self):
        return str(self.data.get("name", "Player"))[:32] or "Player"

    @name.setter
    def name(self, v):
        self.data["name"] = str(v)[:32]

    @property
    def skin(self):
        return str(self.data.get("skin", ""))

    @skin.setter
    def skin(self, v):
        self.data["skin"] = str(v)

    @property
    def color(self):
        c = self.data.get("color", [0.2, 0.6, 1.0])
        return (float(c[0]), float(c[1]), float(c[2]))

    @color.setter
    def color(self, v):
        self.data["color"] = [float(v[0]), float(v[1]), float(v[2])]


PROFILE = Profile()