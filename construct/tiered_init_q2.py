"""
问题2 政策感知的分层构造器。

问题2 新增约束: 8:00-16:00 禁止燃油车进入绿色区。

构造策略:
    1. 先把"绿色区客户"用新能源车 (EV) 处理 —— 绿色区内客户优先消耗 EV 车队,
       这样燃油车根本不会进入绿色区, 从源头上避免违规.
    2. 非绿色区客户用剩余车队按问题1的方式分层构造.

绿色区客户规模 (扣除幽灵客户后) 约 12 个, EV 车队 25 辆 (10+15) 充足覆盖.
即便 EV 不够用, ALNS 阶段再把部分非绿色区小客户搭在燃油车上补偿也来得及.
"""
from __future__ import annotations

from typing import List, Tuple

from core.problem import Problem, VehicleType, VEHICLE_TYPES
from core.solution import Solution, Route
from core.cost import evaluate_route
from construct.tiered_init import (
    _build_big_customer_routes,
    _build_medium_customer_routes,
    _build_small_customer_routes,
    classify_customers,
)


def _pick_ev(available: dict, demand_kg: float, demand_m3: float) -> VehicleType | None:
    """只在新能源车里选最小能装下的一辆。"""
    evs = [v for v in VEHICLE_TYPES if v.is_electric and available[v.type_id] > 0]
    feas = [v for v in evs
            if v.capacity_kg >= demand_kg and v.capacity_m3 >= demand_m3]
    if feas:
        return min(feas, key=lambda v: (v.capacity_kg, v.capacity_m3))
    # 装不下 → 最大 EV (下游会做 SDVRP 拆分)
    return max(evs, key=lambda v: v.capacity_kg) if evs else None


def _build_green_ev_routes(
    prob: Problem, green_cids: List[int], available: dict,
) -> Tuple[List[Route], dict]:
    """为绿色区客户独占构造 EV 路径。

    策略: 每个绿色区客户独占一辆 EV (类似 medium 逻辑). 这样简单清晰,
    避免"EV 之间拼单时时间窗冲突"的复杂性。ALNS 后续可合并。
    大客户 (> 3000kg) 拆分, 剩余量也只用 EV。
    """
    routes = []
    for cid in green_cids:
        c = prob.customers[cid]
        rem_kg = c.demand_kg
        rem_m3 = c.demand_m3
        iter_cnt = 0
        while rem_kg > 1e-6 or rem_m3 > 1e-6:
            if iter_cnt > 20:
                break
            iter_cnt += 1
            vt = _pick_ev(available, rem_kg, rem_m3)
            if vt is None:
                # EV 用完, 剩余量只能让 ALNS 阶段处理
                break
            if rem_kg > vt.capacity_kg and rem_m3 > vt.capacity_m3:
                take_kg = vt.capacity_kg
                take_m3 = vt.capacity_m3
            elif rem_kg > vt.capacity_kg:
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


def tiered_construct_q2(
    prob: Problem,
    clockwise: bool = True,
    outward: bool = True,
) -> Solution:
    """问题2 政策感知构造:
        Step 1. 绿色区客户 → 优先 EV 独占
        Step 2. 非绿色区大/中/小客户 → 沿用问题1 分层构造
    """
    assert prob.policy_mode != "off", (
        "tiered_construct_q2 只在 policy_mode != 'off' 时使用"
    )

    available = {v.type_id: v.fleet_size for v in VEHICLE_TYPES}
    sol = Solution()

    # 分出绿色区与非绿色区有订单客户
    green_cids = []
    other_cids = []
    for c in prob.customers[1:]:
        if c.demand_kg <= 0:
            continue
        if c.in_green_zone:
            green_cids.append(c.cid)
        else:
            other_cids.append(c.cid)

    # Step 1: 绿色区 → EV
    green_routes, available = _build_green_ev_routes(prob, green_cids, available)
    sol.routes.extend(green_routes)

    # Step 2: 非绿色区按问题1分层, 但在"剩余 EV / 燃油车"池中选车
    # 复用 classify_customers 然后只取 other_cids 的分类
    big_all, med_all, small_all = classify_customers(prob)
    other_set = set(other_cids)
    big = [c for c in big_all if c in other_set]
    medium = [c for c in med_all if c in other_set]
    small = [c for c in small_all if c in other_set]

    # 非绿色区大/中/小客户: 沿用原有工具 (它会按剩余 available 选车,
    # 大客户会把燃油大车吃光, EV 剩下的留给小客户).
    # 注意: _build_big_customer_routes 会调 _pick_vehicle, 它不限制车型,
    # 所以燃油车 / EV 都能用.
    big_routes, available = _build_big_customer_routes(prob, big, available)
    sol.routes.extend(big_routes)

    med_routes, available = _build_medium_customer_routes(prob, medium, available)
    sol.routes.extend(med_routes)

    small_routes, available = _build_small_customer_routes(
        prob, small, available, clockwise=clockwise, outward=outward,
    )
    sol.routes.extend(small_routes)

    return sol


if __name__ == "__main__":
    from core.data_loader import load_problem
    from core.solution import evaluate_solution, solution_summary

    prob = load_problem()
    prob.policy_mode = "hard"

    sol = tiered_construct_q2(prob, clockwise=True, outward=True)
    info = solution_summary(prob, sol)
    print(f"=== Q2 政策感知构造 (policy=hard) ===")
    print(f"  路径数: {info['num_routes']}")
    print(f"  可行/不可行: {info['num_feasible']}/{info['num_infeasible']}")
    print(f"  政策违反: {info['policy_violations']}")
    print(f"  总成本: {info['total_cost']:.0f}")
    print(f"    - 启动: {info['start_cost']:.0f}")
    print(f"    - 能耗: {info['energy_cost']:.0f}")
    print(f"    - 碳排: {info['carbon_cost']:.0f}")
    print(f"    - 早到: {info['early_cost']:.0f}")
    print(f"    - 晚到: {info['late_cost']:.0f}")
    print(f"  EV 路径: {info['ev_routes']}; 燃油路径: {info['fuel_routes']}")
    print(f"  车型分布: {info['type_used']}")
