"""Rocket launcher behaviour for the ``rpg`` weapon.

Left mouse fires the rocket (the projectile, its impact blast and its look live
in ``addons/ammo/rocket.txt``).  Right mouse toggles **laser guidance**: while
it is active every rocket in flight is bent toward the crosshair, so the player
can steer a shot around a corner - the HL2 RPG's defining feature.

The steering is a pure velocity rotation, so the projectile still collides,
still hits walls and still triggers the same explosion path as an unguided one.
"""
import math

import glm

from engine_pkg.core.utils import log

#: how fast a guided rocket can turn (radians / second)
MAX_TURN = 3.2


def _auto_reload(app, wdef):
    if app.weapon.magazine > 0 or app.weapon.reserve <= 0:
        return
    if float(getattr(app, "_reload_t", 0.0) or 0.0) > 0.0:
        return
    dur = max(0.1, float(getattr(wdef, "reload_duration", 1.5) or 1.5))
    try:
        app._reload_t = dur
        app.view_anim.on_reload(dur)
        app._skin_weapon_play("reload")
    except Exception as ex:
        log(f"[rpg] reload animation failed: {ex}")


def on_alt_fire(app, wdef):
    """Toggle the guidance laser (right mouse)."""
    app._rpg_laser = not bool(getattr(app, "_rpg_laser", False))
    log(f"[rpg] laser guidance {'ON' if app._rpg_laser else 'OFF'}")


def on_fire(app, wdef):
    """Tag the rocket that just left the tube as guided / unguided."""
    guided = bool(getattr(app, "_rpg_laser", False))
    for b in reversed(app.bullets):
        if b.get("ammo") == "rocket":
            b["guided"] = guided
            break
    log(f"[rpg] rocket fired ({'guided' if guided else 'dumb'})")
    _auto_reload(app, wdef)


def on_update(app, wdef, delta):
    """Steer every guided rocket toward the current crosshair."""
    if not bool(getattr(app, "_rpg_laser", False)):
        return
    if not getattr(app, "bullets", None):
        return
    look = getattr(app, "look_dir", None)
    if look is None:
        return
    want = glm.normalize(glm.vec3(float(look.x), float(look.y), float(look.z)))
    for b in app.bullets:
        if b.get("ammo") != "rocket" or not b.get("guided"):
            continue
        vel = b.get("vel")
        if vel is None:
            continue
        speed = float(glm.length(vel))
        if speed < 1e-3:
            continue
        cur = vel / speed
        dot = max(-1.0, min(1.0, float(glm.dot(cur, want))))
        if dot > 0.9995:
            continue
        angle = math.acos(dot)
        # rotate at most MAX_TURN * delta along the arc, so the rocket curves
        # instead of snapping instantly onto the crosshair
        max_angle = MAX_TURN * max(0.0, float(delta))
        blend = min(1.0, max_angle / max(1e-5, angle))
        new_dir = glm.normalize(cur * (1.0 - blend) + want * blend)
        b["vel"] = new_dir * speed


def on_empty(app, wdef):
    _auto_reload(app, wdef)


__all__ = ["on_alt_fire", "on_fire", "on_update", "on_empty"]
