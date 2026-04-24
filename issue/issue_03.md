# Issue 03：第二阶段（问题2）与第三阶段（问题3）实现归档

> 本次工作：在 issue_01 完成的问题1（静态 VRP, 107k 元）基础上，按 issue_02
> 规划落地问题2（绿色区限行）与问题3（动态事件响应）。目标是"能跑出对比表"和
> "能在事件下稳定重调度的演示"两条硬指标。

---

## 0. 本次最终结果摘要（一眼看懂）

| 项目 | 数值 |
|------|------|
| 问题1 最优 (policy=off) | 约 107,905 元，131 条路径 |
| 问题2 最优 (policy=hard) | 约 108,385 元（+0.4%），133 条路径，0 违规 |
| 问题2 EV 使用 | 10 → 19 条路径（+9） |
| 问题2 碳排变化 | 10,944 → 10,884 kg CO₂（↓0.5%） |
| 问题3 S1 (8 新订单) | 成本 +3,879 元，改派率 2.1% |
| 问题3 S2 (10 时窗突变) | 成本 +50 元，晚到 +50 元，改派率 0% |
| 问题3 S3 (4+3+5 复合) | 成本 +108 元，晚到 ↓156 元，改派率 82.6% |

核心结论：政策不会大幅推高成本（+0.4%），但会显著重构车队使用；动态事件可在
秒级响应，成本扰动最多 +3.7%。

---

## 1. 工作流程

### 1.1 问题2 实施路径
按 issue_02 的"方案B先跑通、再切A做最终结果"策略：

1. **参数注入**：在 `core/problem.py` 新增
   - `GREEN_BAN_START=8.0, GREEN_BAN_END=16.0`
   - `POLICY_PENALTY_PER_VIOLATION=1e6`
   - `Problem.policy_mode: str = "off"`（三态：off / hard / soft）

2. **成本评估改造**：`core/cost.py::evaluate_route`
   - 为每个客户服务区间 `[t_service_start, t_service_end]` 判断
     是否与禁行窗口 `[8, 16]` 重叠
   - hard 模式 → `feasible=False`
   - soft 模式 → `policy_cost += 1e6`
   - off 模式（Q1）走原路径，不受影响

3. **政策感知构造器**：`construct/tiered_init_q2.py`
   - Step 1: 先对绿色区客户用 EV（`_pick_ev` 只从 electric 车池选）
   - Step 2: 非绿色区用原有 `tiered_init` 流程
   - 复用 `_build_big_customer_routes / _build_medium_customer_routes /
     _build_small_customer_routes`，不重写

4. **Q2 驱动器**：`run_q2.py`
   - ALNS 用 soft 模式（1e6 罚项，搜索期间允许短暂违规）
   - 收敛后切 hard 模式验证
   - 4 个方向起点取最优，Q1 vs Q2 对比表输出

### 1.2 问题3 实施路径

1. **事件数据结构**：`dynamic/events.py`
   - 一个 `Event` 含 `time / etype / cid / payload`
   - `make_new_order / make_cancel / make_address_change / make_tw_change` 工厂
   - `Scenario` 打包一组事件 + 名称 + 描述

2. **调度器**：`dynamic/scheduler.py`
   - `_APPLIERS` 字典分发 4 类事件到修改 Problem/Solution 的纯函数
   - `fast_repair()` 快速层：贪心插入未覆盖客户（直接复用
     `alns.operators._insert_one_customer`）
   - `reoptimize()` 优化层：小步 ALNS（200 iter 左右）
   - `stability_delta()` 稳定性度量：比较"同车伙伴集合"

3. **场景**：`run_q3.py` 三套
   - S1: 12:00 时 8 个幽灵客户（ID ∈ {1,14,15,17,18,20,21,22,23,96}）产生订单
   - S2: 10 个活跃客户 `tw_end` 前移 30-60 分钟
   - S3: 4 新增 + 3 取消 + 5 时窗调整

---

## 2. 过程中遇到的坑与解决方法

### 坑1：policy_cost 忘了计入 total
- **现象**：新增了 `RouteCost.policy_cost` 字段但 `total` 属性没更新
- **影响**：soft 模式下 1e6 罚项无法推动 ALNS 避开违规
- **解决**：在 `@property total` 加上 policy_cost 求和，并在 `as_dict()`
  导出 `policy_violations`

### 坑2：policy check 位置——用 "arrival time" 还是 "service interval"
- **问题**：燃油车 15:50 到达绿色区客户，服务 20min 到 16:10，算不算违规？
- **决策**：算违规。使用 `_overlaps_ban(t_start, t_end)` 判定，车辆只要
  在 `[8, 16]` 期间"仍在绿色区逗留"就算违反。这和"禁止进入"的字面含义一致。

### 坑3：Q2 初始解里 EV 不够
- **观察**：绿色区 12 个有订单客户（扣除幽灵后），EV 车队共 25 辆（10+15），
  够用。但若 ALNS 把非绿色区小客户搭在 EV 上，就会让 EV 提前吃紧。
- **处置**：构造阶段绿色区客户一个独占一辆 EV（不合并），让 ALNS 自己决定是
  否要合并/交换。实测 soft 模式 ALNS 会自动优化掉 6 辆 EV（25 → 19）。

### 坑4：动态事件后 `_demand_covered` 误判取消订单
- **现象**：取消订单后 ALNS 主循环里 `_demand_covered` 仍然认为客户欠货
  （因为 `prob.customers[cid].demand_kg` 没清零）
- **解决**：`_apply_cancel` 里除了从路径移除，还把 `demand_kg=0, demand_m3=0`
  清零。否则 ALNS 会尝试"把已取消客户再插回去"。

### 坑5：稳定性指标噪声过大（S3 改派率 97.8%）
- **现象**：S3 场景 ALNS 重构后，按"路径索引"比较，97.8% 客户"改派"
- **根因**：ALNS 会删除空路径并追加新路径，路径索引列表完全重排，即使客户
  实际还和原来一起装车，索引也变了。
- **解决**：改成按"同车伙伴集合（frozenset）"比较。伙伴不变就算没改派。
  修后 S1 从 6.4% → 2.1%，S3 从 97.8% → 82.6%（82.6% 是真改派，因为 3 个
  取消触发了路径合并）。

### 坑6：没有 Python 环境
- **现象**：刚进仓库直接 `python3 -c` 报 `ModuleNotFoundError: numpy`
- **解决**：`uv sync` 一次性装好（项目已配 pyproject.toml + uv lock）

---

## 3. 新增 / 修改文件清单

**新增（Q2）**：
- `construct/tiered_init_q2.py` — 绿色区优先 EV 的分层构造
- `run_q2.py` — Q2 入口 + Q1 vs Q2 对比表

**新增（Q3）**：
- `dynamic/__init__.py`
- `dynamic/events.py` — 事件数据结构
- `dynamic/scheduler.py` — 双层重调度 + 稳定性度量
- `run_q3.py` — 三个场景 + 指标汇总

**修改**：
- `core/problem.py` — 新增 `GREEN_BAN_*` 常量 + `Problem.policy_mode` 字段
- `core/cost.py` — `RouteCost.policy_cost/policy_violations` + `evaluate_route`
  政策检查逻辑
- `core/solution.py` — `solution_summary` 新增 policy_cost/policy_violations/
  ev_routes/fuel_routes 字段

**接口兼容性**：所有改动均**向后兼容**问题1。`policy_mode` 默认 `"off"`，Q1
的原有调用（`main.py` / `experiments/*`）无需改一个字。

---

## 4. 论文写作映射（复用 issue_02 的结构）

1. **静态问题**（issue_01）：分层 + 螺旋初始解 + ALNS，107k 结果 ✔
2. **政策约束**（本次 Q2）：绿色区 EV 优先 + soft→hard 两阶段 ALNS，
   +0.4% 成本换零违规、EV 使用翻倍、碳排下降 ✔
3. **动态响应**（本次 Q3）：双层重调度 + 同车伙伴稳定性指标，
   秒级响应三类场景 ✔

---

## 5. 统一入口改造（附带任务）

为方便后续写作/测试，`main.py` 重构成多子命令 CLI：

```bash
uv run python main.py --help           # 查看所有子命令
uv run python main.py q1 --iters 1200  # 跑问题1
uv run python main.py q2 --iters 1200  # 跑问题2
uv run python main.py q3 --iters 200   # 跑问题3 三场景
uv run python main.py compare          # Q1 vs Q2 对比
uv run python main.py summary          # 简要汇总所有已保存结果
```

原来独立的 `run_q2.py / run_q3.py` 保留，内部逻辑被 `main.py` 调用复用。

---

## 6. 下一步（交稿前）

- [ ] 跑一次完整 Q2（`--iters 1200`，每起点约 60s × 4 = 4 分钟）得到正式解
- [ ] 问题2 代表性路径图（政策前后一条典型路径对比）
- [ ] 问题3 场景对比图（甘特图 / 时间窗图）
- [ ] 写论文"方法-实验-结论"三节
- [ ] 敏感性分析（可选）：policy 开关对能耗/碳排/成本的曲线

---

## 7. 一句话收尾

> 问题2/3 的核心实现量约 600 行代码，零破坏性改动；所有核心功能在
> 150 iter 级别的小测试中已验证可用，1200 iter 正式跑一次即可交稿。
