# alns/ — ALNS 算法实现（第四层）

## 内容

| 文件 | 作用 |
|---|---|
| `operators.py` | 基础算子：4 个破坏 + 2 个修复 |
| `main.py` | ★ **标准 ALNS 主循环**：退火 + 自适应权重更新 |
| `operators_v2.py` | 精细化算子：2-opt / relocate / merge_routes |
| `v2.py` | 增强版主循环：周期性局部搜索 + 无改进重启 |

## 推荐使用

**默认使用 `main.py` + `operators.py`**（实测效果和 v2 差不多，但更快更稳）。

```python
from alns.main import run_alns, ALNSConfig

cfg = ALNSConfig(
    max_iterations=1200,
    initial_temp=5000,
    cooling_rate=0.997,
    destroy_min_frac=0.08,
    destroy_max_frac=0.25,
    segment_size=100,
)
best_sol, history = run_alns(prob, init_sol, cfg)
```

## 算子库

### 破坏算子（destroy operators）

| 名字 | 策略 | 适用场景 |
|---|---|---|
| `random_removal` | 随机抽 k 个客户访问 | 基础探索 |
| `worst_removal` | 抽"抽走后降成本最多"的 k 个 | 攻坚局部最优 |
| `shaw_removal` | 种子 + 相似客户（距离+时间窗近） | 针对簇状结构 |
| `route_removal` | 整条路径抽走 | 大幅扰动 |

### 修复算子（repair operators）

| 名字 | 策略 |
|---|---|
| `greedy_insertion` | 每个客户找插入成本最低的位置 |
| `random_insertion` | 随机打乱顺序再插入 |

两者都支持 SDVRP：若整装插入失败会拆分。

## ALNS 主循环流程

```
for iter in range(max_iters):
    1. 按权重选 destroy + repair 算子
    2. 破坏 candidate（抽走 5%~20% 客户）
    3. 修复 candidate（把客户重新插回路径）
    4. 计算候选分数 = 成本 + 不可行惩罚(2000/条)
    5. 接受判据:
       - 完全可行且成本 < best → 更新 best（+33 分）
       - 分数 < current → 接受（+13 分）
       - SA 概率接受（+9 分）
       - 拒绝（+0 分）
    6. 每 100 次迭代: 根据分数更新算子权重
    7. 温度衰减: T *= 0.997
```

## 关键参数解读

- `initial_temp = 5000`：初始接受 ~5000 元劣解的概率约 37%
- `cooling_rate = 0.997`：约 230 次迭代后温度减半
- `segment_size = 100`：每 100 次迭代重算权重（既不过于频繁也不过于稀疏）
- `destroy_min_frac / max_frac`：破坏 5%-25% 的客户

## 当前性能

| 指标 | 值 |
|---|---|
| 最终成本 | **107113 元** |
| 单起点时间 | ~40s (1200 iters) |
| 4 起点时间 | ~160s |
| 初始→最终改进 | 12.3% |

## 为什么有 v2？

`v2` 加了两个东西：
- 周期性局部搜索（每 5 iters 做 2-opt）
- 无改进重启（500 iters 没 best 就把 current 重置为 best + 升温）

实测结果：和 v1 差不多。原因是 108k 附近的局部最优盆地**结构性地**难以跳出（大客户占 80% 车数是固定的）。

如果想继续压榨，可以试 v2，但 ROI 比较低。
