# 物品（addons/items/*.txt）

物品是"能被捡起来放进背包"的东西：地图里摆的物品、或者被打碎的道具掉出来的
东西都走这套定义。一个 .txt 就是一件物品。

```ini
reg_name = apple                    # 物品名（drop_item / item 命令都用它）
world_model = props/food_apple/food_apple_01_2k.gltf
world_texture =                     # 留空 = 用模型自带的材质

scale = 0.25                        # 世界里的显示尺寸

type = consumable                   # consumable / quest / ...
effect = heal                       # heal = 回血；ammo = 补备弹；留空 = 只能用不能吃
value = 15                          # 效果数值
stack_max = 5                       # 背包叠加上限

pickup_sound =                      # 可选音效名（sound/ 目录）
use_sound =
```

## 怎么出现在世界里

* **地图实体**：`item <名字>` 控制台命令，或地图里的 item 实体；
* **打碎东西掉落**：模型的 `drop_item = apple` 就用这里注册的名字；
* **脚本**：`app.model_script.spawn_item("apple", pos)`。

物品落地后是普通刚体（有碰撞形状、会滚动、会静止），走近按 E 捡起。
背包里按使用键消耗，`effect = heal` 回血、`effect = ammo` 补当前武器备弹。

一次可以掉多件：模型的 `drop_item = apple x3` 会生成 3 个独立物品，
`drop_count = 3` 则让每个没写数量的条目各掉 3 个。掉落物会在箱子周围
散开，不会叠在同一个点；脚本调用 `spawn_item(name, pos, count=5)` 还能让
**单个**掉落物一次给出多件。

## 自带的物品

| 名字 | 模型 | 效果 |
|---|---|---|
| `apple` | food_apple | +15 hp |
| `ammo_small` | boxmrounds | +30 备弹 |
