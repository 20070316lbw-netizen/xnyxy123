"""
解的表示 (Solution)：
    一个解 = 若干条路径 + 每条路径对应的车型
    每条路径 = [0, c1, c2, ..., 0]
    对 SDVRP 支持: 每条路径可附带 {cid: delivered_kg/m3} 记录该车对客户的配送量
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.problem import Problem, VehicleType
from core.cost import RouteCost, evaluate_route


@dataclass
class Route:
    """单条路径。"""
    vtype: VehicleType
    nodes: List[int]                 # [0, ..., 0]
    delivered_kg: Dict[int, float] = field(default_factory=dict)  # 本车对客户的配送量 (SDVRP)
    delivered_m3: Dict[int, float] = field(default_factory=dict)

    def customers(self) -> List[int]:
        return self.nodes[1:-1]


@dataclass
class Solution:
    routes: List[Route] = field(default_factory=list)
    unassigned: List[int] = field(default_factory=list)  # ALNS 破坏后池中的客户

    def num_routes(self) -> int:
        return len(self.routes)


def evaluate_solution(prob: Problem, sol: Solution) -> Tuple[float, List[RouteCost]]:
    """返回总成本和每条路径的详细成本。

    SDVRP: 需求由 route.delivered_kg/m3 控制; 若缺失就按客户全量计算。
    """
    total = 0.0
    details: List[RouteCost] = []
    for r in sol.routes:
        # 如果有 delivered override, 把 override 传给 evaluate_route
        dk = r.delivered_kg if r.delivered_kg else None
        dv = r.delivered_m3 if r.delivered_m3 else None
        rc = evaluate_route(prob, r.vtype, r.nodes,
                             demand_override=dk, volume_override=dv)
        details.append(rc)
        total += rc.total
    return total, details


def solution_summary(prob: Problem, sol: Solution) -> dict:
    total, details = evaluate_solution(prob, sol)
    n_feasible = sum(1 for d in details if d.feasible)
    n_infeas = len(details) - n_feasible
    total_dist = sum(d.total_distance for d in details)
    total_start = sum(d.start_cost for d in details)
    total_energy = sum(d.energy_cost for d in details)
    total_carbon = sum(d.carbon_cost for d in details)
    total_early = sum(d.early_cost for d in details)
    total_late = sum(d.late_cost for d in details)
    total_policy = sum(d.policy_cost for d in details)
    total_violations = sum(d.policy_violations for d in details)
    total_co2 = sum(d.carbon_kg for d in details)

    # 车辆类型统计
    type_used = {}
    ev_routes = 0
    fuel_routes = 0
    for r in sol.routes:
        type_used[r.vtype.name] = type_used.get(r.vtype.name, 0) + 1
        if r.vtype.is_electric:
            ev_routes += 1
        else:
            fuel_routes += 1

    return dict(
        total_cost=total,
        num_routes=len(sol.routes),
        num_feasible=n_feasible,
        num_infeasible=n_infeas,
        unassigned=len(sol.unassigned),
        total_distance_km=total_dist,
        start_cost=total_start,
        energy_cost=total_energy,
        carbon_cost=total_carbon,
        early_cost=total_early,
        late_cost=total_late,
        policy_cost=total_policy,
        policy_violations=total_violations,
        carbon_kg=total_co2,
        type_used=type_used,
        ev_routes=ev_routes,
        fuel_routes=fuel_routes,
    )


if __name__ == "__main__":
    from core.data_loader import load_problem
    from core.problem import VEHICLE_TYPES
    prob = load_problem()
    # 测试: 一个空解 + 单条路径
    sol = Solution()
    sol.routes.append(Route(vtype=VEHICLE_TYPES[0], nodes=[0, 3, 2, 0]))
    info = solution_summary(prob, sol)
    for k, v in info.items():
        print(f"{k}: {v}")
