"""Example vehicle script (``addons/vehicle/buggy.py``).

The engine calls any of these functions the script defines, exactly like a
model/NPC script:

    def on_spawn(app, obj)                  vehicle created
    def on_enter(app, obj, player)          player got in
    def on_exit(app, obj, player)           player got out
    def on_update(app, obj, delta)          every game tick
    def on_fire(app, obj, wdef)             mounted weapon fired a round
    def on_crash(app, obj, speed)           bumped into a wall

Use :func:`engine_pkg.world.vehicle.attach_weapon` /
:func:`engine_pkg.world.vehicle.detach_weapon` to swap the mounted weapon at
runtime, or ``obj["attached_weapon"] = "shotgun"`` directly.
"""


def on_enter(app, obj, player):
    player["driving_vehicle"] = obj.get("vehicle_name", "vehicle")


def on_exit(app, obj, player):
    player.pop("driving_vehicle", None)


def on_fire(app, obj, wdef):
    # Small recoil kick on the vehicle model.
    obj["angle"].x = min(0.06, float(obj["angle"].x) + 0.015)
