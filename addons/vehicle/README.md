# Vehicle addons

Drop a `.txt` into `addons/vehicle/`. Like models and NPCs, a vehicle placed in
the map editor compiles to an `addon_entity` whose `kind` is the vehicle's
`reg_name`, so `type = buggy` entities spawn the demo buggy.

```ini
reg_name = buggy
world_model = vehicle/buggy/buggy.obj
world_texture = vehicle/buggy/buggy.png
scale = 2.6
mass = 800
hp = 400
rotation = 0, 0, 0        # static model correction (degrees)
yaw_offset = 0.0          # forward-axis correction (degrees), applied to the model angle

type = ground              # ground | hover
hover_height = 1.2
drive_speed = 10.0
boost_speed = 17.0
reverse_speed = 4.5
turn_speed = 1.25
accel = 8.0
brake = 20.0
drag = 2.6
enter_radius = 2.8
seat_offset = 0.16, -0.49, 0.21 # seat base (from the model's Rig_Buggy.view bone)
seat_eye_height = 0.55          # camera height above the seat base
exit_offset = 0, 0, -2.0
camera_distance = 7.0
camera_height = 3.0

mount_weapon = smg1        # left mouse button fires this while driving
mount_offset = 0, 1.4, 0.9
mount_damage_scale = 1.0

engine_sound =              # optional; empty = synthesised engine loop
start_sound =
stop_sound =
horn_sound =
crash_sound =
sound_volume = 0.55

script =                    # optional; addons/vehicle/<reg_name>.py is default
```

## Controls

| Key | Action |
| --- | --- |
| E | get in / get out (nearest vehicle inside `enter_radius`) |
| Mouse | look / aim; the vehicle steers toward it |
| W / S | accelerate / brake+reverse |
| A / D | direct steering (adds to look steering) |
| Shift | boost |
| Space | brake / handbrake |
| Left mouse | fire `mount_weapon` |
| Ctrl | toggle first-person / third-person camera |

The vehicle samples ground under its four wheel corners. If its centre passes a ledge (or fewer than three wheels are supported) it tips and falls under gravity; parked vehicles settle the same way.
| H | horn |

Console spawning: `vehicle buggy`, `spawn vehicle buggy`, or `spawn buggy`.
`vehicles` lists the loaded vehicle addons.

## Scripts and weapons

`addons/vehicle/<reg_name>.py` receives `on_spawn`, `on_enter`, `on_exit`,
`on_update`, `on_fire` and `on_crash` hooks. The mounted weapon is an ordinary
weapon addon, so its own `addons/weapon/<reg_name>.py` hooks (`on_fire`, ...)
also run. At runtime:

```python
from engine_pkg.world.vehicle import attach_weapon, detach_weapon
attach_weapon(vehicle_obj, "shotgun", offset=(0, 1.5, 1.0))
detach_weapon(vehicle_obj)
```

The menu background (`menu/menu_bg.txt`) can also point at a map containing a
vehicle: the menu player is invulnerable and NPC AI is paused.

The chassis also pitches under acceleration/braking and the chase camera lags slightly, so mass is felt while driving; ledge support samples four wheel corners and the centre and falls instead of hovering.
