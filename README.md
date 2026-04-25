# 城市绿色物流配送调度 — 代码仓库

A 题三个子问题的统一实现：
- **Q1** 静态 VRP — 速度时变 + 载重能耗 + 时间窗 + SDVRP
- **Q2** 在 Q1 基础上加入绿色区限行政策 (8:00–16:00 禁燃油车进绿色区)
- **Q3** 配送过程中出现动态事件 (新增 / 取消 / 时间窗变 / 地址变) 时的实时调度

算法主体是分层构造 + ALNS；问题2 加了政策感知的构造器与软/硬罚项；问题3 用双层调度 (秒级快速插入 + 分钟级 ALNS 重优化)。

---

## 🚀 快速开始

```bash
# 一次性安装依赖
uv sync

# 运行三个子问题（统一入口）
uv run python main.py q1 --iters 1200    # ~3 min, 最优 ~107k
uv run python main.py q2 --iters 1200    # ~3 min, 政策可行 ~108k
uv run python main.py q3 --iters 200     # 三个动态场景

# 对比 / 摘要
uv run python main.py compare            # Q1 vs Q2 表
uv run python main.py summary            # 所有保存结果的摘要
```

每条 `qN` 命令会把结果落盘到 `result_qN.pkl`，可被 `compare` / `summary` / 后续脚本复用。

---

## 📁 目录结构

```
.
├── README.md            ← 本文档
├── main.py              ← 统一 CLI 入口 (q1/q2/q3/compare/summary)
├── run_q2.py            ← 问题2 端到端 (供 main.py 调用, 也可独立运行)
├── run_q3.py            ← 问题3 端到端 (定义三个标准场景)
│
├── core/                ← 数据模型 + 成本计算 (基石层)
│   ├── problem.py          全局常量 + 数据类 + 政策开关 policy_mode
│   ├── data_loader.py      读 4 个 xlsx → Problem
│   ├── solution.py         Route / Solution + 评估 / 摘要
│   └── cost.py          ★ 速度时变 + 载重能耗 + 时间窗 + 政策罚
│
├── construct/           ← 初始解构造
│   ├── spiral_init.py      阿基米德螺线排序
│   ├── tiered_init.py   ★ 大/中/小客户分层构造 (Q1 用)
│   ├── tiered_init_q2.py   政策感知构造: 绿色区客户优先 EV (Q2 用)
│   └── solution_utils.py   路径修复: 时间窗排序 / 合并尝试
│
├── alns/                ← ALNS 算法
│   ├── operators.py        基础算子 (4 破坏 + 2 修复)
│   ├── main.py          ★ 主循环 (退火 + 自适应权重)
│   ├── operators_v2.py     精细化: 2-opt / relocate / merge
│   └── v2.py               增强版: 周期性局部搜索 + 重启
│
├── dynamic/             ← 问题3 动态调度
│   ├── events.py           事件数据结构 (4 类事件)
│   └── scheduler.py     ★ 双层调度: 快速贪心 + 优化层 ALNS
│
├── experiments/         ← 失败尝试归档 (论文用素材)
│   ├── aggressive_merge.py
│   └── piggyback.py
│
├── viz/                 ← 可视化
│   ├── visualize.py        路径图 + 螺旋序图 (库函数)
│   └── gen_figs.py         一键生成论文图 fig1~fig4 (脚本)
│
├── data/                ← 题目数据 (xlsx) 与原题 PDF
├── figs/                ← 论文用图片 (gen_figs 输出)
├── A202611902032.docx   ← 比赛指定承诺书 (随压缩包一起提交)
└── result_q{1,2,3}.pkl  ← 三个问题的最优解快照 (可直接被 viz 读取)
```

每个子目录都有自己的 `README.md` 详细说明设计与接口。

---

## 📊 当前结果

### 问题 1 — 静态 VRP

| 指标 | 值 |
|---|---|
| 总成本 | **107905 元** |
| 路径数 | 131 (全部可行) |
| 总里程 | 15493 km |
| 总碳排 | 10944 kg CO₂ |
| EV / 燃油路径 | 10 / 121 |
| 启动 / 能耗 / 碳排 / 早到 / 晚到 | 52400 / 32810 / 7114 / 15323 / 260 |

### 问题 2 — 绿色区限行 (与 Q1 同实例)

| 指标 | Q1 (无政策) | Q2 (硬模式可行) | Δ |
|---|---|---|---|
| 总成本 | 107905 | **108385** | +480 |
| 路径数 | 131 | 133 | +2 |
| EV 路径 | 10 | 19 | +9 |
| 燃油路径 | 121 | 114 | -7 |
| 政策违规 | (1 起) | **0** | — |
| 总碳排 | 10944 | 10885 | -59 |

政策成本几乎为零 —— 把绿色区客户全切到 EV，多用 9 辆 EV 即可零违规，多花 480 元。

### 问题 3 — 动态事件三场景 (基于 Q1 解扰动)

| 场景 | 事件构成 | 成本 Δ | 晚到 Δ | 改派率 | 优化层耗时 |
|---|---|---|---|---|---|
| S1 新增订单潮 | 8 新增 (12:00) | +3879 | 0 | 2.1% | 1.9s |
| S2 时间窗突变 | 10 个 tw_end 前移 30-60min | +50 | +50 | 0.0% | 2.8s |
| S3 复合事件 | 4 新增 + 3 取消 + 5 时间窗前移 | +108 | -156 | 82.6% | 2.5s |

快速层 (贪心插入) 平均响应 < 2ms，优化层 200 次 ALNS 迭代 < 3s。

---

## 🧠 核心思想速览

### Q1 的关键技术点
- **跨段行驶时间**：边的速度可能跨越 8 个分段中多个，按段累积时间与能耗 (见 `core/cost.py`)
- **载重递减能耗**：`η(load) = 1 + α·current_load/capacity`，每送一个客户后载重下降，影响后续边的能耗
- **分层构造**：36 个大客户 + 18 个中客户 = 96% 的车数被结构性决定，ALNS 真正能优化的只有小客户部分 (见 `construct/`)
- **ALNS**：4 破坏 (random/worst/shaw/route) + 2 修复 (greedy/random)，自适应权重 + 模拟退火 (见 `alns/`)

### Q2 的政策处理
- `Problem.policy_mode` 三档：`off` (Q1) / `soft` (ALNS 搜索阶段，违规 +1e6) / `hard` (验收阶段，违规 → infeasible)
- `tiered_init_q2`：先把绿色区客户用 EV 独占处理，从源头消灭违规；ALNS 沿用 Q1 的算子无须改动

### Q3 的双层调度
- **快速层** (`fast_repair`)：对新增订单做贪心插入，毫秒级响应
- **优化层** (`reoptimize`)：在快速层结果上跑小步 ALNS (200 iter, T₀=1500)，分钟内压缩成本
- **稳定性度量** (`stability_delta`)：客户的"同车伙伴集合"是否变化，作为改派率指标

---

## 🔬 与理论下界的差距

Q1 的解 107905 元 vs 理论下界 ~67000 元，主要差距来自：
1. **大客户拆分** 不可避免地需要 96 辆车 (启动成本下界 40k → 实际 52k)
2. **时间窗分布偏后** (集中在 14-20 点)，7:30 出发必然产生 ~15k 早到成本

`experiments/` 下两个零改进的实验 (`aggressive_merge`, `piggyback`) 进一步证明 107k 已接近实际下界 —— 这是数据的结构性特征，不是算法的问题。

---

## 🛠 复现 / 单元运行

每个文件都可独立运行做单元自测：
```bash
uv run python core/cost.py             # 速度分段 + 单边行驶时间 + 路径评估
uv run python core/problem.py          # 打印参数概览
uv run python construct/tiered_init.py # Q1 初始解
uv run python construct/tiered_init_q2.py # Q2 初始解 (含政策检查)
```

### 重新生成论文图片

`figs/` 下的四张图都由 `viz/gen_figs.py` 一次性生成，依赖根目录的 `result_q1.pkl` 与 `result_q2.pkl`：

```bash
# 先确保两个 pkl 已经存在 (没有的话先跑一次 q1 / q2)
uv run python main.py q1 --iters 1200
uv run python main.py q2 --iters 1200

# 重新生成 figs/fig{1,2,3,4}_*.png
uv run python viz/gen_figs.py
```

主要可调参数都集中在 `core/problem.py`，无需深入算法层。
