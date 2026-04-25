# construct/ — 初始解构造（第三层）

## 内容

| 文件 | 作用 |
|---|---|
| `spiral_init.py` | 阿基米德螺线排序（原创思想） + 基于螺旋的贪心构造 |
| `tiered_init.py` | ★ **Q1 采用**：分层构造（大/中/小客户分别策略） |
| `tiered_init_q2.py` | ★ **Q2 采用**：政策感知构造（绿色区客户优先 EV） |
| `solution_utils.py` | 解的修复工具：内部按时间窗排序、尝试合并路径 |

## 为什么分层？

一个关键数据观察：

| 客户类别 | 数量 | 必需车数 | 占比 |
|---|---|---|---|
| 大客户（需求 > 3000kg） | 36 | 96 | 80% |
| 中客户（容量 50%~100%） | 18 | 18 | 15% |
| 小客户（容量 < 50%） | 34 | 6 | 5% |

**96% 的"用车数"被结构性决定**，ALNS 真正能优化的只有小客户部分。所以分层构造让每一层用最合适的策略：

- **大客户**：SDVRP 拆分，每辆车满载跑一趟
- **中客户**：独占一辆合适的车
- **小客户**：按螺旋序贪心合并 + 时间窗检查

## Q2 的政策感知构造 (`tiered_init_q2`)

问题2 多了一条约束：8:00–16:00 禁止燃油车进入绿色区。我们的构造策略是**从源头消灭违规**：

```
Step 1. 把绿色区客户全部用 EV 独占 (类似 medium 逻辑, 一客户一车)
Step 2. 非绿色区大/中/小客户沿用 Q1 的分层构造, 在剩余车池中选车
```

绿色区有订单客户约 12 个，EV 车队 25 辆 (10 + 15) 完全够用。即使 EV 用完，ALNS 阶段还能把非绿色区小客户搭在燃油车上腾出 EV 给绿色区，所以这个构造非常稳健。

实测：用 4 个起点跑 1200 iter，零违规可行解的总成本约 **108k**，比 Q1 仅高 ~480 元。

## 关键函数签名

```python
# 螺旋序（被 tiered_init / tiered_init_q2 复用）
spiral_order(prob, clockwise=True, outward=True) → List[cid]

# Q1 构造器
tiered_construct(prob, clockwise=True, outward=True) → Solution

# Q2 政策感知构造器（要求 prob.policy_mode != "off"）
tiered_construct_q2(prob, clockwise=True, outward=True) → Solution

# 便宜的后处理
sort_routes_by_tw(prob, sol) → Solution   # 路径内部按时间窗排序
```

## 使用示例

```python
# Q1 流程
from construct.tiered_init import tiered_construct
from construct.solution_utils import sort_routes_by_tw

init = tiered_construct(prob, clockwise=True, outward=True)
init = sort_routes_by_tw(prob, init)
# init 是完全可行的 Q1 初始解

# Q2 流程
from construct.tiered_init_q2 import tiered_construct_q2
prob.policy_mode = "soft"   # ALNS 搜索时用 soft
init = tiered_construct_q2(prob, clockwise=True, outward=True)
init = sort_routes_by_tw(prob, init)
```
