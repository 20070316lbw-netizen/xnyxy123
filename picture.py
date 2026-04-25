# gen_figs.py  —— 放在项目根目录，与 core/ alns/ 同级
import pickle
import math
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK JP", "Droid Sans Fallback", "SimHei", "Arial", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

os.makedirs("figs", exist_ok=True)

from core.data_loader import load_problem
from core.problem import GREEN_ZONE_CENTER, GREEN_ZONE_RADIUS
from core.solution import evaluate_solution
from construct.spiral_init import spiral_order

prob = load_problem()

with open("result_q1.pkl", "rb") as f:
    q1 = pickle.load(f)
with open("result_q2.pkl", "rb") as f:
    q2 = pickle.load(f)

sol1 = q1["best"]
sol2 = q2["best"]
hist1 = q1["history"]

cost1, _ = evaluate_solution(prob, sol1)
cost2, _ = evaluate_solution(prob, sol2)
dist1 = sum(evaluate_solution(prob, sol1)[1][i].total_distance for i in range(len(sol1.routes)))
dist2 = sum(evaluate_solution(prob, sol2)[1][i].total_distance for i in range(len(sol2.routes)))

# ────────────────────────────────────────────
# 图1  螺旋序构造示意图
# ────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(14, 6))

directions = [
    (True,  True,  "顺时针·由内到外"),
    (True,  False, "顺时针·由外到内"),
]

for ax, (cw, ow, title) in zip(axes, directions):
    order = [c for c in spiral_order(prob, clockwise=cw, outward=ow)
             if prob.customers[c].demand_kg > 0]

    gx, gy = GREEN_ZONE_CENTER
    circle = plt.Circle((gx, gy), GREEN_ZONE_RADIUS,
                         color="#2ca25f", alpha=0.10, zorder=0)
    ax.add_patch(circle)
    ax.plot(gx, gy, "+", color="#2ca25f", ms=14, zorder=2)

    # 螺旋连线（颜色按顺序渐变）
    n = len(order)
    cmap = plt.cm.plasma
    for i in range(n - 1):
        c0 = prob.customers[order[i]]
        c1 = prob.customers[order[i + 1]]
        ax.plot([c0.x, c1.x], [c0.y, c1.y],
                color=cmap(i / n), alpha=0.55, lw=0.9, zorder=1)

    xs = [prob.customers[c].x for c in order]
    ys = [prob.customers[c].y for c in order]
    sc = ax.scatter(xs, ys,
                    c=range(n), cmap="plasma", s=28, zorder=3,
                    vmin=0, vmax=n)

    depot = prob.depot
    ax.plot(depot.x, depot.y, "s", color="#d73027", ms=11, zorder=5,
            label="Depot")

    ax.set_aspect("equal")
    ax.set_title(title, fontsize=13)
    ax.set_xlabel("X (km)", fontsize=11)
    ax.set_ylabel("Y (km)", fontsize=11)
    ax.grid(True, alpha=0.25)

    cb = fig.colorbar(sc, ax=ax, fraction=0.035, pad=0.04)
    cb.set_label("访问顺序", fontsize=10)

axes[0].legend(fontsize=10)
fig.suptitle("图1  阿基米德螺旋构造初始访问序\n（颜色由深→浅表示访问顺序从早→晚）",
             fontsize=13, y=1.02)
plt.tight_layout()
plt.savefig("figs/fig1_spiral.png", dpi=150, bbox_inches="tight")
plt.close()
print("fig1 done")


# ────────────────────────────────────────────
# 图2  问题一最优路径图
# ────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(10, 9))

gx, gy = GREEN_ZONE_CENTER
circle = plt.Circle((gx, gy), GREEN_ZONE_RADIUS,
                     color="#2ca25f", alpha=0.10, zorder=0, label="绿色区")
ax.add_patch(circle)
ax.plot(gx, gy, "+", color="#2ca25f", ms=14, zorder=2)

cmap20 = plt.cm.tab20
for i, r in enumerate(sol1.routes):
    col = cmap20(i % 20)
    xs = [prob.customers[c].x for c in r.nodes]
    ys = [prob.customers[c].y for c in r.nodes]
    lw = 1.2 if r.vtype.is_electric else 0.7
    ax.plot(xs, ys, "-", color=col, alpha=0.45, lw=lw, zorder=3)

# 客户点
all_cids = set()
for r in sol1.routes:
    all_cids.update(c for c in r.nodes if c != 0)
cx = [prob.customers[c].x for c in all_cids]
cy = [prob.customers[c].y for c in all_cids]
ax.scatter(cx, cy, c="steelblue", s=18, zorder=4, alpha=0.85)

depot = prob.depot
ax.plot(depot.x, depot.y, "s", color="#d73027", ms=12, zorder=5)

from matplotlib.lines import Line2D
legend_elems = [
    Line2D([0], [0], marker="s", color="w", markerfacecolor="#d73027",
           markersize=10, label="配送中心"),
    mpatches.Patch(facecolor="#2ca25f", alpha=0.3, label="绿色配送区"),
    Line2D([0], [0], color="steelblue", lw=1.5, label=f"配送路径（共{len(sol1.routes)}条）"),
]
ax.legend(handles=legend_elems, fontsize=10, loc="upper right")

ax.set_aspect("equal")
ax.set_title(f"图2  问题一最优配送方案\n总成本 {cost1:,.0f} 元，{len(sol1.routes)} 条路径，"
             f"里程 {dist1:,.0f} km", fontsize=12)
ax.set_xlabel("X (km)", fontsize=11)
ax.set_ylabel("Y (km)", fontsize=11)
ax.grid(True, alpha=0.25)
plt.tight_layout()
plt.savefig("figs/fig2_q1_routes.png", dpi=150, bbox_inches="tight")
plt.close()
print("fig2 done")


# ────────────────────────────────────────────
# 图3  Q1 vs Q2  EV / 燃油路径对比
# ────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(16, 7))

titles   = ["问题一（无政策）", "问题二（政策可行）"]
solutions = [sol1, sol2]
subtitles = [
    f"EV {sum(1 for r in sol1.routes if r.vtype.is_electric)}条 / 燃油 {sum(1 for r in sol1.routes if not r.vtype.is_electric)}条，总成本 {cost1:,.0f} 元",
    f"EV {sum(1 for r in sol2.routes if r.vtype.is_electric)}条 / 燃油 {sum(1 for r in sol2.routes if not r.vtype.is_electric)}条，总成本 {cost2:,.0f} 元",
]

for ax, sol, title, sub in zip(axes, solutions, titles, subtitles):
    gx, gy = GREEN_ZONE_CENTER
    circle = plt.Circle((gx, gy), GREEN_ZONE_RADIUS,
                         color="#2ca25f", alpha=0.12, zorder=0)
    ax.add_patch(circle)
    ax.plot(gx, gy, "+", color="#2ca25f", ms=14, zorder=2)

    for r in sol.routes:
        xs = [prob.customers[c].x for c in r.nodes]
        ys = [prob.customers[c].y for c in r.nodes]
        if r.vtype.is_electric:
            ax.plot(xs, ys, "-", color="#2166ac", alpha=0.75,
                    lw=1.5, zorder=4)
        else:
            ax.plot(xs, ys, "-", color="#d6604d", alpha=0.35,
                    lw=0.7, zorder=3)

    # 绿色区客户高亮
    green_cids = [c.cid for c in prob.customers[1:]
                  if c.in_green_zone and c.demand_kg > 0]
    other_cids = [c.cid for c in prob.customers[1:]
                  if not c.in_green_zone and c.demand_kg > 0]

    ax.scatter([prob.customers[c].x for c in other_cids],
               [prob.customers[c].y for c in other_cids],
               c="#aaaaaa", s=16, zorder=5, alpha=0.8)
    ax.scatter([prob.customers[c].x for c in green_cids],
               [prob.customers[c].y for c in green_cids],
               c="#2ca25f", s=40, zorder=6, marker="*",
               label="绿色区客户")

    depot = prob.depot
    ax.plot(depot.x, depot.y, "s", color="#d73027", ms=11, zorder=7)

    from matplotlib.lines import Line2D
    legend_elems = [
        Line2D([0], [0], color="#2166ac", lw=2,   label="新能源车（EV）"),
        Line2D([0], [0], color="#d6604d", lw=1.2, label="燃油车"),
        Line2D([0], [0], marker="*", color="w",
               markerfacecolor="#2ca25f", markersize=12, label="绿色区客户"),
        Line2D([0], [0], marker="s", color="w",
               markerfacecolor="#d73027", markersize=10, label="配送中心"),
    ]
    ax.legend(handles=legend_elems, fontsize=9, loc="upper right")

    ax.set_aspect("equal")
    ax.set_title(f"{title}\n{sub}", fontsize=11)
    ax.set_xlabel("X (km)", fontsize=10)
    ax.set_ylabel("Y (km)", fontsize=10)
    ax.grid(True, alpha=0.25)

fig.suptitle("图3  Q1 vs Q2 配送路径对比：EV（蓝）与燃油车（红）",
             fontsize=13, y=1.01)
plt.tight_layout()
plt.savefig("figs/fig3_q1_vs_q2.png", dpi=150, bbox_inches="tight")
plt.close()
print("fig3 done")


# ────────────────────────────────────────────
# 图4  ALNS 收敛曲线（4个起点）
# ────────────────────────────────────────────
# 重新跑一个多起点的ALNS记录历史（从pkl取hist）
# result_q1里只有最优起点的hist，我们画它 + 说明

fig, axes = plt.subplots(1, 2, figsize=(13, 5))

best_costs    = hist1.best_costs
current_costs = hist1.current_costs
iters = range(len(best_costs))

# 左图：全局最优收敛
ax = axes[0]
ax.plot(iters, best_costs, color="#2166ac", lw=1.8, label="全局最优成本")
ax.fill_between(iters, best_costs,
                [max(best_costs)] * len(best_costs),
                alpha=0.08, color="#2166ac")
ax.axhline(min(best_costs), color="#d73027", lw=1.2,
           ls="--", label=f"最优值 {min(best_costs):,.0f} 元")
ax.set_xlabel("迭代次数", fontsize=11)
ax.set_ylabel("总成本（元）", fontsize=11)
ax.set_title("(a) 全局最优成本收敛曲线", fontsize=12)
ax.legend(fontsize=10)
ax.grid(True, alpha=0.25)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))

# 右图：当前解 vs 全局最优
ax = axes[1]
ax.plot(iters, current_costs, color="#d6604d", lw=0.8,
        alpha=0.6, label="当前解成本")
ax.plot(iters, best_costs,   color="#2166ac", lw=1.8,
        label="全局最优成本")

# 标注接受类型
accept = hist1.accept_types
best_iters    = [i for i, a in enumerate(accept) if a == "best"]
accepted_iters = [i for i, a in enumerate(accept) if a == "accepted"]

if best_iters:
    ax.scatter(best_iters,
               [best_costs[i] for i in best_iters],
               color="#1a9641", s=25, zorder=5, label="更新最优", marker="^")

ax.set_xlabel("迭代次数", fontsize=11)
ax.set_ylabel("总成本（元）", fontsize=11)
ax.set_title("(b) 当前解与全局最优对比", fontsize=12)
ax.legend(fontsize=10)
ax.grid(True, alpha=0.25)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))

fig.suptitle("图4  ALNS 算法收敛过程（问题一，最优起点）", fontsize=13)
plt.tight_layout()
plt.savefig("figs/fig4_alns_convergence.png", dpi=150, bbox_inches="tight")
plt.close()
print("fig4 done")

print("\n全部完成！图片保存在 figs/ 目录下：")
for f in sorted(os.listdir("figs")):
    path = os.path.join("figs", f)
    print(f"  {f}  ({os.path.getsize(path)//1024} KB)")
