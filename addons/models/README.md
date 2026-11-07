# 物理道具（addons/models/*.txt）

一个模型 addon 就是一个 .txt。`model_type = physics` 会把它变成刚体，其余字段
决定它有多重、怎么碰撞、怎么落地。

```ini
reg_name = wood_crate001aout
world_model = props\wood_crate001aout.obj
world_texture = props\WoodCrates01aout.png

scale = 0.7               # 世界尺寸（米）

# ---- 物理 ----
model_type = physics      # physics = 刚体；static = 固定不动
mass = 1                  # 质量（kg）：影响推进去、抛出去、堆叠时的反应
gravity = 1               # 重力倍率：0 = 悬浮，1 = 正常，2 = 两倍

# 碰撞形状（可选，默认 auto）
#   auto   从模型网格自动拟合：用若干"有朝向的盒子"拼出真实外形，
#          圆形物体的顶点云足够圆时会自动变成球体
#   cube   强制一个立方体
#   sphere 强制一个球体
shape = auto

# 材质（可选，不写就用引擎默认）
friction = 0.75           # 摩擦系数，越大越不容易滑动
restitution = 0.18        # 弹性，0 = 不弹，1 = 完全弹

# ---- 拾取 ----
can_pickup = yes
can_kick = yes

# ---- 破坏 / 掉落 ----
destructible = yes        # 能被子弹打碎
hp = 50                   # 血量（子弹伤害累加扣减）
drop_item = apple         # 打碎后掉什么（见 addons/items/）
drop_count = 1            # 每个条目掉几个（会散开成多个独立掉落物）
drop_chance = 1.0         # 0..1，按概率掉
drop_weapon =             # 打碎后掉出的武器，如 smg1（可写多个：pistol x2）
spawn_npc =               # 打碎后放出的 NPC，如 classic（木箱里藏人）
on_hit_sound =            # 受击音效（可选）
on_destroy_sound =        # 破碎音效（可选）
behavior =                # 留空=只碎裂；explode=炸开；drop_item=掉东西

# ---- 脚本（可选）----
# 默认加载 addons/models/<reg_name>.py，也可以显式指定文件：
script = crate.py
```

`drop_item` / `drop_weapon` / `spawn_npc` 都支持逗号分隔和数量：

```ini
drop_item   = apple x3, ammo_small x2   # 3 个苹果 + 2 份弹药
drop_count  = 3                          # 没写数量的条目各掉 3 个
drop_weapon = smg1, pistol               # 两把武器
spawn_npc   = classic                    # 掉出一个 NPC
```

每个掉落物都会落在木箱周围的一圈位置上（不会全部叠在一个点），
武器同样是**有重力的刚体**：掉出来会落到地面，而不是浮在半空。

## 支持的模型格式

`world_model` 可以是下面任意一种（MDL 也支持，只是它有自己的打包加载器）：

| 格式 | 说明 |
|---|---|
| `.obj` | 手写解析，支持 `.mtl` 贴图 |
| `.gltf` / `.glb` | glTF 2.0，含骨骼/动画时走蒙皮路径 |
| `.smd` | Source 网格（reference/静态导出；纯动画 SMD 会被忽略） |
| `.fbx` `.dae` `.3ds` `.x` | 通过 assimp 读取，自动烘焙节点变换 |
| `.stl` `.ply` `.off` `.3mf` | 通过 trimesh 读取（assimp 作为后备） |
| `.mdl` | Source 模型（专用加载器） |

扩展名大小写不敏感。`python tools/verify_model_formats.py` 会为除 MDL 外的
每种格式生成测试模型，逐个加载、归一化并实际生成到世界里。

## 破坏、掉落与脚本

子弹命中会走 `ModelScript`（`engine_pkg/script/model_script.py`）：

1. 扣血并调用脚本的 `on_hit`；
2. 血量 ≤ 0 时调用 `on_destroy`（破碎音效、`behavior = explode` 的爆炸），
3. 再按 `drop_item` 生成掉落物，最后把物体移出世界。

掉落的名字必须能在 `addons/items/` 里找到（`reg_name` 对上即可）。目前自带：

| 名字 | 模型 | 效果 |
|---|---|---|
| `apple` | `props/food_apple/...gltf` | `effect = heal`，+15 hp |
| `ammo_small` | `props/boxmrounds/...mdl` | `effect = ammo`，+30 备弹 |

一个 `drop_item` 可以写多个：`drop_item = apple, ammo_small x2`。

### 模型脚本

在 addons 的 .txt 旁边放一个同名 `.py` 就会被自动加载（也可用 `script =` 指定）：

`addons/models/wood_crate001aout.py`

```python
def on_spawn(app, obj):                   # 物体出现
    obj["hp"] = 120

def on_hit(app, obj, damage, source):     # 受击（销毁前）
    if obj["hp"] < 30:
        obj["vel"].y += 0.1               # 被打得往后跳一下

def on_destroy(app, obj, source):         # 销毁前（掉落之前）
    app.model_script.spawn_item("apple", obj["pos"])

def on_pickup(app, obj, player):          # 被捡起
    pass

def on_update(app, obj, delta):           # 每帧（只有定义了才每帧调用）
    pass
```

脚本只是"补充"：不写脚本，addon 的 `destructible` / `drop_item` 一样生效。
脚本报错只会写日志，不会影响物体本身。NPC 的脚本放在 `addons/npc/<reg_name>.py`，
用同一套钩子；NPC 死亡默认掉落手上的武器（`drop_weapon = yes`）以及 `drop_item`。

控制台 `scripts` 可以查看已加载的脚本，`scripts reload` 清缓存（改完脚本不用重启）。

## 碰撞形状是怎么来的

引擎把模型的顶点（已归一化到世界尺寸、居中）喂给
`engine_pkg/world/shapes.py::fit_shape`：

1. 先判断是不是球（顶点数够多、半径方差小、协方差各向同性）→ 是就用球；
2. 否则拟合一个**有朝向的包围盒**（世界轴 + 主成分轴 + 小角度微调，取体积最小者）；
3. 如果盒子比点云松太多，就沿最长轴把点云切开递归分解（最多 8 块），
   得到一个**复合盒形**——L 形、细长道具因此能贴合真实外形；
4. 每个盒子外扩一点点，避免复合体之间出现能让物体钻过去的缝。

同一模型的所有实例共享一次拟合结果（按模型路径 + 缩放 + shape 缓存）。

物理里 `obj["collision"]` 是这份形状，`obj["half"]` 始终是它当前的**世界 AABB**
（玩家碰撞、视锥剔除、阴影、空间哈希都继续用 half，所以外部系统不用改）。

## 玩法上的表现

- 箱子倒向一边后，挡住的是它真实的斜边，不再是原来的正放方盒；
- 细长道具（铁锹、木板）只有本体挡路，不会在"空角"处凭空站立；
- 球体真的会滚，落地后会滚动而不是像方块一样跳；
- 箱子能稳稳叠起来，静止后进入睡眠（完全跳过物理），被踢/抓/砸到才醒。

## 调参建议

| 想要 | 改什么 |
|---|---|
| 更重、更难推 | `mass` 调大 |
| 悬浮 / 慢速下落 | `gravity` 调小 |
| 木头更涩 | `friction` 调大（0.9） |
| 金属更滑 | `friction` 调小（0.3） |
| 弹力球 | `restitution` 调大（0.8） |
| 复杂模型碰撞太慢 | `shape = cube` 用最省的盒形 |
