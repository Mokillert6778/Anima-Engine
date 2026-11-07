# 武器系统速查（addons/weapon/*.txt + *.py）

一个武器 = **一个 .txt（数据）** + 可选 **一个同名 .py（行为）**。
同名规则：`.py` 的文件名必须等于 .txt 里的 `reg_name`。

## 1. 数据：addons/weapon/xxx.txt

```ini
reg_name = physcannon
world_model = weapon\v_physcannon\v_physcannon.mdl
ammo = 手枪子弹          # 对应 addons/ammo/ 里某个 addon 的 reg_name
fire_rate = 4.0          # 每秒射速
magazine = 12
reserve_ammo = 96
reload_duration = 1.15   # 换弹时长兜底（秒）。模型自带 reload 动画时用动画时长
fire_sound =             # 声音文件（sound/ 目录里）

# ---- 第一人称外观 ----
scale = 1.0              # 相对默认大小（默认最大边 = 0.52 m）
offset = 0.17,-0.17,-0.62  # 相机空间位置：+X 右 / +Y 上 / -Z 前（米）
rotation = 0,0,0         # 在自动朝向之上的微调（度）
recoil = 1.0             # 开火后坐强度
alpha_test = 0           # 1 = 贴图 alpha 当镂空用（Source 武器贴图通常是高光，别开）
anim_model =             # 可选：借用另一个视图模型的 SMD 动画
                         # 例如 weapon\v_smg1\v_smg1.mdl，骨骼按名字匹配

# ---- 按键 ----
attack1_key = lmb        # 左键 / 也可以 mmb rmb 0..4
attack2_key = rmb        # 右键
alt_fire_cooldown = 0.45 # 右键冷却（秒）
alt_fire_sound =

script =                 # 可选，留空表示用同名 .py
```

右键**不会**自动做任何事：它是留给脚本的（见下）。

## 1.1 近战武器

`melee = 1` 时武器没有弹丸、也不消耗弹药：每次按下攻击键会在相机前方
`melee_range` 米、`melee_arc` 半角的锥形范围内命中最近的一个模型/NPC/玩家，
造成 `melee_damage`。自带 `crowbar` 和 `knife` 两个示例。

```ini
melee = 1
melee_range = 2.2
melee_damage = 45
melee_arc = 0.40          # dot(视线, 目标方向) 下限：0.4≈66° 半角
melee_impulse = 0.06      # 命中物理道具时给的推力
melee_hit_sound =
fire_rate = 1.6           # 挥动频率（次/秒）
fire_sound =              # 挥动声
magazine = 0
reserve_ammo = 0
```

近战命中远端玩家/复制道具时会走网络伤害消息（PVP / object_damaged），
和枪械一致。`world_model` 仍然用于第一人称外观、掉落和拾取。

## 1.2 第一人称朝向（自动）

引擎按模型尺寸把**长轴自动对准镜头前方**，`rotation` 只在其上微调：

| 最长轴 | 处理 | 例子 |
|---|---|---|
| Z | yaw 180（枪管 / 刀尖朝前） | v_smg1、v_shotgun、v_crowbar |
| X | yaw 90（横着摆的模型） | 少数老式模型 |
| Y | pitch −90（竖着摆的世界道具放倒，上端朝前） | knife.obj、crowbar.obj |

X / Y 判定的容差是 10%：撬棍是斜着的（Y 0.500 ≈ Z 0.492），
如果只按“谁最长”比大小就会漏判、让钩子朝向玩家。

## 1.3 原生 SMD 动画

模型目录（以及 `<model>_anims/`、`<model>/` 子目录）里的 `.smd` 会**按文件名**
自动匹配成第一人称动画：

| 文件名包含 | 用途 |
|---|---|
| `fire` | 开火（优先 `fire01`） |
| `pump` | 开火之后的拉泵/上膛（霰弹枪，接着 `fire` 播） |
| `draw` / `reload` / `dryfire` / `altfire` | 拿出 / 换弹 / 空枪 / 右键 |
| `idle01` / `idle` | 待机循环 |
| `hitcenter*` / `misscenter*` / `hitkill*` | 近战命中 / 挥空 / 击杀 |

模型自带某个动画时，引擎**不再叠加**程序化的后坐、抬枪或换弹下沉
（两套动画同时播会互相打架，看起来就像“只有程序化动画”）。
换弹时长自动取原生 `reload` 动画的长度。

## 2. 行为：addons/weapon/physcannon.py

只要定义你需要的那几个函数，引擎按事件调用：

| 函数 | 什么时候调用 |
|---|---|
| `on_equip(app, wdef)` | 拿出 / 捡起这把武器 |
| `on_fire(app, wdef)` | 成功打出一发之后 |
| `on_alt_fire(app, wdef)` | 右键按下的那一次（不会连发） |
| `on_reload(app, wdef)` | 按 R 之后 |
| `on_empty(app, wdef)` | 没子弹还扣扳机 |
| `on_update(app, wdef, delta)` | 拿着它时每帧 |
| `on_hit(app, wdef, target, damage)` | 我们打出的弹丸命中东西 |

常用 API：

```python
app.player          # dict: player_pos / foot_y / yaw / pitch / hp ...
app.cam_pos         # glm.vec3 眼睛位置
app.look_dir        # glm.vec3 视线方向
app.weapon          # magazine / reserve / fire_cooldown / name
app.bullets         # 场上弹丸 list（dict: pos vel damage life ammo weapon）
app.spawn_bullet(pos, vel, damage=60, ammo="plasma")   # 按流程发射
app.view_anim.on_fire(2.0)     # 额外后坐
app.view_anim.flash = 1.0      # 立刻枪口闪光
app.play_sound("boom.wav")
app.scene.objects              # 场景实体
```

脚本里抛异常只会打日志，不会崩游戏。

## 3. 射出去的东西长什么样：addons/ammo/xxx.txt

```ini
reg_name = 手枪子弹
speed = 45            # 米/秒
damage = 12
life = 3.0
gravity = 0           # 0 = 直线；1 ≈ 9.8 m/s² 下坠
hit_radius = 0.1      # 命中判定加粗

visual = sprite       # sprite | tracer | model | none
size = 0.09           # sprite 直径 / tracer 宽度 / model 长度（米）
color = 1.0, 0.85, 0.45
alpha = 1.0
texture =             # 留空用引擎内置光晕；也可以指向一张 png
model =               # visual=model 时用哪个模型（mdl/obj/gltf）
```

- `sprite`：加色发光圆片，透视缩放，最省事
- `tracer`：沿速度方向拉长的光条，激光/曳光弹
- `model`：真的把一个模型当子弹扔出去，能旋转朝向
- `none`：看不见（hitscan）

### 3.1 爆炸物（手雷 / 火箭弹）

再加下面这些字段，就是一颗会炸的弹丸（引擎已有的通用逻辑，
`addons/ammo/grenade.txt`、`rocket.txt` 是现成例子）：

```ini
explode_damage = 120    # 爆炸伤害（0 = 不爆炸）
explode_radius = 5.5    # 爆炸半径（米）
fuse = 2.8              # 引信秒数；0 = 一碰就炸
bounce = 0.42           # 引信期间撞墙/地面的反弹系数（0 = 不弹）
launch_up = 2.4         # 额外向上初速，扔手雷时让它走抛物线
spin = 9.0              # visual=model 时模型自转（弧度/秒），手雷会翻滚
```

弹丸会沿本帧的扫掠线段同时对模型、地图刷子、地形和水面求最近命中；
引信期间撞到东西会反弹并继续烧引信，引信烧完或在 `fuse = 0` 时撞到
第一个表面，就在命中点走公共爆炸通道（玩家 / NPC / 可破坏道具 / 连锁）。

### 3.2 两个 reg_name 共用一个行为

`weapon` 的 `script` 字段可以指到别的脚本文件名（不带 `.py`），
`physgun.txt` 里的 `script = physcannon` 就是让两个重力枪型号共用一份行为。
不写时按 `reg_name` 自动找同名 `.py`。

工具武器（重力枪）用 `weapon_type = tool`（或 `gravity_gun = 1`）标记：
引擎按它决定“开火不发射弹丸”和“画不画牵引光束”，而不是写死某个名字。

## 4. 加一个新武器的步骤

1. 复制一份 `addons/weapon/xxx.txt`，改 `reg_name` / `world_model` / `ammo`。
2. （可选）写 `addons/weapon/<reg_name>.py` 定义右键等行为。
3. （可选）在 `addons/ammo/` 里定义新弹丸的外观。
4. 游戏里控制台：`weapons <名字>` 试放，`give <reg_name>` 拿在手上。

现成的爆炸物示例：`grenade`（弹跳 + 引信）和 `rpg`（碰撞爆炸 + 激光制导），
数据在 `grenade.txt` / `rpg.txt` 与 `../ammo/grenade.txt` / `../ammo/rocket.txt`，
行为脚本在 `grenade.py` / `rpg.py`。
