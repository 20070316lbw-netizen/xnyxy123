"""
螺旋初始解构造器 (Spiral Initial Solution Constructor)。

核心思想：
    把所有客户从极坐标 (r, θ) 的"盘状分布"拍成一条"阿基米德螺旋"上的一维顺序，
    然后按车辆容量把这条一维序列切成若干条路径。

阿基米德螺线方程: r(θ) = a + b · θ / (2π)
    - a: 起始半径（理论上 = depot 所在位置到极点的距离）
    - b: 圈距（每转一圈半径增量）
    - θ: 总累积角度（可 > 2π）

一个客户 (r_i, θ_i) 的螺旋顺序由以下"累积角度"给出:
    k_i = θ_i + 2π · ⌊(r_i - a) / b⌋    （等效于: 转了多少圈 + 当前圈的角度）

SDVRP 处理：
    若当前车装不下下一个客户，检查"剩余空间是否值得部分配送"：
        - 部分拆分: 用本车装下一部分，后续车送剩下
        - 整装留给下辆车: 当前车在此处结束并返 depot
    为避免过度拆分，只在车容量剩余 >= 10% 时才做拆分。

方向参数:
    clockwise: True=顺时针, False=逆时针
    outward: True=由内向外, False=由外向内
"""
from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np

from core.problem import Problem, VehicleType, VEHICLE_TYPES, GREEN_ZONE_CENTER
from core.solution import Solution, Route


# ========= 极坐标与螺旋序数 =========

def polar_around(x: float, y: float, cx: float, cy: float
                 ) -> Tuple[float, float]:
    """以 (cx, cy) 为原点的极坐标 (r, θ)；θ ∈ [0, 2π)。"""
    dx, dy = x - cx, y - cy
    r = math.hypot(dx, dy)
    theta = math.atan2(dy, dx)
    if theta < 0:
        theta += 2 * math.pi
    return r, theta


def spiral_order(
    prob: Problem,
    center: Tuple[float, float] = None,
    b: float = None,
    clockwise: bool = True,
    outward: bool = True,
) -> List[int]:
    """返回按螺旋顺序排列的客户 ID 列表。
    
    b: 螺距 (km)。默认用 (r_max-r_min) / num_rings 估计。
    
    核心思路：
        阿基米德螺线 r(θ_tot) = a + b·θ_tot/(2π), 其中 θ_tot 是累积角度（可>2π）。
        给定客户 (r, θ)，我们希望找到它在螺旋上的累积角度 θ_tot 使得:
            ① r ≈ a + b·θ_tot/(2π)   → "所在圈"
            ② θ_tot mod 2π = θ       → "当前圈的角位置"
        所以: θ_tot = 2π·(r - a)/b + (θ - 2π·(r - a)/b mod 2π)

        等价实现: 
            base_angle = 2π * (r - a) / b     # 对应半径r应位于哪个累积角度
            adjust = (θ - base_angle) mod 2π  # 对到实际θ的偏差
            θ_tot = base_angle + adjust       # 最终累积角
        
        这样保证相邻螺旋序的客户既角度接近又半径接近。
    """
    if center is None:
        center = (prob.depot.x, prob.depot.y)
    cx, cy = center

    customers = prob.customers[1:]
    polar = []
    for c in customers:
        r, th = polar_around(c.x, c.y, cx, cy)
        polar.append((c.cid, r, th))

    rs = [p[1] for p in polar]
    r_max = max(rs)
    r_min = min(rs)
    a = r_min  # 螺旋起始半径
    if b is None:
        num_rings = 4  # 期望 4 圈覆盖
        b = max((r_max - r_min) / num_rings, 1.0)

    result = []
    two_pi = 2 * math.pi
    for cid, r, th in polar:
        # 顺时针: 角度反向
        if clockwise:
            th_used = (two_pi - th) % two_pi
        else:
            th_used = th
        # 螺旋累积角度
        base_angle = two_pi * (r - a) / b
        adjust = (th_used - base_angle) % two_pi  # in [0, 2π)
        theta_tot = base_angle + adjust
        # 由外到内 = 反向排
        key = theta_tot if outward else -theta_tot
        result.append((key, cid, r, th))

    result.sort()
    return [cid for _, cid, _, _ in result]


# ========= 切分成路径 =========

def _pick_vehicle_type(
    demand_kg: float, demand_m3: float, available: dict
) -> VehicleType | None:
    """从剩余可用车型中挑一辆"最小能装下"的。

    挑选策略：载重足够 + 体积足够 + 剩余数量 > 0，选载重最小者。
    如果没有单车能整装这个需求，返回最大的车（后续做 SDVRP 拆分）。
    """
    # 先找能整装的
    feasible = [
        vt for vt in VEHICLE_TYPES
        if available[vt.type_id] > 0
        and vt.capacity_kg >= demand_kg
        and vt.capacity_m3 >= demand_m3
    ]
    if feasible:
        # 选载重最小且体积最紧的（利用率最高）
        return min(feasible, key=lambda v: (v.capacity_kg, v.capacity_m3))

    # 没有一辆能整装 → 返回当前可用的最大车
    candidates = [vt for vt in VEHICLE_TYPES if available[vt.type_id] > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda v: (v.capacity_kg, v.capacity_m3))


def spiral_construct(
    prob: Problem,
    center: Tuple[float, float] = None,
    clockwise: bool = True,
    outward: bool = True,
    allow_split: bool = True,
    look_ahead: int = 5,   # 当前客户塞不下时，往后看几个客户
) -> Solution:
    """螺旋构造初始解。

    策略：
        1) 计算螺旋序并展开为待分配客户队列
        2) 开一条路径，选车（第一个客户的需求决定）
        3) 按序列顺序尽量塞入客户；塞不下的允许拆分或跳过看后续
        4) 路径满或尝试失败后收尾
    """
    order = spiral_order(prob, center=center, clockwise=clockwise, outward=outward)
    # 跳过幽灵客户
    order = [cid for cid in order if prob.customers[cid].demand_kg > 0]

    available = {vt.type_id: vt.fleet_size for vt in VEHICLE_TYPES}
    remain_kg = {cid: prob.customers[cid].demand_kg for cid in order}
    remain_m3 = {cid: prob.customers[cid].demand_m3 for cid in order}

    sol = Solution()
    # 使用"位置指针 + skip 集合"模拟一个动态队列
    served = set()  # cid 完全配送完
    pos = 0
    MAX_ROUTES = 500
    route_count = 0

    while route_count < MAX_ROUTES:
        # 找到下一个未被完全服务的客户 (按螺旋序)
        while pos < len(order) and (order[pos] in served or remain_kg[order[pos]] <= 1e-6):
            pos += 1
        if pos >= len(order):
            break
        start_cid = order[pos]

        # 选车：按当前起点的需求（或与后面几个合计）
        vt = _pick_vehicle_type(remain_kg[start_cid], remain_m3[start_cid], available)
        if vt is None:
            break

        nodes = [0]
        deliv_kg: dict[int, float] = {}
        deliv_m3: dict[int, float] = {}
        used_kg = 0.0
        used_m3 = 0.0
        cap_kg = vt.capacity_kg
        cap_m3 = vt.capacity_m3

        # 从 pos 开始向后扫, 能塞就塞
        scan = pos
        miss_in_a_row = 0
        while scan < len(order) and miss_in_a_row < look_ahead:
            cid = order[scan]
            if cid in served or remain_kg[cid] <= 1e-6:
                scan += 1
                continue

            want_kg = remain_kg[cid]
            want_m3 = remain_m3[cid]
            left_kg = cap_kg - used_kg
            left_m3 = cap_m3 - used_m3

            if left_kg >= want_kg - 1e-6 and left_m3 >= want_m3 - 1e-6:
                # 整装
                nodes.append(cid)
                deliv_kg[cid] = want_kg
                deliv_m3[cid] = want_m3
                used_kg += want_kg
                used_m3 += want_m3
                remain_kg[cid] = 0.0
                remain_m3[cid] = 0.0
                served.add(cid)
                miss_in_a_row = 0
            else:
                # 装不下 - 如果剩余空间足够大, 允许拆分
                if allow_split and left_kg > 0.15 * cap_kg and left_m3 > 0.15 * cap_m3 and want_kg > 0:
                    kg_frac = left_kg / want_kg
                    m3_frac = left_m3 / want_m3 if want_m3 > 0 else 1
                    frac = min(kg_frac, m3_frac, 1.0)
                    if frac > 0.1:
                        take_kg = want_kg * frac
                        take_m3 = want_m3 * frac
                        nodes.append(cid)
                        deliv_kg[cid] = take_kg
                        deliv_m3[cid] = take_m3
                        used_kg += take_kg
                        used_m3 += take_m3
                        remain_kg[cid] -= take_kg
                        remain_m3[cid] -= take_m3
                        # 拆分后, 车基本满, 结束
                        break
                # 没拆, 跳过这个客户继续看后面
                miss_in_a_row += 1
            scan += 1

        # 路径收尾
        nodes.append(0)
        if len(nodes) > 2:
            sol.routes.append(Route(
                vtype=vt, nodes=nodes,
                delivered_kg=deliv_kg, delivered_m3=deliv_m3,
            ))
            available[vt.type_id] -= 1
            route_count += 1
        else:
            # 死路径, 强制前进
            pos = scan + 1
            continue

    return sol


# ========= 自测 =========

if __name__ == "__main__":
    from core.data_loader import load_problem
    from core.solution import solution_summary

    prob = load_problem()

    print("\n=== 螺旋构造 (顺时针, 由内到外) ===")
    sol_in = spiral_construct(prob, clockwise=True, outward=True)
    info = solution_summary(prob, sol_in)
    for k, v in info.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.2f}")
        else:
            print(f"  {k}: {v}")

    print("\n=== 螺旋构造 (顺时针, 由外到内) ===")
    sol_out = spiral_construct(prob, clockwise=True, outward=False)
    info = solution_summary(prob, sol_out)
    for k, v in info.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.2f}")
        else:
            print(f"  {k}: {v}")
