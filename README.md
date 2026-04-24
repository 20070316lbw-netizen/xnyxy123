#  代码

## 🚀 快速开始

```bash
cd vrp/
python3 main.py --iters 1200    # 约 3 分钟, 得到最优解 ~107k
```

## 📁 目录结构

```
vrp/
├── README.md          ← 本文档（总体说明）
├── main.py            ← 端到端入口脚本
│
├── core/              ← 数据模型 + 成本计算（基石）
│   ├── problem.py        参数常量 + 数据类
│   ├── data_loader.py    读 4 个 xlsx
│   ├── solution.py       Route / Solution 结构
│   └── cost.py        ★ 核心成本计算
│
├── construct/         ← 初始解构造
│   ├── spiral_init.py    螺旋构造（原创思想）
│   ├── tiered_init.py ★ 分层构造（最终用）
│   └── solution_utils.py 路径修复工具
│
├── alns/              ← ALNS 算法
│   ├── operators.py      破坏+修复算子
│   ├── main.py        ★ ALNS 主循环
│   ├── operators_v2.py   精细化算子
│   └── v2.py             加强版主循环
│
├── experiments/       ← 失败尝试的记录（可写进论文）
│   ├── aggressive_merge.py
│   └── piggyback.py
│
└── viz/               ← 可视化
    └── visualize.py
```

每个子目录都有自己的 `README.md` 详细说明。

---

# A题 代码架构与原理说明

## 📁 文件清单与依赖关系

```
[第一层·数据与参数]
problem.py        ← 所有常量、数据类（车型/客户/问题）
data_loader.py    ← 读4个xlsx → Problem对象
solution.py       ← Route/Solution 结构 + 评估函数

[第二层·核心计算]
cost.py           ← ★ 最关键的模块：速度时变+载重能耗+时间窗成本

[第三层·初始解构造]
spiral_init.py    ← 螺旋算法（你的原创思想，作为分层的子模块）
tiered_init.py    ← ★ 分层构造（最终用的初始解生成器）
solution_utils.py ← 路径内部排序、合并修复工具

[第四层·ALNS]
alns_operators.py    ← 破坏/修复算子（基础版：4破坏+2修复）
alns_main.py         ← ★ ALNS 主循环：退火+权重更新
alns_operators_v2.py ← 精细化：2-opt/relocate/merge
alns_v2.py           ← 加强版主循环：带重启

[第五层·后处理（实验用，未采用）]
aggressive_merge.py  ← 激进合并尝试（证明是0改进）
piggyback.py         ← 小客户搭便车（证明是0改进）

[第六层·辅助]
visualize.py      ← matplotlib 画路径图
```

依赖方向：上层依赖下层。每个文件都能独立 `python3 xxx.py` 运行做单元测试。

---

## 🔧 各文件详解

### 1. `problem.py` — 参数与数据类定义

**作用**：集中定义所有全局参数、常量、数据结构。其他所有模块从这里 import。

**关键设计**：
- `SPEED_SEGMENTS`：8 段速度分布（题目给3段+我们补的早8点前/17点后），每段写成 `(t_start, t_end, mean, var)`元组，这样 `speed_at(t)` 可以 O(8) 查找
- `VEHICLE_TYPES`：5 种车型的静态列表，题目给定的数据
- `Customer` / `Problem`：用 `@dataclass` 装饰器，避免手写 `__init__`

**参数决策追溯**：
- `DEPART_TIME = 7.5`（7:30出发）：你决定的
- `MAX_WORK_HOURS = 15.0`：几经调整，从12→14→15
- 17:00 后按顺畅：你的理由"下班后货车限行解除"
- 载重系数线性：文献主流，能在论文里辩护

---

### 2. `data_loader.py` — 数据读取

**作用**：把4个xlsx读进来，组装成一个 `Problem` 对象。

**关键处理**：
- 订单按 `目标客户编号` 聚合成每个客户的 `(总重量, 总体积)` —— 这一步把 2169 条订单压缩成 98 个客户需求
- 自动识别"幽灵客户"（有坐标时间窗但没订单）并保留在 `Problem` 中，需求置0（为问题3留用）
- 时间窗字符串 `"HH:MM"` → 浮点小时数（方便计算）

**踩过的坑**：距离矩阵偶有"矩阵距离 < 欧氏距离"的情况（约18%），但题目要求用矩阵，所以我们以矩阵为准。

---

### 3. `solution.py` — 解的数据结构

**作用**：定义"一个解"长什么样。

**核心类**：
```python
Route:       vtype(车型) + nodes([0, c1, c2, ..., 0]) + delivered_kg/m3(SDVRP拆分量)
Solution:    routes(List[Route]) + unassigned(ALNS破坏后的池)
```

**SDVRP 的关键处理**：`delivered_kg` 是一个 dict `{cid: 送的量}`。同一个客户可以在**多条路径**里都出现（每条路径送一部分），合起来等于总需求。`evaluate_solution` 会调用 `evaluate_route` 并把这些 dict 作为 `demand_override` 传入。

---

### 4. `cost.py` — ★ 核心成本计算

**作用**：给定一条路径 + 一辆车，算出完整成本分解。

**最关键的技术点**：

**① 跨段行驶时间 `travel_time(dist, t_start)`**

车辆在一条边上行驶时，可能**跨过速度段边界**（比如 7:30 出发开 1 公里后到 8:00，速度从55.3骤降到9.8）。算法用 while 循环：
```
t = t_start
while 距离未走完:
    找到 t 所在的速度段, 速度 v, 段结束时间 seg_end
    本段最多能跑 v * (seg_end - t) 公里
    若剩余距离 ≤ 本段能跑的 → 时间 += 剩余/v, 距离耗尽
    否则 → 时间跳到 seg_end, 剩余距离 -= v*(seg_end-t)
```

**② 能耗公式**

基础能耗 FPK/EPK 是速度的 U 型函数（题目给定）：
- `FPK = 0.0025v² - 0.2554v + 31.75` L/100km
- `EPK = 0.0014v² - 0.12v + 36.19` kWh/100km

然后乘载重系数：`η(load) = 1 + α·(current_load/capacity)`

边能耗同样分段累加：同一条边内不同段用不同速度计算不同能耗。

**③ 时间窗惩罚**

```python
if 到达时间 < tw_start:
    早到成本 = (tw_start - 到达时间) × 20/h
    车辆等待到 tw_start 才服务
elif 到达时间 > tw_end:
    晚到成本 = (到达时间 - tw_end) × 50/h
    立即服务
```

**④ 载重递减**

车从 depot 出发时满载，每送完一个客户载重递减。这影响**后续边的能耗**（载重系数减小）。

**函数签名**：
```python
evaluate_route(prob, vtype, route, demand_override=None, volume_override=None)
                                    ↑ SDVRP 关键：若非空则用这个dict代替客户完整需求
→ RouteCost(启动 + 能耗 + 碳排 + 早到 + 晚到 + 总里程 + 总时间 + 可行性)
```

---

### 5. `spiral_init.py` — 螺旋构造

**作用**：你的原创思想——把客户按阿基米德螺线 `r(θ) = a + b·θ/(2π)` 排序，作为"一维访问顺序"。

**核心算法**：`spiral_order(prob, clockwise, outward)`

```
对每个客户 (x, y):
    以配送中心为原点，计算极坐标 (r_i, θ_i)
    如果顺时针: θ_i' = 2π - θ_i
    "螺旋累积角度" = 2π(r_i - r_min)/b + ((θ_i' - 2π(r_i - r_min)/b) mod 2π)
                   ←  基础累积角度  ←  对齐到实际角度的偏差
按累积角度排序 → 得到螺旋访问序
```

**为什么这么复杂**：朴素做法"先按圈分组再按角度"会在圈边界产生大跳跃。正确做法是**让同一条螺旋曲线上的客户有连续的累积角**，这就是这段数学的意义。

**参数**：
- `b`：螺距，默认 `(r_max - r_min) / 4` 圈
- `clockwise`: 是否顺时针
- `outward`: 是否由内到外

然后 `spiral_construct` 按这个顺序贪心切分成路径。不过，最终我们没直接用这个，而是让它作为 `tiered_init` 的**小客户部分**的排序依据。

---

### 6. `tiered_init.py` — ★ 分层构造（最终采用）

**作用**：根据客户需求规模分成大/中/小三类，分别用不同策略构造初始路径。

**核心洞察**：数据告诉我们大客户（36个）占 96 辆车、中客户（18个）占 18 辆、小客户（34个）只用 6 辆——**96%的"用车数"被结构性决定了，ALNS能优化的只有小客户部分**。所以对应三种策略：

**大客户（需求 > 3000kg 或 > 15m³）**：
- 必须拆分到多辆车（SDVRP）
- 每辆车满载跑一趟
- 最后剩余量用小车（省3000kg大车给中客户用）

**中客户（容量50%-100%）**：
- 独占一辆车（合并利用率低）
- 如果大车用完了，允许拆到多辆小车

**小客户（容量 < 50%）**：
- 按螺旋序贪心合并
- `look_ahead = 8`：当前装不下时往后看8个客户有没有能装的
- 每次新加客户都 `evaluate_route` 检查时间窗是否破坏

这个分层让初始解成本从纯螺旋的 117k 降到 122k（看起来变高了，但 **100% 可行**，而纯螺旋有 30 条不可行路径）。

---

### 7. `solution_utils.py` — 解的修复工具

**作用**：提供两个实用函数。

**`sort_routes_by_tw(prob, sol)`**：对每条路径内部按 `tw_start` 排序
- 如果排序后可行且更便宜 → 采用
- 否则保留原路径
- 这是一个"便宜的修复"，常常能省 500-2000 元

**`try_merge_routes(prob, sol, max_attempts)`**：两两尝试合并
- 找能装下两条路径总载的车
- 合并后 `evaluate_route` 检查可行
- 只在新成本 < 两条旧成本之和时采用

**`repair_infeasible_routes`**：对不可行路径尝试按时间窗排序 + 贪心切断
- 实际效果有限，因为 `tiered_init` 输出的解基本已经可行

---

### 8. `alns_operators.py` — 基础算子

**作用**：定义 4 个破坏 + 2 个修复算子。

**破坏算子**：
| 算子 | 策略 |
|---|---|
| `random_removal` | 随机抽走 k 个访问 |
| `worst_removal` | 抽走"抽走后降成本最多"的 k 个 |
| `shaw_removal` | 抽走一个种子 + 与其相似（距离+时间窗近）的 k-1 个 |
| `route_removal` | 随机删 1-2 整条路径 |

**修复算子**：
| 算子 | 策略 |
|---|---|
| `greedy_insertion` | 对池中每个客户找插入成本最低的位置 |
| `random_insertion` | 随机打乱顺序再插入 |

**SDVRP 处理**：修复时如果整装插入失败，尝试拆分（按最大可用车容量拆）。

**"访问"概念**：一个访问 = `(route_idx, position, cid)` 三元组。同一客户可以有多个访问（SDVRP拆分）。破坏移除的是访问，不是客户。

---

### 9. `alns_main.py` — ★ ALNS 主循环

**作用**：模拟退火 + 自适应权重的标准 ALNS 实现。

**每次迭代**：
```
1. 按权重随机选 destroy 算子 d, repair 算子 r
2. 破坏大小 k = 总访问数 × random(5%~20%)
3. candidate = deepcopy(current); d(candidate, k); r(candidate, removed)
4. 检查 candidate 完整性（所有客户需求是否被配送）
5. 计算候选分数 = 成本 + 不可行路径惩罚(2000/条)
6. 接受判据（优先级：best > better > SA接受 > 拒绝）
7. 更新算子分数（best:+33, better:+13, accepted:+9, rejected:+0）
8. 每 100 次迭代：权重 = 0.7×老权重 + 0.3×(分数/次数)
9. 温度衰减：T *= 0.9985
```

**关键细节**：

**① 可行性与 best 分离**：
- 带惩罚分数 `cand_score = cost + 2000×不可行路径数`用于 SA 接受判据
- 真实成本 `cand_best_cost`（仅当完全可行时）才能更新 best
- 这样 ALNS 可以短暂探索不可行区域，但最终输出必然可行

**② 初始可行性处理**：
- 如果初始解不可行，`best_cost = ∞`，等待 ALNS 找到第一个可行解

---

### 10. `alns_operators_v2.py` — 精细化算子

**作用**：加入局部搜索算子，在 ALNS 的 repair 后做"打磨"。

**`two_opt_route(r)`**：
```
对路径 [0, c1, c2, ..., cn, 0]
枚举 i<j, 反转 [c_i..c_j]
如果新路径可行且更便宜 → 接受，从头再扫
```
适合消除"交叉"。

**`relocate_customer(sol)`**：
```
随机挑一个路径A的一个客户c
尝试把c从A移到其他路径B的任意位置
如果总成本下降 → 接受
```
适合平衡负载。

**`merge_routes(sol)`**：
```
随机挑两条路径, 合并后按时间窗排序
尝试所有能装下的车型
如果总成本下降 → 合并
```
适合减少路径数。

**`local_search`** 按顺序调用这三个，作为 repair 后的精修。

---

### 11. `alns_v2.py` — 加强 ALNS

**作用**：在 `alns_main` 基础上增加两个重要机制。

**① 周期性局部搜索**：
- 每 5 次迭代调一次 `local_search(do_2opt=True)` 
- 每 20 次迭代调 `do_merge=True`（因为 merge 较慢）

**② 无改进重启**：
- 连续 500 次迭代没找到新 best → current = best（回到最优位置）、温度升回初始的 50%
- 这个机制让算法能跳出"老待在一个局部最优附近"的状态

---

### 12. `aggressive_merge.py` & `piggyback.py` — 归档实验

**结论**：这两个实验证明 107k 附近已经接近最优。代码保留作为**论文中"实验尝试"的证据**。

**`aggressive_merge`**：无视车型约束，任意两条路径都尝试合并（包括升级到更大的车）。结果：0 改进。

**`piggyback`**：让"单客户小路径"搭便车到其他路径的空闲容量里。结果：0 改进。

**原因**：大客户路径（107条中的105条）大多满载，平均余量仅 118kg；而小客户中只有 25%-50% 的需求 ≤ 500kg。匹配空间太小。

---

### 13. `visualize.py` — 可视化

**作用**：用 matplotlib 画路径图。

**两种图**：
- `plot_routes(sol)`：多路径图，每条路径一个颜色 + depot + 绿色区圆
- `plot_spiral_order(order)`：螺旋序可视化，用颜色表示顺序（深→浅）

**注意**：中文字体有问题时用英文标题。

---

## 🚀 使用方法

一个完整的端到端运行：

```python
from data_loader import load_problem
from tiered_init import tiered_construct
from solution_utils import sort_routes_by_tw
from alns_main import run_alns, ALNSConfig
from solution import evaluate_solution, solution_summary

# 1. 加载问题
prob = load_problem()

# 2. 构造初始解（分层 + 内部排序）
init = tiered_construct(prob, clockwise=True, outward=True)
init = sort_routes_by_tw(prob, init)

# 3. ALNS 优化
cfg = ALNSConfig(
    max_iterations=1200,
    initial_temp=5000,
    cooling_rate=0.997,
    destroy_min_frac=0.08,
    destroy_max_frac=0.25,
    segment_size=100,
)
best, history = run_alns(prob, init, cfg)

# 4. 输出结果
info = solution_summary(prob, best)
print(info)
```

多起点版本（最终用的）：重复上述过程 4 次，用不同的 `(clockwise, outward)` 组合作为起点，取成本最小的。

---

## 📊 当前问题1最优结果

| 指标 | 值 |
|---|---|
| 总成本 | **107113 元** |
| 路径数 | 131 |
| 可行率 | 100% (131/131) |
| 总里程 | 15355 km |
| 总碳排 | 10762 kg |
| 启动成本 | 52400 (48.9%) |
| 能耗成本 | 32279 (30.1%) |
| 碳排成本 | 6995 (6.5%) |
| 早到成本 | 15250 (14.2%) |
| 晚到成本 | 189 (0.2%) |

## 🔬 与理论下界比较

| 组成 | 理论下界 | 当前解 | 差距 |
|---|---|---|---|
| 启动成本 | 40000 (100辆车) | 52400 (131辆车) | +12400 |
| 能耗成本 | ~22000 | 32279 | +10000 |
| 碳排成本 | ~4800 | 6995 | +2200 |
| 时间窗惩罚 | ~0 | 15439 | +15000 |
| **合计** | **~67000** | **107113** | **+40000** |

差距主要来自：
1. 大客户拆分导致的启动成本（每个大客户要用好几辆车）
2. 时间窗分布不均（客户时间窗偏向 14-20 点，早上出发必然早到）
