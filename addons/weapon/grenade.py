"""Grenade behaviour for the throwable ``grenade`` weapon.

The projectile itself (bounce, fuse, blast) is entirely data driven by
``addons/ammo/grenade.txt``; this script only handles the magazine: a grenade
weapon holds one, so after a throw the next one is readied automatically
instead of forcing the player to press R between throws.
"""
from engine_pkg.core.utils import log


def _auto_reload(app, wdef):
    """Start the normal timed reload when the last grenade has been thrown."""
    if app.weapon.magazine > 0 or app.weapon.reserve <= 0:
        return
    if float(getattr(app, "_reload_t", 0.0) or 0.0) > 0.0:
        return
    dur = max(0.1, float(getattr(wdef, "reload_duration", 0.75) or 0.75))
    try:
        app._reload_t = dur
        app.view_anim.on_reload(dur)
        app._skin_weapon_play("reload")
    except Exception as ex:
        log(f"[grenade] reload animation failed: {ex}")


def on_fire(app, wdef):
    log("[grenade] thrown")
    _auto_reload(app, wdef)


def on_empty(app, wdef):
    _auto_reload(app, wdef)


__all__ = ["on_fire", "on_empty"]
