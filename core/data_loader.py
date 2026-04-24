"""
读取附件数据并组装为 Problem 对象。

附件:
  客户坐标信息.xlsx:  ID, 类型, X(km), Y(km)
  距离矩阵.xlsx:       99x99 距离矩阵 (km)
  订单信息.xlsx:       订单编号, 目标客户编号, 重量, 体积
  时间窗.xlsx:         客户编号, 开始时间, 结束时间
"""

import numpy as np
import pandas as pd
from pathlib import Path

from core.problem import Customer, Problem, GREEN_ZONE_CENTER, GREEN_ZONE_RADIUS


DATA_DIR = None  # 延迟初始化


def _find_data_dir() -> Path:
    """在多个可能位置查找数据目录, 返回第一个存在的。

    查找顺序:
        1. 环境变量 VRP_DATA_DIR
        2. 项目根目录下 data/  (推荐放置位置)
        3. 脚本当前目录下 data/
        4. /mnt/user-data/uploads/ (Claude 环境)
    """
    import os

    # 1. 环境变量
    env_dir = os.environ.get("VRP_DATA_DIR")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir)

    # 2. 项目根 data/
    # __file__ = core/data_loader.py → 根是 parent.parent
    project_root_data = Path(__file__).resolve().parent.parent / "data"
    if project_root_data.exists():
        return project_root_data

    # 3. 当前工作目录 data/
    cwd_data = Path.cwd() / "data"
    if cwd_data.exists():
        return cwd_data

    # 4. Claude 默认路径
    claude_path = Path("/mnt/user-data/uploads")
    if claude_path.exists():
        return claude_path

    raise FileNotFoundError(
        "找不到数据目录。请把 4 个 xlsx 放在以下任一位置:\n"
        f"  1. 项目根下的 data/ 目录: {project_root_data}\n"
        f"  2. 当前目录下的 data/: {cwd_data}\n"
        f"  3. 或设置环境变量 VRP_DATA_DIR=/path/to/data"
    )


def _time_to_hour(s: str) -> float:
    """'HH:MM' -> 小时浮点数。"""
    h, m = map(int, s.split(":"))
    return h + m / 60


def load_problem() -> Problem:
    """读所有附件并组装 Problem。"""
    data_dir = _find_data_dir()
    # --- 坐标 ---
    coords = pd.read_excel(data_dir / "客户坐标信息.xlsx")
    coords = coords.rename(columns={"X (km)": "X", "Y (km)": "Y"})
    # 第一行应当是配送中心 (ID=0, type='配送中心')
    assert coords.iloc[0]["类型"] != "客户", "第一行应为配送中心"

    # --- 距离矩阵 ---
    dist_df = pd.read_excel(data_dir / "距离矩阵.xlsx", index_col=0)
    distance = dist_df.values.astype(np.float64)
    assert distance.shape == (99, 99), f"距离矩阵形状异常: {distance.shape}"

    # --- 时间窗 ---
    tw = pd.read_excel(data_dir / "时间窗.xlsx")
    # 客户编号 -> (tw_start, tw_end)
    tw_map = {}
    for _, row in tw.iterrows():
        cid = int(row["客户编号"])
        tw_map[cid] = (
            _time_to_hour(str(row["开始时间"])),
            _time_to_hour(str(row["结束时间"])),
        )

    # --- 订单聚合 ---
    orders = pd.read_excel(data_dir / "订单信息.xlsx")
    agg = (
        orders.groupby("目标客户编号")
        .agg(
            demand_kg=("重量", "sum"),
            demand_m3=("体积", "sum"),
        )
        .reset_index()
    )
    demand_map = {
        int(row["目标客户编号"]): (row["demand_kg"], row["demand_m3"])
        for _, row in agg.iterrows()
    }

    # --- 组装 Customer 列表 ---
    # 注意: 坐标表中 ID 从 0 开始 (配送中心), 客户 ID 从 1 开始
    customers: list[Customer] = []
    for _, row in coords.iterrows():
        cid = int(row["ID"])
        x, y = float(row["X"]), float(row["Y"])
        if cid == 0:
            # 配送中心：无需求、无时间窗（给 0 和 24 占位）
            customers.append(
                Customer(
                    cid=0,
                    x=x,
                    y=y,
                    demand_kg=0.0,
                    demand_m3=0.0,
                    tw_start=0.0,
                    tw_end=24.0,
                    in_green_zone=_dist_to_center(x, y) <= GREEN_ZONE_RADIUS,
                )
            )
        else:
            dk, dm = demand_map.get(cid, (0.0, 0.0))
            ts, te = tw_map.get(cid, (0.0, 24.0))
            customers.append(
                Customer(
                    cid=cid,
                    x=x,
                    y=y,
                    demand_kg=float(dk),
                    demand_m3=float(dm),
                    tw_start=ts,
                    tw_end=te,
                    in_green_zone=_dist_to_center(x, y) <= GREEN_ZONE_RADIUS,
                )
            )

    return Problem(customers=customers, distance=distance)


def _dist_to_center(x: float, y: float) -> float:
    cx, cy = GREEN_ZONE_CENTER
    return float(np.hypot(x - cx, y - cy))


def summary(prob: Problem) -> dict:
    """打印问题基本统计。"""
    n = prob.n_customers
    green = sum(1 for c in prob.customers[1:] if c.in_green_zone)
    has_demand = sum(1 for c in prob.customers[1:] if c.demand_kg > 0)
    total_kg = sum(c.demand_kg for c in prob.customers[1:])
    total_m3 = sum(c.demand_m3 for c in prob.customers[1:])

    info = {
        "n_customers": n,
        "in_green_zone": green,
        "有订单客户": has_demand,
        "幽灵客户": n - has_demand,
        "总重量_t": total_kg / 1000,
        "总体积_m3": total_m3,
        "最大单客户重量_kg": max(c.demand_kg for c in prob.customers[1:]),
        "最大单客户体积_m3": max(c.demand_m3 for c in prob.customers[1:]),
        "depot坐标": (prob.depot.x, prob.depot.y),
    }
    return info


if __name__ == "__main__":
    prob = load_problem()
    info = summary(prob)
    print("问题实例摘要:")
    for k, v in info.items():
        print(f"  {k}: {v}")

    print(f"\n距离矩阵对称性: {np.allclose(prob.distance, prob.distance.T)}")
    print(f"距离矩阵对角线全0: {(np.diag(prob.distance) == 0).all()}")
    print(f"\n前 3 个客户:")
    for c in prob.customers[:4]:
        print(f"  {c}")
