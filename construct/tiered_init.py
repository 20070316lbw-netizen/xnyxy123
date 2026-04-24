"""
分层初始解构造器 (Tiered Construction)。

核心思路:
    将客户按需求规模分成三层, 分别处理:
    
    1. 大客户 (需求 > 单车容量): 
       必须拆分到多辆车。每辆车满载跑一趟送这个客户, 剩余空间可接收小客户。
    
    2. 中客户 (占车容量 50%-100%):
       独占一辆车, 因为和其他客户合并后利用率很低。
    
    3. 小客户 (占车容量 < 50%):
       按螺旋序排列, 贪心合并到同一辆车, 时间窗冲突时切分。

这样做的好处:
    - 大客户的路径在构造时就"固定"了结构 (每车满载), 后续 ALNS 不需要花时间改它们
    - 中客户的"独占"也是最优的 (合并会降低利用率)
    - ALNS 真正的优化空间集中在小客户合并
"""
from __future__ import annotations

import math
from typing import List, Tuple

from core.problem import Problem, VehicleType, VEHICLE_TYPES
from core.solution import Solution, Route
from construct.spiral_init import spiral_order, polar_around
from core.cost import evaluate_route


# 分类阈值
BIG_KG_THRESHOLD_FACTOR = 1.0   # > 车最大容量的 100% → 必须拆
BIG_M3_THRESHOLD_FACTOR = 1.0
MEDIUM_THRESHOLD_FACTOR = 0.5   # > 50% → 独占一辆中小车


def classify_customers(prob: Problem) -> Tuple[List[int], List[int], List[int]]:
    """返回 (big_cids, medium_cids, small_cids)。幽灵客户被排除。"""
    # 用最大车作为基准
    max_kg = max(v.capacity_kg for v in VEHICLE_TYPES)
    max_m3 = max(v.capacity_m3 for v in VEHICLE_TYPES)

    big, medium, small = [], [], []
    for c in prob.customers[1:]:
        if c.demand_kg <= 0:
            continue
        if c.demand_kg > max_kg * BIG_KG_THRESHOLD_FACTOR or c.demand_m3 > max_m3 * BIG_M3_THRESHOLD_FACTOR:
            big.append(c.cid)
        elif c.demand_kg > max_kg * MEDIUM_THRESHOLD_FACTOR or c.demand_m3 > max_m3 * MEDIUM_THRESHOLD_FACTOR:
            medium.append(c.cid)
        else:
            small.append(c.cid)
    return big, medium, small


def _pick_vehicle(demand_kg: float, demand_m3: float, available: dict) -> VehicleType | None:
    """选一辆能装下的最小车; 装不下则返最大可用车。"""
    feas = [v for v in VEHICLE_TYPES
            if available[v.type_id] > 0
            and v.capacity_kg >= demand_kg
            and v.capacity_m3 >= demand_m3]
    if feas:
        return min(feas, key=lambda v: (v.capacity_kg, v.capacity_m3))
    cands = [v for v in VEHICLE_TYPES if available[v.type_id] > 0]
    return max(cands, key=lambda v: v.capacity_kg) if cands else None


def _build_big_customer_routes(
    prob: Problem, big_cids: List[int], available: dict,
) -> Tuple[List[Route], dict]:
    """为每个大客户构造多条路径 (SDVRP 拆分)。
    策略: 满载大车跑 N-1 车, 剩余量尝试用更小的车 (节约大车给中客户)。"""
    routes = []
    for cid in big_cids:
        c = prob.customers[cid]
        rem_kg = c.demand_kg
        rem_m3 = c.demand_m3
        iter_cnt = 0
        while rem_kg > 1e-6 or rem_m3 > 1e-6:
            if iter_cnt > 20:
                break
            iter_cnt += 1

            # 关键改进: 如果剩余量 ≤ 小车容量, 直接用小车
            # 这样节约大车给中客户
            vt = _pick_vehicle(rem_kg, rem_m3, available)
            if vt is None:
                break

            take_kg = min(rem_kg, vt.capacity_kg)
            take_m3 = min(rem_m3, vt.capacity_m3)
            # 如果两维度都超, 按"更紧"的一维按比例取
            if rem_kg > vt.capacity_kg and rem_m3 > vt.capacity_m3:
                # 都超, 只能各取最大 (相当于物理上装满,可能一维浪费)
                take_kg = vt.capacity_kg
                take_m3 = vt.capacity_m3
            elif rem_kg > vt.capacity_kg:
                # 重量紧, 体积按比例
                frac = vt.capacity_kg / rem_kg
                take_kg = vt.capacity_kg
                take_m3 = rem_m3 * frac
            elif rem_m3 > vt.capacity_m3:
                frac = vt.capacity_m3 / rem_m3
                take_m3 = vt.capacity_m3
                take_kg = rem_kg * frac
            else:
                take_kg = rem_kg
                take_m3 = rem_m3

            routes.append(Route(
                vtype=vt, nodes=[0, cid, 0],
                delivered_kg={cid: take_kg}, delivered_m3={cid: take_m3},
            ))
            available[vt.type_id] -= 1
            rem_kg -= take_kg
            rem_m3 -= take_m3

    return routes, available


def _build_medium_customer_routes(
    prob: Problem, medium_cids: List[int], available: dict,
) -> Tuple[List[Route], dict]:
    """每个中客户独占一辆车; 若大车用完, 允许拆分到多辆小车。"""
    routes = []
    for cid in medium_cids:
        c = prob.customers[cid]
        rem_kg = c.demand_kg
        rem_m3 = c.demand_m3
        while rem_kg > 1e-6 or rem_m3 > 1e-6:
            vt = _pick_vehicle(rem_kg, rem_m3, available)
            if vt is None:
                break
            # 实际装量
            take_kg = min(rem_kg, vt.capacity_kg)
            take_m3 = min(rem_m3, vt.capacity_m3)
            if rem_kg > vt.capacity_kg:
                # 车装不下全部, 按比例拆
                frac = vt.capacity_kg / rem_kg
                take_kg = vt.capacity_kg
                take_m3 = rem_m3 * frac
            elif rem_m3 > vt.capacity_m3:
                frac = vt.capacity_m3 / rem_m3
                take_m3 = vt.capacity_m3
                take_kg = rem_kg * frac
            else:
                take_kg = rem_kg
                take_m3 = rem_m3
            routes.append(Route(
                vtype=vt, nodes=[0, cid, 0],
                delivered_kg={cid: take_kg}, delivered_m3={cid: take_m3},
            ))
            available[vt.type_id] -= 1
            rem_kg -= take_kg
            rem_m3 -= take_m3
    return routes, available


def _build_small_customer_routes(
    prob: Problem, small_cids: List[int], available: dict,
    clockwise: bool = True, outward: bool = True,
) -> Tuple[List[Route], dict]:
    """小客户按螺旋序合并到同一路径。每条路径:
       1. 按螺旋序从头取客户
       2. 塞车容量 (同时检查时间窗不冲突过于严重)
    """
    # 子集化: 只对小客户计算螺旋序
    full_order = spiral_order(prob, clockwise=clockwise, outward=outward)
    small_set = set(small_cids)
    order = [cid for cid in full_order if cid in small_set]

    routes = []
    served = set()
    pos = 0
    MAX_ROUTES = 100

    while pos < len(order) and len(routes) < MAX_ROUTES:
        while pos < len(order) and order[pos] in served:
            pos += 1
        if pos >= len(order):
            break
        start_cid = order[pos]
        start_c = prob.customers[start_cid]

        # 选车 (先按起始客户需求, 优先小车)
        vt = _pick_vehicle(start_c.demand_kg, start_c.demand_m3, available)
        if vt is None:
            break

        # 构造路径
        nodes = [0, start_cid]
        deliv_kg = {start_cid: start_c.demand_kg}
        deliv_m3 = {start_cid: start_c.demand_m3}
        used_kg = start_c.demand_kg
        used_m3 = start_c.demand_m3
        served.add(start_cid)
        pos += 1

        # 向后尝试加入更多客户
        scan = pos
        miss_in_a_row = 0
        LOOK_AHEAD = 8
        while scan < len(order) and miss_in_a_row < LOOK_AHEAD:
            cid = order[scan]
            if cid in served:
                scan += 1
                continue
            c = prob.customers[cid]
            if (used_kg + c.demand_kg > vt.capacity_kg + 1e-6
                or used_m3 + c.demand_m3 > vt.capacity_m3 + 1e-6):
                miss_in_a_row += 1
                scan += 1
                continue

            # 尝试插入到路径最合适的位置
            trial_cids = nodes[1:] + [cid]  # 末尾先试
            trial_nodes = [0] + trial_cids + [0]
            trial_dk = {**deliv_kg, cid: c.demand_kg}
            trial_dm = {**deliv_m3, cid: c.demand_m3}
            rc = evaluate_route(prob, vt, trial_nodes,
                                 demand_override=trial_dk, volume_override=trial_dm,
                                 check_feasibility=True)
            if rc.feasible:
                nodes = [0] + trial_cids
                deliv_kg = trial_dk
                deliv_m3 = trial_dm
                used_kg += c.demand_kg
                used_m3 += c.demand_m3
                served.add(cid)
                miss_in_a_row = 0
            else:
                # 尝试按时间窗插入到路径中部
                best_pos = -1
                best_cost = math.inf
                for ins in range(1, len(nodes) + 1):
                    test_cids = nodes[1:ins] + [cid] + nodes[ins:]
                    test_nodes = [0] + test_cids + [0]
                    rc2 = evaluate_route(prob, vt, test_nodes,
                                          demand_override=trial_dk,
                                          volume_override=trial_dm,
                                          check_feasibility=True)
                    if rc2.feasible and rc2.total < best_cost:
                        best_cost = rc2.total
                        best_pos = ins
                if best_pos > 0:
                    nodes = [0] + nodes[1:best_pos] + [cid] + nodes[best_pos:]
                    deliv_kg = trial_dk
                    deliv_m3 = trial_dm
                    used_kg += c.demand_kg
                    used_m3 += c.demand_m3
                    served.add(cid)
                    miss_in_a_row = 0
                else:
                    miss_in_a_row += 1
            scan += 1

        # 收尾
        nodes.append(0)
        routes.append(Route(
            vtype=vt, nodes=nodes,
            delivered_kg=deliv_kg, delivered_m3=deliv_m3,
        ))
        available[vt.type_id] -= 1

    return routes, available


def tiered_construct(
    prob: Problem,
    clockwise: bool = True,
    outward: bool = True,
) -> Solution:
    """分层构造完整初始解。"""
    big, medium, small = classify_customers(prob)

    available = {v.type_id: v.fleet_size for v in VEHICLE_TYPES}
    sol = Solution()

    # 1. 大客户 (优先用大车, 每车满载)
    big_routes, available = _build_big_customer_routes(prob, big, available)
    sol.routes.extend(big_routes)

    # 2. 中客户 (独占)
    med_routes, available = _build_medium_customer_routes(prob, medium, available)
    sol.routes.extend(med_routes)

    # 3. 小客户 (螺旋合并)
    small_routes, available = _build_small_customer_routes(
        prob, small, available, clockwise=clockwise, outward=outward,
    )
    sol.routes.extend(small_routes)

    return sol


if __name__ == "__main__":
    from core.data_loader import load_problem
    from core.solution import evaluate_solution, solution_summary

    prob = load_problem()
    big, medium, small = classify_customers(prob)
    print(f"客户分类: 大={len(big)}, 中={len(medium)}, 小={len(small)}")

    for direction in [(True, True, "顺时针 由内到外"),
                       (True, False, "顺时针 由外到内")]:
        cw, out, name = direction
        sol = tiered_construct(prob, clockwise=cw, outward=out)
        info = solution_summary(prob, sol)
        print(f"\n=== {name} ===")
        print(f"  路径数: {info['num_routes']}")
        print(f"  可行: {info['num_feasible']}, 不可行: {info['num_infeasible']}")
        print(f"  总成本: {info['total_cost']:.0f}")
        print(f"    - 启动: {info['start_cost']:.0f}")
        print(f"    - 能耗: {info['energy_cost']:.0f}")
        print(f"    - 碳排: {info['carbon_cost']:.0f}")
        print(f"    - 早到: {info['early_cost']:.0f}")
        print(f"    - 晚到: {info['late_cost']:.0f}")
        print(f"  类型分布: {info['type_used']}")
