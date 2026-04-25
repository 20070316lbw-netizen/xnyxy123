# dynamic/ — 动态事件调度（问题3）

## 内容

| 文件 | 作用 |
|---|---|
| `events.py` | 事件数据结构 + 4 类事件构造函数 |
| `scheduler.py` | ★ **双层调度器**：快速层 (贪心插入) + 优化层 (小步 ALNS) + 稳定性度量 |

## 设计思想：双层决策

实时调度的两个矛盾目标：
- **响应延迟要短**（用户期望几秒内有响应）
- **解的质量要高**（不能为了快牺牲成本）

我们的解法是把这两件事拆成两层：

| 层 | 调用 | 时间尺度 | 任务 |
|---|---|---|---|
| 快速层 | `fast_repair` | 毫秒级 | 把新增订单贪心插入当前最便宜的位置；保证立即可用 |
| 优化层 | `reoptimize` | 秒级 (200 iter ALNS, T₀=1500) | 在快速层结果上做小步搜索，进一步压缩成本 |

调用入口：`apply_scenario(prob, base_sol, scenario, reopt_iters=200)` 把这两层串起来，并返回 `ScenarioResult` 指标。

## 4 类事件

| 类型 | 构造函数 | payload | 应用效果 |
|---|---|---|---|
| `new_order` | `make_new_order(time, cid, demand_kg, demand_m3, tw_start, tw_end)` | 4 字段 | 写入对应客户的需求与时间窗 |
| `cancel_order` | `make_cancel(time, cid)` | — | 从所有路径里移除该客户，需求清零 |
| `address_change` | `make_address_change(time, cid, new_x, new_y)` | (x, y) | 更新坐标并重算距离矩阵的对应行/列 |
| `tw_change` | `make_tw_change(time, cid, tw_start=None, tw_end=None)` | 任一窗口 | 修改时间窗 |

事件成组打包成 `Scenario(name, events, description)`。

## 稳定性度量

调度系统不仅要省钱，还要不能频繁改派 (司机不喜欢中途换路)。我们用**同车伙伴集合**作为指纹：

```python
sig(cid) = frozenset(同路径其他客户)
reassigned_frac = #{cid: sig_before(cid) != sig_after(cid)} / 总客户数
```

这种度量对路径索引的重排序是鲁棒的——只要伙伴没变，就算"没改派"。

## `ScenarioResult` 字段

每个场景跑完后返回一个 dataclass，包含：
- `cost_before / cost_after_fast / cost_after_reopt`：三态成本
- `late_before / late_after`：晚到成本变化
- `response_ms_fast / response_ms_reopt`：两层响应时间
- `routes_before / routes_after`：路径数变化
- `reassigned_frac`：改派比例
- `num_events / events_by_type`：事件构成

## 当前性能 (Q1 解扰动后)

| 场景 | 事件 | 成本 Δ | 晚到 Δ | 快速层耗时 | 优化层耗时 | 改派率 |
|---|---|---|---|---|---|---|
| S1 新增订单潮 | 8 新增 | +3879 | 0 | 1.3 ms | 1.9 s | 2.1% |
| S2 时间窗突变 | 10 个 tw_end 前移 | +50 | +50 | 0.1 ms | 2.8 s | 0.0% |
| S3 复合事件 | 4 新增 + 3 取消 + 5 时间窗 | +108 | -156 | 0.4 ms | 2.5 s | 82.6% |

S3 改派率高是因为取消 3 个客户后，邻近路径的负载结构发生剧烈变化，ALNS 重新组合是预期行为。

## 怎么扩展

要加新事件类型：
1. 在 `events.py` 加 `make_xxx` 构造函数
2. 在 `scheduler.py` 写 `_apply_xxx(prob, sol, ev)` 处理函数
3. 在 `_APPLIERS` dict 注册

`fast_repair` 与 `reoptimize` 不需要改 —— 它们只看修改后的 `Problem` 和 `Solution`。
