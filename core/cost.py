"""
核心成本计算模块。
给定一条路径 + 一辆车，计算：
  1. 行驶时间（考虑速度时变）
  2. 能耗/电耗（考虑速度 U 型 + 载重系数）
  3. 各项成本（启动/能耗/碳排/时间窗）
"""
from dataclasses import dataclass
from typing import List, Tuple
import math

import numpy as np

from core.problem import (
    Problem, VehicleType, Customer,
    DEPART_TIME, SERVICE_TIME, SPEED_SEGMENTS,
    FUEL_PRICE, ELEC_PRICE, ETA_FUEL, ETA_ELEC, CARBON_PRICE,
    START_COST, EARLY_PENALTY, LATE_PENALTY,
    LOAD_FACTOR_FUEL, LOAD_FACTOR_ELEC,
    GREEN_BAN_START, GREEN_BAN_END, POLICY_PENALTY_PER_VIOLATION,
)


# ========= 基础函数 =========

def speed_at(t: float) -> float:
    """在时刻 t 的速度均值（km/h）。
    这里取均值而不是随机抽样，因为 ALNS 每次评估都要确定性。
    方差可以在论文里提到，用于敏感性分析时加入随机扰动。
    """
    for t0, t1, mu, _var in SPEED_SEGMENTS:
        if t0 <= t < t1:
            return mu
    # 时间超过 24 按最后一段
    return SPEED_SEGMENTS[-1][2]


def travel_time(dist_km: float, t_start: float) -> float:
    """从时刻 t_start 出发，跨段累加方式计算走完 dist_km 所需时间（h）。

    关键：速度时变意味着车辆跨段行驶时要分别用对应速度。
    算法：按时间段向前推进，一段一段消耗剩余距离，直到距离耗尽。
    """
    remain = dist_km
    t = t_start
    while remain > 1e-9:
        # 找到当前 t 所在的段
        seg_found = False
        for t0, t1, mu, _ in SPEED_SEGMENTS:
            if t0 <= t < t1:
                seg_end = t1
                v = mu
                seg_found = True
                break
        if not seg_found:
            # t >= 24, 用最后一段速度，无限延续
            v = SPEED_SEGMENTS[-1][2]
            seg_end = t + remain / v + 1e6

        # 本段最多能跑的时间
        avail_time = seg_end - t
        need_time = remain / v
        if need_time <= avail_time:
            t += need_time
            remain = 0.0
        else:
            remain -= v * avail_time
            t = seg_end
    return t - t_start


def travel_energy_per_km(
    v: float, is_electric: bool, load_frac: float
) -> float:
    """在速度 v 下、载重比例 load_frac 时，每公里能耗
    （燃油车：L/km；新能源：kWh/km）。

    基础公式（每百公里）：
      FPK = 0.0025 v^2 - 0.2554 v + 31.75 (L/100km)
      EPK = 0.0014 v^2 - 0.12 v + 36.19 (kWh/100km)
    载重系数:
      燃油车: 1 + 0.40 * load_frac
      新能源: 1 + 0.35 * load_frac
    """
    v = max(v, 1.0)  # 数值稳定
    if is_electric:
        base_per_100km = 0.0014 * v * v - 0.12 * v + 36.19
        load_coef = 1.0 + LOAD_FACTOR_ELEC * load_frac
    else:
        base_per_100km = 0.0025 * v * v - 0.2554 * v + 31.75
        load_coef = 1.0 + LOAD_FACTOR_FUEL * load_frac
    # 保证非负
    base_per_100km = max(base_per_100km, 1.0)
    return base_per_100km * load_coef / 100.0  # 每 km


def edge_energy_load_aware(
    dist_km: float, t_start: float,
    is_electric: bool, load_frac: float,
) -> float:
    """跨段累加能耗（L 或 kWh）。
    和 travel_time 并行的逻辑：按时间段分片，每段用该段速度算能耗。
    """
    remain = dist_km
    t = t_start
    energy = 0.0
    while remain > 1e-9:
        seg_found = False
        for t0, t1, mu, _ in SPEED_SEGMENTS:
            if t0 <= t < t1:
                seg_end = t1
                v = mu
                seg_found = True
                break
        if not seg_found:
            v = SPEED_SEGMENTS[-1][2]
            seg_end = t + remain / v + 1e6

        avail_time = seg_end - t
        need_time = remain / v
        seg_dist = min(remain, v * avail_time)
        energy += seg_dist * travel_energy_per_km(v, is_electric, load_frac)
        remain -= seg_dist
        if need_time <= avail_time:
            t += need_time
        else:
            t = seg_end
    return energy


# ========= 成本结构 =========

@dataclass
class RouteCost:
    """一条路径的完整成本分解。"""
    start_cost: float = 0.0
    energy_cost: float = 0.0   # 油费或电费
    carbon_cost: float = 0.0
    early_cost: float = 0.0
    late_cost: float = 0.0
    policy_cost: float = 0.0   # 问题2: 绿色区限行软罚项
    total_distance: float = 0.0
    total_time: float = 0.0
    total_load_kg: float = 0.0
    total_load_m3: float = 0.0
    energy_used: float = 0.0   # L 或 kWh
    carbon_kg: float = 0.0
    feasible: bool = True
    reason: str = ""           # 不可行原因
    policy_violations: int = 0  # 违反绿色区限行次数

    @property
    def total(self) -> float:
        return (self.start_cost + self.energy_cost + self.carbon_cost
                + self.early_cost + self.late_cost + self.policy_cost)

    def as_dict(self) -> dict:
        return dict(
            total=self.total,
            start_cost=self.start_cost,
            energy_cost=self.energy_cost,
            carbon_cost=self.carbon_cost,
            early_cost=self.early_cost,
            late_cost=self.late_cost,
            policy_cost=self.policy_cost,
            total_distance=self.total_distance,
            total_time=self.total_time,
            energy_used=self.energy_used,
            carbon_kg=self.carbon_kg,
            feasible=self.feasible,
            reason=self.reason,
            policy_violations=self.policy_violations,
        )


def _overlaps_ban(t_enter: float, t_exit: float) -> bool:
    """判断 [t_enter, t_exit] 是否与禁行时段 [8, 16] 重叠。"""
    return t_enter < GREEN_BAN_END and t_exit > GREEN_BAN_START


def evaluate_route(
    prob: Problem,
    vtype: VehicleType,
    route: List[int],
    demand_override: dict = None,  # SDVRP: {cid: 本车送的重量 kg}
    volume_override: dict = None,  # SDVRP: {cid: 本车送的体积 m³}
    check_feasibility: bool = True,
) -> RouteCost:
    """评估一条路径的总成本。

    route: [cid0, cid1, ...], 必须以 0 开始以 0 结束 (depot -> ... -> depot)
    demand_override/volume_override: 用于 SDVRP, 允许同一客户分多车配送。
        如果为 None, 则按完整需求计算。
    """
    rc = RouteCost()
    if len(route) < 2:
        rc.feasible = False
        rc.reason = "路径长度不足"
        return rc
    if route[0] != 0 or route[-1] != 0:
        rc.feasible = False
        rc.reason = "路径必须从 depot 出发并返回"
        return rc

    # --- 计算本车携带的总载重/体积，用于容量约束检查 ---
    visited = route[1:-1]  # 中间客户
    if demand_override is None:
        carry_kg = sum(prob.customers[c].demand_kg for c in visited)
    else:
        carry_kg = sum(demand_override.get(c, 0) for c in visited)
    if volume_override is None:
        carry_m3 = sum(prob.customers[c].demand_m3 for c in visited)
    else:
        carry_m3 = sum(volume_override.get(c, 0) for c in visited)

    rc.total_load_kg = carry_kg
    rc.total_load_m3 = carry_m3

    if check_feasibility:
        if carry_kg > vtype.capacity_kg + 1e-6:
            rc.feasible = False
            rc.reason = f"超重: {carry_kg:.1f} > {vtype.capacity_kg}"
            # 不立即 return, 继续算出成本用于诊断
        if carry_m3 > vtype.capacity_m3 + 1e-6:
            rc.feasible = False
            rc.reason = f"超体积: {carry_m3:.2f} > {vtype.capacity_m3}"

    # --- 启动成本 ---
    rc.start_cost = START_COST

    # --- 按顺序模拟行驶 ---
    t = DEPART_TIME
    # 车从 depot 出发时是满的（已装货），每次送完一个客户载重递减
    current_load_kg = carry_kg

    policy_active = getattr(prob, "policy_mode", "off") != "off"
    is_fuel = not vtype.is_electric

    for i in range(len(route) - 1):
        a, b = route[i], route[i + 1]
        d = prob.distance[a, b]
        rc.total_distance += d

        # 当前载重比例 (车上剩余货物 / 车额定载重)
        load_frac = min(current_load_kg / vtype.capacity_kg, 1.0) if vtype.capacity_kg > 0 else 0

        # 行驶时间和能耗
        dt = travel_time(d, t)
        e = edge_energy_load_aware(d, t, vtype.is_electric, load_frac)
        rc.energy_used += e
        t_arrival = t + dt

        # 到达 b（如果 b 不是 depot, 则有服务时间 + 时间窗惩罚）
        if b != 0:
            cust = prob.customers[b]
            # 时间窗惩罚
            if t_arrival < cust.tw_start:
                wait = cust.tw_start - t_arrival
                rc.early_cost += wait * EARLY_PENALTY
                # 车辆等到时间窗开始
                t_service_start = cust.tw_start
            elif t_arrival > cust.tw_end:
                late = t_arrival - cust.tw_end
                rc.late_cost += late * LATE_PENALTY
                t_service_start = t_arrival
            else:
                t_service_start = t_arrival

            # 服务后离开时间
            t_service_end = t_service_start + SERVICE_TIME

            # 政策约束: 燃油车在 [8, 16] 禁入绿色区
            # 一次违规 = 该客户在绿色区 && 服务区间与禁行窗口有重叠
            if (policy_active and is_fuel and cust.in_green_zone
                    and _overlaps_ban(t_service_start, t_service_end)):
                rc.policy_violations += 1
                if prob.policy_mode == "hard":
                    rc.feasible = False
                    if not rc.reason:
                        rc.reason = (
                            f"绿色区限行: 燃油车 {vtype.name} 于 "
                            f"{t_service_start:.2f}h 服务绿色区客户 c{b}")
                else:  # soft
                    rc.policy_cost += POLICY_PENALTY_PER_VIOLATION

            t = t_service_end

            # 卸货，载重减少
            if demand_override is None:
                delivered = cust.demand_kg
            else:
                delivered = demand_override.get(b, 0)
            current_load_kg = max(0, current_load_kg - delivered)
        else:
            # 返回 depot
            t = t_arrival

    rc.total_time = t - DEPART_TIME

    # 工作时间上限检查
    from core.problem import MAX_WORK_HOURS
    if check_feasibility and rc.total_time > MAX_WORK_HOURS + 1e-6:
        rc.feasible = False
        if not rc.reason:
            rc.reason = f"超时: {rc.total_time:.2f}h > {MAX_WORK_HOURS}h"

    # --- 能耗成本 + 碳排 ---
    if vtype.is_electric:
        rc.energy_cost = rc.energy_used * ELEC_PRICE
        rc.carbon_kg = rc.energy_used * ETA_ELEC
    else:
        rc.energy_cost = rc.energy_used * FUEL_PRICE
        rc.carbon_kg = rc.energy_used * ETA_FUEL
    rc.carbon_cost = rc.carbon_kg * CARBON_PRICE

    return rc


# ========= 自测 =========

if __name__ == "__main__":
    from core.data_loader import load_problem
    from core.problem import VEHICLE_TYPES

    prob = load_problem()

    # 测试 1: speed_at 各时段
    print("=== 速度分段测试 ===")
    for t in [7.5, 8.5, 9.5, 11.0, 12.0, 14.0, 16.0, 18.0]:
        print(f"  t={t}h, v={speed_at(t)} km/h")

    # 测试 2: 单边行驶时间 (7:30 出发, 跨段)
    print("\n=== 跨段行驶时间测试 ===")
    for d in [5.0, 50.0, 100.0]:
        dt = travel_time(d, 7.5)
        print(f"  {d} km from 7:30 → {dt:.3f}h (到 {7.5+dt:.2f}h)")

    # 测试 3: 一条简单路径的完整成本
    print("\n=== 路径评估测试 (depot → c2 → c3 → depot) ===")
    route = [0, 2, 3, 0]
    vtype = VEHICLE_TYPES[0]  # 3000kg 燃油车
    rc = evaluate_route(prob, vtype, route)
    print(f"  路径: {route}")
    print(f"  车型: {vtype.name}")
    for k, v in rc.as_dict().items():
        if isinstance(v, float):
            print(f"  {k}: {v:.3f}")
        else:
            print(f"  {k}: {v}")
