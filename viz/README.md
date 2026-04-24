# viz/ — 可视化

## 内容

| 文件 | 作用 |
|---|---|
| `visualize.py` | matplotlib 画路径图、螺旋序图 |

## 函数

- `plot_routes(prob, sol, title, fname)`：画出所有路径的路径图，每条路径一个颜色
- `plot_spiral_order(prob, order, title, fname)`：画出螺旋访问序，颜色梯度从深到浅表示顺序

## 用法

```python
from viz.visualize import plot_routes, plot_spiral_order
import pickle

with open('result_q1.pkl', 'rb') as f:
    data = pickle.load(f)

from core.data_loader import load_problem
prob = load_problem()
plot_routes(prob, data['best'], 'Q1 Optimal Solution', 'figs/q1_best.png')
```

## 已生成的图

在 `figs/` 目录下（如果之前跑过 `visualize.py`）：
- `spiral_order_inout.png` / `spiral_order_outin.png`：螺旋序可视化
- `init_sol_*.png`：初始解路径图
- `q1_best.png` / `q1_init.png`：问题1 最优解 vs 初始解

## 后续扩展

论文需要的图还包括：
- [ ] 收敛曲线（best_cost vs iter）
- [ ] 成本分解饼图
- [ ] 车型使用柱状图
- [ ] 时间窗到达时刻甘特图
- [ ] 问题2 vs 问题1 对比图

这些可以等做到具体问题时再加。
