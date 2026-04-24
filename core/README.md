# core/ — 数据模型与成本计算（基石层）

## 内容

| 文件 | 作用 |
|---|---|
| `problem.py` | 全局常量（出发时间、成本参数、速度分段） + 数据类（Customer, VehicleType, Problem） |
| `data_loader.py` | 读 4 个 xlsx → 组装 Problem 对象 |
| `solution.py` | Route / Solution 数据结构 + 全局评估函数 |
| `cost.py` | ★ **最核心**：路径成本计算（速度时变 + 载重能耗 + 时间窗惩罚） |

## 依赖关系

```
problem.py   ← 无依赖（所有模块都依赖它）
data_loader.py  → problem
solution.py     → problem, cost
cost.py         → problem
```

## 改参数的入口

**几乎所有可调参数都在 `problem.py`**，不用改代码的其他地方：
- 出发时间 / 工作时间上限
- 速度分段
- 油价 / 电价 / 碳价
- 启动成本 / 时间窗惩罚
- 载重系数
- 绿色区半径

## 改问题2时要动什么

只要改 `cost.py`，在 `evaluate_route` 里加一条约束：燃油车在 8:00-16:00 不能进绿色区。具体实现：在每次到达或经过客户时检查 `customer.in_green_zone` 和 `t_arrival`，违反则加大额惩罚（或标记不可行）。
