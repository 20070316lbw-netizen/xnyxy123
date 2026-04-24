"""
生成初始解的可视化 SVG / matplotlib 图。
"""
import matplotlib
matplotlib.use("Agg")  # 非交互后端
import matplotlib.pyplot as plt
import numpy as np

from core.data_loader import load_problem
from construct.spiral_init import spiral_construct, spiral_order
from core.problem import GREEN_ZONE_CENTER, GREEN_ZONE_RADIUS

plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def _color_for_route(idx: int) -> tuple:
    cmap = plt.cm.tab20
    return cmap(idx % 20)


def plot_routes(prob, sol, title: str, fname: str):
    fig, ax = plt.subplots(figsize=(10, 9))

    # 绿色区圆
    gx, gy = GREEN_ZONE_CENTER
    circle = plt.Circle((gx, gy), GREEN_ZONE_RADIUS, color='green', alpha=0.08, zorder=0)
    ax.add_patch(circle)
    ax.plot(gx, gy, '+', color='green', markersize=15, zorder=2, label='City Center')

    # 所有客户点
    cx = [c.x for c in prob.customers[1:]]
    cy = [c.y for c in prob.customers[1:]]
    ax.scatter(cx, cy, c='lightgray', s=15, zorder=1)

    # 每条路径
    for i, r in enumerate(sol.routes):
        color = _color_for_route(i)
        xs = [prob.customers[c].x for c in r.nodes]
        ys = [prob.customers[c].y for c in r.nodes]
        ax.plot(xs, ys, '-', color=color, alpha=0.5, linewidth=0.8, zorder=3)

    # 配送中心
    depot = prob.depot
    ax.plot(depot.x, depot.y, 's', color='red', markersize=12, zorder=5, label='Depot')

    ax.set_aspect('equal')
    ax.set_title(f"{title}\n{len(sol.routes)} routes")
    ax.set_xlabel("X (km)")
    ax.set_ylabel("Y (km)")
    ax.legend(loc='best', fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(fname, dpi=110, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")


def plot_spiral_order(prob, order, title: str, fname: str):
    """画出螺旋序号 + 客户点，用颜色表示顺序（从深到浅）。"""
    fig, ax = plt.subplots(figsize=(10, 9))
    gx, gy = GREEN_ZONE_CENTER
    circle = plt.Circle((gx, gy), GREEN_ZONE_RADIUS, color='green', alpha=0.08, zorder=0)
    ax.add_patch(circle)
    ax.plot(gx, gy, '+', color='green', markersize=15, zorder=2)

    # 只对有订单的客户排螺旋序
    colors = plt.cm.viridis(np.linspace(0, 1, len(order)))
    for i, cid in enumerate(order):
        c = prob.customers[cid]
        ax.scatter(c.x, c.y, c=[colors[i]], s=30, zorder=3)

    # 螺旋连线
    xs = [prob.customers[cid].x for cid in order]
    ys = [prob.customers[cid].y for cid in order]
    ax.plot(xs, ys, '-', color='royalblue', alpha=0.3, linewidth=0.8, zorder=2)

    depot = prob.depot
    ax.plot(depot.x, depot.y, 's', color='red', markersize=12, zorder=5, label='Depot')
    ax.set_aspect('equal')
    ax.set_title(title)
    ax.set_xlabel("X (km)")
    ax.set_ylabel("Y (km)")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(fname, dpi=110, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")


if __name__ == "__main__":
    import os
    os.makedirs("/home/claude/vrp/figs", exist_ok=True)
    prob = load_problem()

    # 1. 螺旋序本身（由内到外）
    order_in = [cid for cid in spiral_order(prob, clockwise=True, outward=True)
                if prob.customers[cid].demand_kg > 0]
    plot_spiral_order(prob, order_in,
                      title="Spiral Order (CW, Inside-Out)",
                      fname="/home/claude/vrp/figs/spiral_order_inout.png")

    order_out = [cid for cid in spiral_order(prob, clockwise=True, outward=False)
                 if prob.customers[cid].demand_kg > 0]
    plot_spiral_order(prob, order_out,
                      title="Spiral Order (CW, Outside-In)",
                      fname="/home/claude/vrp/figs/spiral_order_outin.png")

    # 2. 初始解路径图
    sol_in = spiral_construct(prob, clockwise=True, outward=True)
    plot_routes(prob, sol_in,
                title="Initial Solution (Spiral Inside-Out)",
                fname="/home/claude/vrp/figs/init_sol_inout.png")

    sol_out = spiral_construct(prob, clockwise=True, outward=False)
    plot_routes(prob, sol_out,
                title="Initial Solution (Spiral Outside-In)",
                fname="/home/claude/vrp/figs/init_sol_outin.png")
