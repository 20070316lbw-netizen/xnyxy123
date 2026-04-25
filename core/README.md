# core/ — 数据模型与成本计算（基石层）

## 内容

| 文件 | 作用 |
|---|---|
| `problem.py` | 全局常量（出发时间、成本参数、速度分段、绿色区半径、政策开关） + 数据类（Customer, VehicleType, Problem） |
| `data_loader.py` | 读 4 个 xlsx → 组装 Problem 对象 |
| `solution.py` | Route / Solution 数据结构 + `evaluate_solution` / `solution_summary` |
| `cost.py` | ★ **最核心**：路径成本计算（速度时变 + 载重能耗 + 时间窗惩罚 + 政策罚） |

## 依赖关系

```
problem.py     ← 无依赖（其它模块都依赖它）
data_loader.py → problem
cost.py        → problem
solution.py    → problem, cost
```

## 改参数的入口

**几乎所有可调参数都在 `problem.py`**：
- 出发时间 / 工作时间上限
- 速度分段 (8 段)
- 油价 / 电价 / 碳价 / 启动成本 / 时间窗惩罚
- 载重系数 (燃油 0.40 / EV 0.35)
- 绿色区中心与半径
- 政策时段 `GREEN_BAN_START` / `GREEN_BAN_END` 与软罚项 `POLICY_PENALTY_PER_VIOLATION`

## 政策开关 `Problem.policy_mode`

| 模式 | 用途 | 行为 |
|---|---|---|
| `"off"` | 问题1 | 完全忽略绿色区限行 |
| `"soft"` | 问题2 ALNS 搜索阶段 | 每次违规 +1e6 元罚项；路径仍可行 |
| `"hard"` | 问题2 验收 / 提交 | 违规 → `RouteCost.feasible = False` |

**怎么用**：
```python
prob = load_problem()
prob.policy_mode = "soft"  # ALNS 搜索时
# ... run ALNS ...
prob.policy_mode = "hard"  # 验收时, 检查 feasible
```

## `RouteCost` 的字段

`evaluate_route` 返回的 `RouteCost` 对象包含完整成本分解：
- `start_cost / energy_cost / carbon_cost / early_cost / late_cost / policy_cost`
- `total_distance / total_time / energy_used (L 或 kWh) / carbon_kg`
- `feasible / reason`
- `policy_violations`：本路径违反绿色区限行的客户次数 (问题2)

## 改问题2 时要动什么

只要把 `prob.policy_mode` 切到 `"soft"` 或 `"hard"`。`evaluate_route` 已经实现了禁行时段判断（`_overlaps_ban`：服务区间与 [8,16] 是否重叠），燃油车进绿色区会在 hard 模式下置 `feasible=False`、soft 模式下加 1e6 罚项。
