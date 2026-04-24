"""
初始解修复与精加工工具:
    repair_infeasible_routes: 把超时/超载的路径拆成多条可行路径
    try_merge_routes: 尝试合并两条短路径
"""
from __future__ import annotations

from copy import deepcopy
from typing import List

from core.problem import Problem, VehicleType, VEHICLE_TYPES
from core.solution import Solution, Route
from core.cost import evaluate_route


def _pick_vehicle_for(demand_kg: float, demand_m3: float, available: dict) -> VehicleType | None:
    feas = [v for v in VEHICLE_TYPES
            if available[v.type_id] > 0
            and v.capacity_kg >= demand_kg
            and v.capacity_m3 >= demand_m3]
    if feas:
        return min(feas, key=lambda v: v.capacity_kg)
    cands = [v for v in VEHICLE_TYPES if available[v.type_id] > 0]
    return max(cands, key=lambda v: v.capacity_kg) if cands else None


def _compute_available(sol: Solution) -> dict:
    used = {v.type_id: 0 for v in VEHICLE_TYPES}
    for r in sol.routes:
        used[r.vtype.type_id] += 1
    return {v.type_id: v.fleet_size - used[v.type_id] for v in VEHICLE_TYPES}


def sort_routes_by_tw(prob: Problem, sol: Solution) -> Solution:
    """对每条路径, 内部按时间窗起点排序。这是一个便宜且通常改善的简单 heuristic。
    如果排序后路径可行, 接受; 如果不行, 保留原路径。"""
    out = Solution()
    for r in sol.routes:
        cids = r.customers()
        if len(cids) <= 1:
            out.routes.append(r)
            continue
        cids_sorted = sorted(cids, key=lambda c: prob.customers[c].tw_start)
        sorted_nodes = [0] + cids_sorted + [0]

        # 比较原路径和排序后的成本
        rc_orig = evaluate_route(prob, r.vtype, r.nodes,
                                  demand_override=r.delivered_kg,
                                  volume_override=r.delivered_m3,
                                  check_feasibility=True)
        rc_sort = evaluate_route(prob, r.vtype, sorted_nodes,
                                  demand_override=r.delivered_kg,
                                  volume_override=r.delivered_m3,
                                  check_feasibility=True)
        # 选更好的 (优先可行; 都可行就选成本低)
        if rc_sort.feasible and (not rc_orig.feasible or rc_sort.total < rc_orig.total):
            out.routes.append(Route(
                vtype=r.vtype, nodes=sorted_nodes,
                delivered_kg=dict(r.delivered_kg), delivered_m3=dict(r.delivered_m3),
            ))
        else:
            out.routes.append(r)
    return out


def repair_infeasible_routes(prob: Problem, sol: Solution) -> Solution:
    """对每条路径, 尝试按时间窗起点排序; 排序后若仍不可行, 尝试逐客户构造可行子路径。"""
    new_routes: List[Route] = []
    for r in sol.routes:
        rc_orig = evaluate_route(
            prob, r.vtype, r.nodes,
            demand_override=r.delivered_kg, volume_override=r.delivered_m3,
            check_feasibility=True,
        )
        if rc_orig.feasible:
            new_routes.append(r)
            continue

        # 尝试 1: 按时间窗起点排序
        cids = r.nodes[1:-1]
        cids_sorted = sorted(cids, key=lambda c: prob.customers[c].tw_start)
        sorted_nodes = [0] + cids_sorted + [0]
        rc_sorted = evaluate_route(
            prob, r.vtype, sorted_nodes,
            demand_override=r.delivered_kg, volume_override=r.delivered_m3,
            check_feasibility=True,
        )
        if rc_sorted.feasible:
            new_routes.append(Route(
                vtype=r.vtype, nodes=sorted_nodes,
                delivered_kg=dict(r.delivered_kg), delivered_m3=dict(r.delivered_m3),
            ))
            continue

        # 尝试 2: 按时间窗排序 + 贪心截断 (超时就切)
        chunks = _greedy_split_by_time(prob, r.vtype, cids_sorted,
                                         r.delivered_kg, r.delivered_m3)
        for chunk_nodes, chunk_dk, chunk_dm in chunks:
            new_routes.append(Route(
                vtype=r.vtype, nodes=chunk_nodes,
                delivered_kg=chunk_dk, delivered_m3=chunk_dm,
            ))

    out = Solution()
    out.routes = new_routes
    return out


def _greedy_split_by_time(prob, vtype, cids_sorted, full_dk, full_dm):
    """从前往后逐客户加入, 每次失败就切断, 开新路径。"""
    chunks = []
    curr_cids = []
    curr_dk = {}
    curr_dm = {}

    def build_nodes(lst):
        return [0] + lst + [0]

    for cid in cids_sorted:
        trial = curr_cids + [cid]
        trial_dk = {**curr_dk, cid: full_dk.get(cid, prob.customers[cid].demand_kg)}
        trial_dm = {**curr_dm, cid: full_dm.get(cid, prob.customers[cid].demand_m3)}
        rc = evaluate_route(prob, vtype, build_nodes(trial),
                             demand_override=trial_dk, volume_override=trial_dm,
                             check_feasibility=True)
        if rc.feasible:
            curr_cids = trial
            curr_dk = trial_dk
            curr_dm = trial_dm
        else:
            # 切
            if curr_cids:
                chunks.append((build_nodes(curr_cids), curr_dk, curr_dm))
            # 以 cid 为起点开新路径
            curr_cids = [cid]
            curr_dk = {cid: full_dk.get(cid, prob.customers[cid].demand_kg)}
            curr_dm = {cid: full_dm.get(cid, prob.customers[cid].demand_m3)}
            # 检查单客户路径是否可行
            rc1 = evaluate_route(prob, vtype, build_nodes(curr_cids),
                                  demand_override=curr_dk, volume_override=curr_dm,
                                  check_feasibility=True)
            if not rc1.feasible:
                # 单客户都不行, 只能强行加入
                pass

    if curr_cids:
        chunks.append((build_nodes(curr_cids), curr_dk, curr_dm))
    return chunks


def try_merge_routes(prob: Problem, sol: Solution, max_attempts: int = 30) -> Solution:
    """贪心合并: 找两条可以装进同一辆车的短路径, 按 [0, c1, c2, ..., 0] 拼接。"""
    import itertools
    attempts = 0
    improved = True
    while improved and attempts < max_attempts:
        improved = False
        attempts += 1
        # 找所有路径的载重 + 客户数
        info = [
            (i, r, sum(r.delivered_kg.values()), sum(r.delivered_m3.values()))
            for i, r in enumerate(sol.routes)
        ]
        # 短路径优先尝试合并 (按客户数排)
        info.sort(key=lambda x: len(x[1].customers()))
        # 两两尝试
        for (i1, r1, kg1, m31), (i2, r2, kg2, m32) in itertools.combinations(info, 2):
            # 选一辆大车能装下两者的
            needed_kg = kg1 + kg2
            needed_m3 = m31 + m32
            # 用 r1 或 r2 的车, 或找更大的
            available = _compute_available(sol)
            # 考虑临时"归还"r1/r2 的车
            available[r1.vtype.type_id] += 1
            available[r2.vtype.type_id] += 1
            vt = _pick_vehicle_for(needed_kg, needed_m3, available)
            if vt is None:
                continue
            if vt.capacity_kg < needed_kg or vt.capacity_m3 < needed_m3:
                continue
            # 合并: [0, r1.cids, r2.cids, 0]
            cids1 = r1.nodes[1:-1]
            cids2 = r2.nodes[1:-1]
            merged = [0] + cids1 + cids2 + [0]
            merged_dk = {**r1.delivered_kg, **r2.delivered_kg}
            merged_dm = {**r1.delivered_m3, **r2.delivered_m3}
            rc = evaluate_route(prob, vt, merged,
                                 demand_override=merged_dk, volume_override=merged_dm,
                                 check_feasibility=True)
            if not rc.feasible:
                # 试反向
                merged = [0] + cids2 + cids1 + [0]
                rc = evaluate_route(prob, vt, merged,
                                     demand_override=merged_dk, volume_override=merged_dm,
                                     check_feasibility=True)
                if not rc.feasible:
                    continue
            # 检查是否真的省钱: 两条旧路径的成本 - 新路径的成本
            old1 = evaluate_route(prob, r1.vtype, r1.nodes,
                                   demand_override=r1.delivered_kg,
                                   volume_override=r1.delivered_m3,
                                   check_feasibility=False).total
            old2 = evaluate_route(prob, r2.vtype, r2.nodes,
                                   demand_override=r2.delivered_kg,
                                   volume_override=r2.delivered_m3,
                                   check_feasibility=False).total
            if rc.total < old1 + old2 - 1e-3:
                # 确认: 替换 r1 为合并路径, 删除 r2
                new_r = Route(vtype=vt, nodes=merged,
                              delivered_kg=merged_dk, delivered_m3=merged_dm)
                # i1 < i2, 先删 i2 再替换 i1
                del sol.routes[i2]
                sol.routes[i1] = new_r
                improved = True
                break
    return sol


if __name__ == "__main__":
    from core.data_loader import load_problem
    from construct.spiral_init import spiral_construct
    from core.solution import evaluate_solution

    prob = load_problem()
    init = spiral_construct(prob, clockwise=True, outward=True)
    t0, d0 = evaluate_solution(prob, init)
    n_infeas0 = sum(1 for x in d0 if not x.feasible)
    print(f"原始:    成本 {t0:.0f}, 路径 {len(init.routes)}, 不可行 {n_infeas0}")

    fixed = repair_infeasible_routes(prob, init)
    t1, d1 = evaluate_solution(prob, fixed)
    n_infeas1 = sum(1 for x in d1 if not x.feasible)
    print(f"修复后:  成本 {t1:.0f}, 路径 {len(fixed.routes)}, 不可行 {n_infeas1}")

    merged = try_merge_routes(prob, fixed)
    t2, d2 = evaluate_solution(prob, merged)
    n_infeas2 = sum(1 for x in d2 if not x.feasible)
    print(f"合并后:  成本 {t2:.0f}, 路径 {len(merged.routes)}, 不可行 {n_infeas2}")
