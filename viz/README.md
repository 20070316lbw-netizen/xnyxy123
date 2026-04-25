# viz/ — 可视化

## 内容

| 文件 | 作用 |
|---|---|
| `visualize.py` | matplotlib 画路径图、螺旋序图 (库函数, 供其它脚本调用) |
| `gen_figs.py`  | ★ 一键生成论文用 4 张图到 `figs/`：螺旋构造、Q1 路径、Q1 vs Q2 对比、ALNS 收敛 |

## gen_figs 用法

从项目根目录执行（依赖 `result_q1.pkl` 与 `result_q2.pkl` 已存在）：

```bash
uv run python viz/gen_figs.py
```

输出：
- `figs/fig1_spiral.png`           螺旋序构造示意
- `figs/fig2_q1_routes.png`        Q1 最优路径图
- `figs/fig3_q1_vs_q2.png`         Q1 vs Q2 EV / 燃油对比
- `figs/fig4_alns_convergence.png` ALNS 收敛曲线

## 函数

- `plot_routes(prob, sol, title, fname)`：画出所有路径的路径图，每条路径一个颜色
- `plot_spiral_order(prob, order, title, fname)`：画出螺旋访问序，颜色梯度从深到浅表示顺序

## 用法

三个 `result_q*.pkl` 都可直接读取并画图：

```python
import pickle
from core.data_loader import load_problem
from viz.visualize import plot_routes

prob = load_problem()

# Q1 最优解
with open('result_q1.pkl', 'rb') as f:
    q1 = pickle.load(f)
plot_routes(prob, q1['best'], 'Q1 Optimal', 'figs/q1_best.png')

# Q2 最优解 (注意 policy_mode 设为 hard, 颜色才能区分 EV/燃油)
prob2 = load_problem(); prob2.policy_mode = 'hard'
with open('result_q2.pkl', 'rb') as f:
    q2 = pickle.load(f)
plot_routes(prob2, q2['best'], 'Q2 Policy-Feasible', 'figs/q2_best.png')
```

## 后续扩展

论文需要的图还包括：
- [ ] 收敛曲线（best_cost vs iter）
- [ ] 成本分解饼图
- [ ] 车型使用柱状图
- [ ] 时间窗到达时刻甘特图
- [ ] 问题2 vs 问题1 对比图（绿色区客户对应的车辆切换）
- [ ] 问题3 三场景的"扰动前/快速层后/优化层后"三态对比图

这些可以等做到具体问题时再加。

## 注意

- 中文字体：matplotlib 默认无中文字体；脚本里给标题/图例用英文最稳妥
- `plot_routes` 会自动画出 depot (黑色方块) 与绿色区圆 (半径 10km)
