"""Gravity gun behaviour for the physcannon tool weapon.

Right mouse grabs / releases the aimed prop (HL2-style: it floats at the
distance it was grabbed at). Left mouse throws a held prop; with empty hands it
gives whatever is in front a small shove.
"""
import glm

from engine_pkg.core.utils import log
from engine_pkg.player import controller as PC


def on_alt_fire(app, wdef):
    pl = app.player
    if pl is None:
        return
    if pl.get("held_obj") is not None:
        PC.drop_held(pl)
        return
    if not PC.try_pickup(pl, app.scene.shash):
        return
    held = pl.get("held_obj")
    if held is None:
        return
    # Keep the object at the distance where it was grabbed instead of pulling
    # it into the player's chest (that is what makes the gravity gun feel like
    # a gravity gun rather than the hand-carry system).
    try:
        cam = app.cam_pos
        ox, oy, oz = held["pos"]
        d = glm.length(glm.vec3(ox - cam.x, oy - cam.y, oz - cam.z))
        held["hold_dist"] = max(1.2, min(8.0, float(d)))
    except Exception:
        pass
    log("[physcannon] grabbed")


def on_fire(app, wdef):
    pl = app.player
    if pl is None:
        return
    held = pl.get("held_obj")
    if held is not None:
        PC.throw_held(pl, held)
        return
    # HL2 primary fire grabs the prop under the crosshair; click again to
    # throw it.  Right mouse still grabs/drops for players used to that.
    if PC.try_pickup(pl, app.scene.shash):
        held = pl.get("held_obj")
        if held is not None:
            try:
                cam = app.cam_pos
                ox, oy, oz = held["pos"]
                d = glm.length(glm.vec3(ox - cam.x, oy - cam.y, oz - cam.z))
                held["hold_dist"] = max(1.2, min(8.0, float(d)))
            except Exception:
                pass
            try:
                app._beam_pulse = 0.0
            except Exception:
                pass
            log("[physcannon] grabbed")
            return
    # Empty-hand shove: nudge the nearest light prop in the crosshair direction.
    try:
        cam = app.cam_pos
        look = app.look_dir
        best = None
        best_d = 4.0
        for o in app.scene.objects:
            if not o.get("is_model") or not o.get("half"):
                continue
            if o is held or o.get("phys_type") != "prop_physics":
                continue
            ox, oy, oz = o["pos"]
            d = ((ox - cam.x) ** 2 + (oy - cam.y) ** 2 + (oz - cam.z) ** 2) ** 0.5
            if d > best_d:
                continue
            dot = ((ox - cam.x) * look.x + (oy - cam.y) * look.y
                   + (oz - cam.z) * look.z) / max(1e-6, d)
            if dot < 0.85:
                continue
            best_d = d
            best = o
        if best is not None:
            from engine_pkg.world.physics import wake_object
            wake_object(best)
            best["vel"] += glm.vec3(look.x, look.y * 0.3, look.z) * 0.12
        # Show a short force-push beam even with empty hands: the tether used
        # to be the only beam, so firing an empty gravity gun looked broken.
        # The pulse covers the weapon's whole fire interval, otherwise holding
        # the trigger blinked the beam on/off and read as a stutter.
        try:
            interval = 1.0 / max(0.1, float(getattr(wdef, "fire_rate", 3.0)
                                            or 3.0))
            app._beam_pulse = max(0.25, interval + 0.10)
            if best is not None:
                bp = best["pos"]
                app._beam_pulse_target = (float(bp[0]), float(bp[1]),
                                          float(bp[2]))
            else:
                app._beam_pulse_target = (cam.x + look.x * 3.0,
                                          cam.y + look.y * 3.0,
                                          cam.z + look.z * 3.0)
        except Exception:
            pass
    except Exception:
        pass
