# Gameplay: slide, falls, death, headshots and vehicles

## Slide

While moving on the ground, press **Ctrl** while sprinting (or within 0.28 s of
sprinting), **Ctrl + Shift**, or **C**. The request is buffered for 0.18 s, so a
press that lands just outside a valid frame still starts the slide on the next
one instead of doing nothing.

The player drops into a crouched slide, launches at ~1.55x sprint speed and
bleeds speed over ~0.9 s. **A / D** steer; the camera banks into the turn.
Jumping out ends it. When it runs out the remaining momentum carries for 0.18 s
(run-out) instead of snapping to crouch speed.

Walking speed + **Ctrl** still crouches. A slide attempt never flashes a crouch
first (Ctrl has a 0.10 s hold delay before it means "crouch"), and a slide never
triggers a parkour roll on a small step.

The slide only re-arms after you release the keys, so holding them does not
chain slides. It has a 0.55 s cooldown and is disabled with broken legs.

Tuning lives in `engine_pkg/core/config.py` under `player`:
`slide_speed`, `slide_duration`, `slide_friction`, `slide_cooldown`,
`slide_eye_offset`, `slide_input_buffer`, `slide_sprint_grace`,
`slide_ground_grace`, `slide_cam_roll`, `slide_cam_roll_rate`.

Verification: `python tools/verify_slide.py`.

## Fall damage and broken legs

Landing above roughly a normal jump's speed hurts:

* `fall_damage_speed` — impact threshold (per-frame velocity; a normal jump is
  below it),
* `fall_damage_scale` — hp per unit above the threshold,
* a landing roll absorbs 45 % of the damage.

A hard landing (or enough damage) breaks the legs for 12 s: movement drops to
45 %, sprinting and jumping are disabled, and the slide cannot start. There is
a 1 s grace period after spawn/respawn so the spawn drop never hurts.

## Death

Death is handled centrally in `App._handle_death_transition`:

* the cause is recorded (shot, headshot, explosion, trigger, fall, void),
* the held object and weapon are dropped and a vehicle is left,
* the camera collapses into the death animation and a **YOU DIED** overlay
  shows the cause and a 5 s respawn countdown,
* left click respawns immediately, otherwise respawn happens automatically.

## Headshots

Bullets that hit the upper part of a character's AABB (`headshot_height_frac`)
deal `headshot_multiplier` damage (default **2.5x**). This applies to NPCs and
replicated player proxies; the `headshot` flag travels with `hit_player` /
`player_damaged` so the victim's death cause says `(headshot)`.

## Vehicles

* **Look-based steering.** `pl["yaw"]` is the mouse look and is not overwritten
  by the vehicle heading any more. The car steers toward the look direction
  (55 deg of aim = full lock, 3 deg deadzone); **A / D** add direct steering.
* **The driver sits inside the hull.** `seat_offset` is the seat *base* (hips)
  in vehicle-local space and `seat_eye_height` is added for the camera; both
  are clamped into the collision box. The player's eye is now at ~1.0 m above
  the floor (a real seated height), not standing height over the hood.
* **Ledge support.** The chassis samples ground under its four wheel corners
  and its centre. If the centre is past the edge (or fewer than three wheels
  have ground), it tips and falls under gravity instead of hovering on a
  centre query whose radius still touches the ledge. Unoccupied vehicles settle
  and fall too.
* **Weight.** `mass` now scales acceleration (`mass_f = 700/mass`); a heavy
  vehicle takes longer to build speed, the chassis pitches under longitudinal
  acceleration (squat/dive), rolls with lateral load, and the chase camera lags
  the chassis slightly.
* **Impacts shove and kill.** The chassis ignores light props (`mass <= 40`)
  and NPCs when it tests whether it can move, then a per-step impact pass
  wakes the prop, shoves it along the bumper and drives the car through it, so
  a car plows through a crate pile instead of nudging it once and grinding to
  a halt. Props are only *smashed* above `IMPACT_CRUSH_SPEED` (11 m/s): normal
  driving scatters a stack, a full-boost ram breaks it. NPCs take damage on a
  0.3 s per-target cooldown (a 9 m/s hit kills a 100 hp NPC, a slow bump only
  hurts) and are knocked back through their own collision-aware movement.
* **Driving feel.** Speed-dependent acceleration, engine braking, rolling/aero
  drag, slope gravity, a speed-limited steering rate, mild lateral slip on hard
  turns, handbrake rotation and a spring suspension. The chassis pitches on
  hills and rolls in turns, and the chase camera banks with it.
* `yaw_offset` (degrees) corrects a model whose forward axis is not +Z; it is
  applied to the rendered vehicle angle and the collision footprint is swapped
  for 90/270-degree corrections. The demo buggy is already nose-along-Z
  (`yaw_offset = 0.0`).
* **Ctrl** toggles first-person / third-person while driving. Ctrl still
  crouches on foot.
* Console: `vehicle buggy`, `spawn vehicle buggy`, `spawn buggy`, `vehicles`.

Verification: `python tools/verify_vehicle.py`,
`python tools/verify_vehicle_cliff.py`,
`python tools/verify_vehicle_impact.py`.

## Shadows

* The sun shadow map now renders **real model triangles** (per-submesh VBO/IBO)
  instead of a unit cube scaled to the AABB. Holes, open frames and cut-out
  foliage therefore cast matching shadows instead of a rectangle.
* Casters are **two-sided**, so a one-sided shell still occludes light as the
  sun moves. Cut-out materials (`alpha_test`) discard their transparent texels
  in the depth pass using the same texture.
* The depth bias is expressed in **light-space depth units** (about one world
  texel, slope-scaled), not a fixed NDC constant. The old `0.0015` bias was
  ~0.43 m over the ortho depth range, which lifted a thin wall's shadow off the
  floor behind it and let light appear to pass through the wall.
* The fitted light box is snapped to a **world-space texel grid**, and the
  shadow depth texture uses `NEAREST` with the manual PCF kernel. Re-baking an
  unchanged world is pixel-identical instead of shimmering as the player walks.
* Only casters inside the fitted light frustum are drawn; the hundreds of props
  outside the 96-unit box cannot write a sampled texel.
* Skinned NPCs are cast in their current pose; terrain contact shadows use the
  character's projected mesh silhouette instead of a box rectangle.
* Dynamic props rotate their caster with their physics angle, so a tumbling
  crate's shadow turns with it.

Verification: `python tools/verify_shadow_geometry.py`,
`python tools/verify_shadow_update.py`.

## Collision hulls

* Static map models (`prop_static`: railings, grates, door frames, trees) use a
  **triangle grid** built from their real mesh, not the compound boxes filled by
  the fitter. Small openings are passable and ground/step/shadow queries use
  the actual surface; holes fall through.
* Rigid props keep the fast oriented-compound shape for the solver, and the
  player still gets the exact compound test (no regression for crates/barrels).
* `ANIMA_NO_MESH_COLLISION=1` disables the triangle path for debugging.

Verification: `python tools/verify_mesh_collision.py`.

## Lighting

* **Soft sun shadows** use a per-pixel rotated 24-tap Poisson disk whose
  penumbra is controlled by `rendering.shadow_softness` (default 9 texels);
  the receiver applies a slope-scaled normal offset to avoid acne.
* **Sky IBL**: the baked sky cubemap (or a tiny procedural 16x16x6 cube for the
  gradient sky) drives both diffuse sky colour and rough specular reflections,
  so metal, glass and water reflect the actual horizon instead of a constant.
* **Cheap AO/GI**: sun visibility also gates the sky ambient, so crevices and
  undersides sit in cooler, darker skylight instead of flat ambient.
* **Atmospheric perspective**: one `exp` per pixel blends distant geometry to
  the horizon colour (`rendering.fog_density`), preserving depth without a
  volumetric pass.
* **Filmic grade**: ACES, hue-preserving highlight roll-off, bloom, vignette,
  contrast/saturation and fine grain in the existing tone-map pass.  The deep-
  shadow lift that used to flatten all shadows has been narrowed to true
  blacks.
* Cost: **one extra cube fetch, one exp, a handful of ALU ops**; no extra render
  targets or passes.  `rendering.shadow_softness` can be lowered on old GPUs.

## Test lab

`map/testlab.amap` (built by `python tools/make_testlab.py`) contains:

* a physics area with steps, a drop platform, a doorway, rails and stackable
  crates/barrels;
* an uneven 32x32 height-field terrain with a pond;
* a rectangular water preview pool beside the showcase;
* a pedestal showcase of crates, drums, props, tree, NPCs, weapons and a
  drivable buggy.

Load it with `map testlab` or the map menu.  Verify with
`python tools/verify_testlab.py`.

## Developer test map

`map/devmap.amap` is an HL2-style developer level built by
`python tools/make_devmap.py` with the dev texture set from
`python tools/make_dev_textures.py`.  The engine projects world UVs at **one
texture tile per metre**, so every dev tile is a 1 m x 1 m reference square and
the textures are drawn in metres (Half-Life 2's own `dev_measure*` sheets are
annotated in Source units, which does not match this engine).

Areas: a blockout room with a 1.2 x 2.2 m doorway and a window, an 8-step
staircase (0.30 m risers) with a 2.4 m ledge for fall testing, a physics yard
with three four-crate stacks plus barrels and pallets, a vehicle court with a
ramp and kerbs, an NPC arena and an HL1 model alcove.  A **weapon deck** at the
+X end of the spawn plaza holds **every weapon** in the addon registry
(`pistol`, `shotgun`, `smg1`, `crowbar`, `knife`, `physcannon`, `grenade`,
`rpg`) with two ammo pickups; it is 43 m from the nearest NPC, so testing a gun
there never pulls the NPC arena into the fight.  `info_player_start` accepts
`params: {"yaw": 180}` so a spawn can face into the level instead of staring at
the +Z wall.

Three water volumes are placed for swim/splash testing away from the NPCs:

* a sunken swimming pool at (-11, -2): the floor slab is cut open, the waterline
  is 0.55 m below floor level and the pool is 2 m deep, so a jump in is a real
  swim;
* a shallow 0.7 m wading pool at (7, -2) with a dev-textured kerb;
* a 0.3 m puddle on the spawn plaza at (-12.5, 19).

The generator writes both `map/devmap.amap` and the editable Hammer source
`map/devmap.hmmap`.  Hammer maps cannot express NPC spawns, so the NPC arena is
only present in the compiled map (the Hammer source carries every solid, every
water volume and every weapon spawn).

Load with `map devmap`; verify with `python tools/verify_devmap.py`.

## Physics

* Adaptive **sub-stepping**: when a body would move further than
  `phys_substep_dist` (0.18 m) in one step, the step is split up to
  `phys_max_substeps`, preventing fast props from tunnelling through thin
  walls. Normal piles keep the single-step path.
* **Sleeping support tracking.** A sleeper does not collide, so removing the
  crate underneath it used to leave a stack hanging in mid air forever.  Every
  sleeper records the body (or terrain) directly beneath it; the probe only
  runs again when the world changes (a body is added/removed/grabbed) or when
  that supporter wakes.  Support must also overlap the body's XZ footprint, so
  the floor a metre below no longer counts.  Cost: a 50-crate sleeping pile is
  **0.06 ms/step** instead of the 24 ms a per-frame probe cost.
* **Validated warm starting.** Warm starting is what lets a five-crate stack
  settle instead of sinking, but replaying a cached impulse onto a contact
  whose normal or point has moved is how a settled pile ended up tumbling and
  falling through itself.  A cached impulse is only reused when the new contact
  normal agrees within ~20 degrees and the point is within 14 cm; static
  contacts are keyed by the static body as well, so a floor contact can no
  longer inherit a wall contact's impulse.  `SOLVER_ITERATIONS` is 20 /
  `RELAX` 0.70, which converges a 5-6 crate stack to a clean column.
* Rigid bodies use oriented, mesh-fitted collision shapes and a sequential
  impulse solver with warm starting, friction/restitution materials and
  sleeping. Static models add triangle-accurate collision (above).
* The solver's relaxation loop has a **Numba-native kernel**
  (`engine_pkg/world/physics_fast.py`), and the narrow phase/AABB helpers were
  rewritten as allocation-free scalar loops. Sleep, static-top and ground
  contacts take fast paths; a settled pile costs ~0.2 ms/step.
* The release build (`build.bat` / `build_pyd.py`) compiles `engine_pkg` with
  Cython, so the Python-side hot loops become native C as well.  A compiled
  `.pyd` left in the *development* tree shadows the newer `.py`, so
  `engine_pkg/__init__.py` warns when it finds one older than its source.
* `ANIMA_NO_NUMBA=1` forces the portable numpy solver.
* Benchmarks: `python tools/bench_physics.py [bodies] [frames]`,
  `python tools/verify_physics_ccd.py`,
  `python tools/verify_stack_support.py`.

## Half-Life 1 (GoldSrc) models

MDL version 10 is a single self-contained file: bones, bodyparts, mesh
triangle strips, vertex/normal arrays and palette textures all live inside it,
with no `.vvd`/`.vtx` pair.

* **Offsets.** GoldSrc writes `bodypart.modelindex`/`model.meshindex` relative
  to their containing struct, but real Half-Life content (including Valve's own
  `gman.mdl`, `grenade.mdl`, `glassgibs.mdl`) stores absolute file offsets in
  the same fields.  The loader validates both readings and takes the one whose
  struct looks sane.
* **Animation.** Per-sequence: the seqdesc holds `fps`, `numframes` and an
  offset to a per-bone table of six `short` channel offsets (relative to that
  bone's own six-short header).  Channels are a chain of `{valid,total}` blocks
  and the sample is `bone.value + sample * bone.scale`, exactly like the SDK's
  `CalcBonePosition`/`CalcBoneQuaternion`.  Skipping `scale` is what made HL1
  meshes explode into spikes.
* **Bone-local vertices.** GoldSrc stores mesh vertices in each bone's local
  frame (the engine draws them with `bonetransform[vertinfoindex]`, with no
  inverse bind).  The loader assembles the bind pose through each bone's bind
  world and keeps the existing model-space skinning path, so the character,
  view arms and weapons come out whole instead of as separate slabs.
* **Rotations are radians.**  The bone's `value[3..5]` and the animation
  deltas are radians; `AngleQuaternion` has no degree conversion.  Reading them
  as degrees left every animated bone almost unrotated against neighbours that
  kept the wrong bind rotation, tearing joints apart.
* **Demand-loaded sequence groups.**  Characters keep most clips in side files
  (`scientist01.mdl`, `zombie01.mdl`, ...); the seqdesc's `seqgroup` selects the
  file and `animindex` is relative to it.  Those clips are decoded from their
  own file (group 0 stays inside the model); a missing group is skipped instead
  of decoding unrelated bytes.
* **Textures.** Embedded 8-bit palette textures are decoded and exported to
  `materials/_mdl_embedded/`.  Models that store none get them from the
  Half-Life texture companion (`gman.mdl` -> `gmant.mdl`).  Bodyparts draw only
  their `base` model instead of stacking every bodygroup variant.
* `materials/sprites/*.vmt` parameters (`$basetexture`, `$spriteorigin`,
  `$additive`, ...) are read by `engine_pkg/resources/vmt.py`.

Verify with `python tools/verify_mdl_goldsrc.py`.

## Physics gun beam

Holding a prop with the physics gun draws a world-space tether from the muzzle
to the object:

* additive, HDR (values > 1 so the existing bloom glows), drawn between the
  world pass and the view model so the depth buffer still clips it;
* the sprite is `materials/sprites/laserbeam.jpg` (32x64, no alpha channel), so
  the shader derives alpha from luminance;
* `materials/sprites/laserbeam.vmt` supplies `$spriteorigin "[ 0.50 0.50 ]"`,
  which anchors the muzzle flare and the object glow;
* the beam fades in on grab and out on release, and breathes slowly (the old
  26 rad/s width pulse read as a stutter);
* **left mouse grabs** the prop under the crosshair (click again to throw it);
  right mouse also toggles grab/drop.  The grab lives in `physcannon.py`, but
  the engine has a grab/drop fallback (`_gravity_gun_toggle_hold`) so a renamed
  or missing script can no longer leave the tool unable to pick anything up;
* **it is drawn only while a gravity gun is equipped.**  E lets the player
  carry a prop with any weapon, and the tether used to be drawn for that
  carry too, so a pistol fired at a crate appeared to shoot a *blue beam*
  because the pistol was not the thing being drawn;
* the gun ships under two addon spellings, `physcannon`
  (`models/weapon/v_physcannon`) and `physgun`
  (`models/weapon/v_physgun`).  Both carry `weapon_type = tool` /
  `gravity_gun = 1`, and `physgun.txt` sets `script = physcannon`, so the
  behaviour and the tether are shared.  Keying either on one hard-coded
  `reg_name` silently disabled the other (no script loaded -> no grab, no
  hold distance, no beam), which is what "the physgun cannot work and draws no
  beam" was;
* firing with **empty hands** starts a force-push beam from the muzzle to the
  pushed prop (or 3 m down the crosshair).  Its pulse covers the whole fire
  interval, so holding the trigger does not blink the beam on and off.

The other half of that bug was the pick-up range: `try_pickup_weapon` measured
the distance from the **eye** to the weapon's **centre**.  A dropped weapon
rests about 1.4 m below the eye, so it was always outside the 1.5 m radius even
when it lay at the player's feet; E fell through to the physics-gun grab, which
picked the weapon up as a prop (and drew the tether).  Both the weapon and item
pick-ups now measure the nearest point of the collision box from the player's
body centre, so E equips the weapon instead of "carrying" it.

Verify with `python tools/verify_physgun.py` and
`python tools/verify_pickup_range.py`.

## Grenades and rockets

Two explosive weapons live entirely in the addon data plus one small script:

* **`grenade`** (`addons/weapon/grenade.txt` + `addons/ammo/grenade.txt`) is
  thrown with a 2.4 m/s upward kick, falls under gravity, bounces off walls and
  the floor (`bounce = 0.42`, tangential friction included) and detonates after
  a 2.8 s fuse for 120 damage in a 5.5 m radius.  The projectile model is the
  HL1 `grenade.mdl` (a v10 MDL with embedded palette textures); the held hand
  is `p_grenade.mdl`, because `v_grenade.mdl` is authored as a full arm and
  filled the screen when normalised.  The magazine holds one grenade;
  `grenade.py` readies the next one automatically.
* **`rpg`** (`addons/weapon/rpg.txt` + `addons/ammo/rocket.txt` + `rpg.py`)
  fires a straight-flying rocket (`gravity = 0`, `fuse = 0`) that detonates on
  the first surface it touches for 140 damage in a 5 m radius.  **Right mouse
  toggles laser guidance**: while it is on, every rocket in flight is rotated
  toward the crosshair at up to 3.2 rad/s, so a shot can be steered around a
  corner.  The model is `rpgrocket.mdl`; the view model is `v_rpg.mdl`.
* Both view models set `anim_model = weapon\v_smg1\v_smg1.mdl` and therefore
  play the SMG's authored fire/draw/reload arm animation instead of their own
  sparse clips (the grenade has no `fire` clip at all, the RPG only has
  base/muzzle/arm bones).  See **View-model animation sharing** above.
* Both share the generic projectile payload added to
  `addons/ammo/*.txt`: `explode_damage`, `explode_radius`, `fuse`, `bounce`,
  `launch_up`, `spin` and `visual = model`, so a new explosive needs no code.
* The blast reuses the existing `engine_pkg/world/explosive.py` damage pass
  (player, NPCs, destructible props, chain reactions and the screen flash) plus
  a new `ImpactEffects.blast` fireball/smoke/flash drawn with the existing
  additive particle shader - no extra render pass or shader.
* Projectiles are swept against objects, map brushes, terrain and water, and
  the *nearest* surface wins, so a fast rocket cannot tunnel through a wall.
* **Model projectiles are drawn opaque.**  They used to inherit the additive
  sprite blend state, which turned the grenade/rocket into a faint translucent
  ghost ("the grenade has no model").  If the model cannot be resolved the
  renderer falls back to a billboard so the projectile is never invisible.
* **A fused projectile always bounces; it never detonates on contact.**  A
  bullet that reached `_update_bullets` without its payload fields recovers
  `fuse`/`bounce` from the ammo addon, so a grenade can no longer behave like
  an impact rocket and explode on the first frame.

Verify with `python tools/verify_explosives.py`.

## Water splashes

Hitting water with a weapon, jumping in and swimming all produce a splash:

* **weapon hits** - the bullet sweep asks `water_surface_hit` whether the
  segment enters a water volume (top or side entry); a hit stops the round and
  spawns a 3D crown + droplets + an expanding surface ring.  A melee swing that
  meets the waterline splashes the same way;
* **jumping in** - on the frame `in_water` turns true the entry splash is scaled
  by the downward speed;
* **swimming** - a throttled ripple trail (0.26 s while moving, 0.62 s while
  treading);
* water is still ignored by damage traces, so a round that hits the surface
  stops there while NPCs/props behind it are not hit through the water.

The splash is **real geometry, not flat billboards**: `engine_pkg/render/splash_mesh.py`
generates a crown of 12 tapered 3D spikes around a centre column and an
octahedral droplet.  `ImpactEffects` draws them through the tiny `splash`
program with the same sun direction as the scene, so a moving camera sees a
silhouette and depth instead of a card that turns with the view.  The flat
surface ring stays: a ripple *is* a flat feature of the water plane.

Cost is bounded by design: at most 160 droplets/spray models, 28 crowns and 16
rings live at once (separate from the 320 spark particles), splashes farther
than 45 m from the camera are skipped, and a busy field updates in ~0.04 ms.
`engine_pkg/world/impact.py` owns the effect (`ImpactEffects.splash`).

Verify with `python tools/verify_water_splash.py`.

## Wave patterns on the ground (caustics)

The water surface has always had travelling waves; the bed under it was a flat
pane.  The scene fragment shader now evaluates the same wave field for every
fragment below a water volume and adds the focused light to the surface:

* volumes are passed as `water_a`/`water_b` uniform arrays (centre x/z, surface
  y, half extents), up to 8 at once, by `WaterManager.apply_caustics()`;
* the pattern is a sharp diamond net (`abs(sin * sin)`), not smooth sine blobs,
  and moves with the game clock;
* it only affects upward-facing surfaces, fades within 0.2 m of the waterline
  and disappears below 7 m, so a bridge underside is not lit through a lake;
* shallow water is made translucent enough to see the bed (deep water keeps its
  murky colour), and the body keeps a green-grey tint so a pool never reads as
  dry floor;
* cost is one loop over at most 8 boxes plus a few sin/pow per shaded pixel;
  no extra pass, texture or render target.

`rendering.water_caustics` is not a switch: the pass is free when no water is in
the map (`water_count == 0`).  Verify with
`python tools/verify_engine_features.py` (screenshot in
`tools/ui_shots/wave_caustics.png`).

## View-model animation sharing (grenade / RPG)

A weapon addon can borrow another view model's SMD clips:

```
anim_model = weapon\v_smg1\v_smg1.mdl
```

`grenade` and `rpg` set it, so their arms play the SMG's complete
`fire` / `draw` / `reload` / `idle` set instead of the sparse native clips
(grenade has no `fire` clip at all).  Bones are matched by name; `v_rpg.mdl`
only has base/muzzle/arm, so the SMG left-hand track is retargeted onto the
launcher's root (`smd_anim.build_skin_anim_bq(name_map=...)`).  The skin skinning
matrices are rebuilt against the *target* skeleton, so the launcher mesh stays
the launcher and only the animation is shared.

Verify with `python tools/verify_engine_features.py`.


## Map menu (chapters and paging)

The New Game dialog reads the campaign order from `cfg/chapter<N>.cfg`
(`map <mapname>` per line, `#` comments allowed).  Files are sorted numerically
(`chapter2` before `chapter10`), maps that no chapter mentions are appended
afterwards, and the number of pages is generated from the total number of
entries.  Every page number on the bottom bar is a real button, so page 3, 4,
5, 6 ... can be jumped to directly; the bar shows `1 ... 4 5 6 ... 12` when
there are more pages than slots.  Prev/next arrows remain at both ends.

Verify with `python tools/verify_new_game_pages.py`.

## Console: `map` and `maps`

`map <name>` loads any map in `map/` (`.amap`, `.jsonmap`, `.json` or `.txt`)
from the console — the same command Hammer's **F5 Run Map** tells you to type:

```
map devmap
map testlab
maps              # list every map + the current one
```

The name is matched exactly first, then case-insensitively.  Loading from the
pause menu releases the old pause snapshot/GL context first; the map's
cinematic intro (below) plays once the world is ready.  Tab completion in the
console fills map names (`map dev<TAB>` -> `map devmap`).

## Cinematic intro (camera, title, subtitles)

Hammer authors an optional opening cinematic via **Map > Cinematic Intro...**:
a list of camera keys (time, camera position, look-at target), a main title, a
subtitle line and timed subtitle cues.  It is stored in the `.hmmap` and
compiled as a top-level `cinematic` block in the `.amap`; the engine plays it
right after the map finishes loading.

### Simple camera sequencing

You do not have to type times or coordinates.  The dialog now has a **Simple
shots** table: put the 3D view where you want the camera, press *Add current 3D
view as shot*, type the seconds and an optional subtitle, repeat.  Each shot
starts where the previous one ended.  The same thing is available live from the
console:

```
cam add 4            # record the current view as the next shot (4 s)
cam text Water       # subtitle for the last shot
cam add 3            # next shot
cam list             # show the auto-computed timeline
cam play             # preview it
cam save promo       # map/promo.cam.json
cam load promo
```

The data format is just:

```json
"cinematic": {
  "enabled": true,
  "title": "ANIMA ENGINE",
  "subtitle": "a world built in Hammer",
  "subtitle_side": "bottom",
  "shots": [
    {"pos": [0, 7, 30], "look": [0, 1.4, 0], "seconds": 5,
     "text": "Water, physics, weapons."},
    {"pos": [0, 2.6, 15], "look": [0, 1, -2], "seconds": 5}
  ]
}
```

`normalize_cinematic` turns `shots` into the precise `keys`/`subtitles` the
player uses, so the old advanced table still works and the engine only ever
plays one timeline.

### Title and subtitle layout

* **Why the editor title could be missing:** starting a map from the main menu
  while the menu's background diorama was loaded inherited the "menu load" flag
  and skipped the intro entirely.  `load_map(map_name, background=False)` now
  takes an explicit parameter, so a real map started from the menu plays its
  intro and only the diorama load is silent.
* A cinematic with an empty `title` now falls back to the map's `meta.name`
  (the name in Hammer's map properties), instead of showing no title card.
* The Hammer 3D viewport caption also shows the map name and cinematic title,
  so the author can see what will play without launching the map.
* Camera keys authored past `duration` are dropped before the final hold (they
  used to teleport the camera at the end).
* `subtitle_side` is `bottom` (the cinema-standard position, default) or
  `right` (a vertical band on the right edge, useful when the action is in the
  lower third).  The Hammer dialog has the same choice.
* `title_time` is clamped inside the timeline, so a 2-second intro still gets a
  readable title instead of a title whose fade window collapsed.

* Camera position and look target are `smoothstep`-blended between keys, so a
  two-key dolly is already smooth; the last shot holds until `duration`.
* The main title fades in over its first 18 % and out over its last 28 %
  (`title_time` controls when it appears), with a soft dark band behind it so
  it stays readable over bright sky.
* Subtitles fade in/out over 0.25 s at each cue boundary; `letterbox` draws the
  cinematic bars.  A map with no `keys` gets a gentle default dolly around its
  spawn.
* **SPACE / ENTER / ESC / click** skips the intro; the player is frozen (the
  intro owns the camera) and normal control resumes at the end.
* `intro` in the console replays the current map's cinematic without a reload.

Verify with `python tools/verify_cinematic.py` and
`python tools/verify_engine_features.py`.

## Engine demo map

`map/engine_demo.amap` (built by `python tools/make_engine_demo.py`, also saved
as editable `map/engine_demo.hmmap`) is the promo / showcase level:

* opens with a six-shot camera sequence and a title card;
* a sea-water pool showing surface waves, **ground caustics** and 3D splashes;
* a crate tower, loose barrels, all seven weapons on a deck, two NPCs, a
  drivable buggy, a small physics court and a vehicle ramp;
* listed as a chapter in the New Game menu (`cfg/chapter8.cfg`), so it can be
  launched without the console.

Verify with `python tools/verify_engine_features.py`.

## Options dialog (Video + Keyboard)

`engine_pkg/ui/options_mp.py` is the HL2-style 560x430 Options window.  The
**Video** page mirrors the reference: Resolution, Display mode, Aspect Ratio,
*Adjust brightness levels...*, HUD Aspect Ratio, *Advanced...*, High Dynamic
Range and the 3rd-party credits link, with OK / Cancel / Apply.  The
**Keyboard** page mirrors the second reference: a MOVEMENT / COMBAT table with
Use Defaults / Advanced / Edit key / Clear Key.

* Bindings live in `config.json` under `input.bindings`
  (`engine_pkg/core/keybinds.py`) and the player controller reads them, so a
  remap really moves the player.
* Resolution / display mode / HDR are written to config and apply on the next
  launch (the renderer and many modules cache the window size); brightness,
  quality preset, shadow softness and bloom apply live.
* The 3rd-party credits button lists SDL/Pygame, moderngl, NumPy/Numba, PyGLM,
  Assimp, PyQt5 and Valve's Source/GoldSrc formats.

Verify with `python tools/verify_engine_features.py` (screenshots in
`tools/ui_shots/options_video.png` and `options_keyboard.png`).


## Quality presets and adaptive performance

`rendering.quality` is `auto` (detect the GPU once at startup) or `low` /
`medium` / `high` / `ultra`; `custom` keeps the raw config values.  A preset
only changes **cost** knobs — shadow-map size (`rendering.shadow_map_size`),
shadow softness, bloom, grain, vignette and MSAA (`window.msaa_samples`) — while
the PBR shading, sun rig and ACES tone curve stay identical, so the image keeps
its look at every tier.

While playing, `AdaptiveQuality` watches a rolling 1.5 s frame window.  If the
frame rate falls below `rendering.adaptive_min_fps` (48), it steps down one of
three levels: soft-shadow penumbra 9 -> 7.7 -> 6.3 -> 5 texels, bloom and grain
trimmed proportionally, and the water-reflection cadence stretched.  Nothing is
ever switched off completely, and three healthy windows above
`rendering.target_fps + 12` walk it back up.  The shadow map and MSAA level are
chosen before the pipeline allocates, so changing tier takes a map reload.

Console: `quality` prints the tier, detected GPU class, adaptive level and
measured FPS; `quality low|medium|high|ultra|auto` forces a tier at runtime.
`ANIMA_QUALITY=low python "Anima Engine.py"` overrides the config for
benchmarks.

## Performance pass (loading, beams, piles, pixelation)

* **Resolution and anti-aliasing.** The default window is now **1280x720**
  instead of 800x600 (config `window.width/height`), which removes most of the
  "pixelated" look. MSAA is never disabled by a preset any more — low/medium
  keep 2x, high/ultra use 4x — and every map texture already gets mipmaps,
  trilinear minification and 16x anisotropic filtering (`resources/texture.py`).
* **Map loading.** The forced 5 s "minimum loading time" is gone (0.75 s
  floor, 0.05 s READY hold). Reloading a map from the console now **reuses the
  GL context, compiled shaders, GPU buffers and texture cache** when the sky's
  cubemap/panorama is unchanged (`_sky_gpu_key`); only the world is rebuilt.
  Loading a devmap twice drops from ~18 s to ~5.5 s in the benchmark rig, and
  the second load no longer recompiles shaders. The texture preload screen also
  repaints in batches instead of once per texture.
* **Beams.** The gravity-gun tether's material and shader are built during map
  warm-up (with the muzzle flash and view-model depth clear), so the first grab
  / first shot no longer hitches. A failed VMT or shader used to be retried
  *every frame* while the beam was on; that retry now fails once and disables
  the effect quietly.
* **Collapsing props.** The sun shadow map is only re-baked at 10 Hz checks,
  at most every 0.55 s, and **while more than three bodies are awake** the bake
  is deferred (up to 2.5 s) until the pile settles. Previously every crate that
  moved triggered a full shadow re-render several times a second — the visible
  "stutter" when a stack collapsed.
* **Update loop.** The simulation catch-up is capped at 3 substeps and 40 ms of
  frame time, and leftover accumulator debt is dropped. The old 50 ms / 5-step
  cap let one slow frame queue up to five full NPC-AI + physics passes, which is
  what turned a single hitch into a freeze.



