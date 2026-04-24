# construct/ — 初始解构造（第三层）

## 内容

| 文件 | 作用 |
|---|---|
| `spiral_init.py` | 阿基米德螺线排序（你的原创思想） + 基于螺旋的贪心构造 |
| `tiered_init.py` | ★ **最终采用**：分层构造（大/中/小客户分别策略） |
| `solution_utils.py` | 解的修复工具：内部按时间窗排序、尝试合并路径 |

## 为什么分层？

一个关键数据观察（在分析阶段发现）：

| 客户类别 | 数量 | 必需车数 | 占比 |
|---|---|---|---|
| 大客户（需求 > 3000kg） | 36 | 96 | 80% |
| 中客户（容量50%~100%） | 18 | 18 | 15% |
| 小客户（容量 < 50%） | 34 | 6 | 5% |

**96% 的"用车数"被结构性决定**，ALNS 真正能优化的只有小客户部分。所以分层构造让每一层用最合适的策略：

- **大客户**：SDVRP 拆分，每辆车满载跑一趟
- **中客户**：独占一辆合适的车
- **小客户**：按螺旋序贪心合并 + 时间窗检查

## 关键函数签名

```python
# 螺旋序（被 tiered_init 复用）
spiral_order(prob, clockwise=True, outward=True) → List[cid]

# 最终用的构造器
tiered_construct(prob, clockwise=True, outward=True) → Solution

# 便宜的后处理
sort_routes_by_tw(prob, sol) → Solution   # 路径内部按时间窗排序
```

## 使用示例

```python
from construct.tiered_init import tiered_construct
from construct.solution_utils import sort_routes_by_tw

init = tiered_construct(prob, clockwise=True, outward=True)
init = sort_routes_by_tw(prob, init)  # 预优化
# 现在 init 就是一个完全可行的初始解，可以喂给 ALNS
```
