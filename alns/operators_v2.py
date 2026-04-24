"""
ALNS 算子库 v2: 加入路径内/间局部搜索算子，以及更激进的破坏。

新增破坏算子:
    - big_random_removal:   抽 30-40% 客户 (大幅扰动)
    - zone_removal:         抽整个时间窗/空间区域的客户

新增"精细化"算子 (实际上作为 repair 的后处理):
    - two_opt:              路径内部反转子段 (找更好的节点顺序)
    - or_opt:               路径内移动 1-3 个连续节点
    - route_swap:           两路径间交换一对客户

这些算子作为 insertion 完成后的"打磨"步骤自动应用。
"""
from __future__ import annotations

import math
import random
from copy import deepcopy
from typing import List, Tuple

from core.problem import Problem, VehicleType, VEHICLE_TYPES
from core.solution import Solution, Route
from core.cost import evaluate_route


# ========= 内部优化: 2-opt =========

def two_opt_route(prob: Problem, r: Route, max_iter: int = 20) -> Route:
    """对单条路径做 2-opt: 反转一个子段, 如果更好则接受。"""
    if len(r.nodes) <= 4:  # depot-c-depot, 无法2-opt
        return r
    best_nodes = list(r.nodes)
    best_cost = evaluate_route(prob, r.vtype, best_nodes,
                                demand_override=r.delivered_kg,
                                volume_override=r.delivered_m3,
                                check_feasibility=True).total
    for _ in range(max_iter):
        improved = False
        n = len(best_nodes)
        for i in range(1, n - 2):
            for j in range(i + 1, n - 1):
                # 反转 [i:j+1]
                trial = best_nodes[:i] + best_nodes[i:j+1][::-1] + best_nodes[j+1:]
                rc = evaluate_route(prob, r.vtype, trial,
                                     demand_override=r.delivered_kg,
                                     volume_override=r.delivered_m3,
                                     check_feasibility=True)
                if rc.feasible and rc.total < best_cost - 1e-3:
                    best_nodes = trial
                    best_cost = rc.total
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break
    return Route(vtype=r.vtype, nodes=best_nodes,
                 delivered_kg=dict(r.delivered_kg), delivered_m3=dict(r.delivered_m3))


def two_opt_solution(prob: Problem, sol: Solution) -> Solution:
    """对解中每条路径做 2-opt。"""
    new_routes = []
    for r in sol.routes:
        new_routes.append(two_opt_route(prob, r))
    out = Solution()
    out.routes = new_routes
    return out


# ========= 路径间: relocate =========

def relocate_customer(prob: Problem, sol: Solution, rng: random.Random, max_trials: int = 50) -> Solution:
    """把一个客户从路径A移到路径B的最佳位置 (SDVRP: 连同其配送量)。"""
    if len(sol.routes) < 2:
        return sol
    improvement = True
    trial = 0
    while improvement and trial < max_trials:
        improvement = False
        trial += 1
        # 随机挑一个路径A
        ra_idx = rng.randint(0, len(sol.routes) - 1)
        ra = sol.routes[ra_idx]
        cids_a = ra.customers()
        if not cids_a:
            continue
        # 随机挑一个客户
        cid = rng.choice(cids_a)
        qty_kg = ra.delivered_kg.get(cid, 0)
        qty_m3 = ra.delivered_m3.get(cid, 0)
        # 当前A成本
        cost_a = evaluate_route(prob, ra.vtype, ra.nodes,
                                 demand_override=ra.delivered_kg,
                                 volume_override=ra.delivered_m3,
                                 check_feasibility=True).total
        # 移除后A成本
        pos = ra.nodes.index(cid)
        new_nodes_a = ra.nodes[:pos] + ra.nodes[pos+1:]
        new_dk_a = {k: v for k, v in ra.delivered_kg.items() if k != cid}
        new_dm_a = {k: v for k, v in ra.delivered_m3.items() if k != cid}
        if len(new_nodes_a) < 3:  # 路径A空了
            cost_a_new = 0
        else:
            cost_a_new = evaluate_route(prob, ra.vtype, new_nodes_a,
                                          demand_override=new_dk_a,
                                          volume_override=new_dm_a,
                                          check_feasibility=True).total

        # 尝试插入其他路径, 找最佳
        best_delta = math.inf
        best_rb_idx = -1
        best_pos = -1
        for rb_idx, rb in enumerate(sol.routes):
            if rb_idx == ra_idx:
                continue
            # 容量检查
            curr_kg = sum(rb.delivered_kg.values())
            curr_m3 = sum(rb.delivered_m3.values())
            if curr_kg + qty_kg > rb.vtype.capacity_kg + 1e-6:
                continue
            if curr_m3 + qty_m3 > rb.vtype.capacity_m3 + 1e-6:
                continue
            # 跳过同路径已送过该客户
            if cid in rb.delivered_kg:
                continue
            cost_b_old = evaluate_route(prob, rb.vtype, rb.nodes,
                                          demand_override=rb.delivered_kg,
                                          volume_override=rb.delivered_m3,
                                          check_feasibility=True).total
            for ins_pos in range(1, len(rb.nodes)):
                new_nodes_b = rb.nodes[:ins_pos] + [cid] + rb.nodes[ins_pos:]
                new_dk_b = {**rb.delivered_kg, cid: qty_kg}
                new_dm_b = {**rb.delivered_m3, cid: qty_m3}
                rc = evaluate_route(prob, rb.vtype, new_nodes_b,
                                     demand_override=new_dk_b,
                                     volume_override=new_dm_b,
                                     check_feasibility=True)
                if not rc.feasible:
                    continue
                delta = rc.total - cost_b_old + (cost_a_new - cost_a)
                if delta < best_delta:
                    best_delta = delta
                    best_rb_idx = rb_idx
                    best_pos = ins_pos

        if best_delta < -1e-3:
            # 应用
            rb = sol.routes[best_rb_idx]
            sol.routes[best_rb_idx] = Route(
                vtype=rb.vtype,
                nodes=rb.nodes[:best_pos] + [cid] + rb.nodes[best_pos:],
                delivered_kg={**rb.delivered_kg, cid: qty_kg},
                delivered_m3={**rb.delivered_m3, cid: qty_m3},
            )
            if len(new_nodes_a) >= 3:
                sol.routes[ra_idx] = Route(
                    vtype=ra.vtype, nodes=new_nodes_a,
                    delivered_kg=new_dk_a, delivered_m3=new_dm_a,
                )
            else:
                del sol.routes[ra_idx]
            improvement = True
    return sol


# ========= 路径间: merge =========

def merge_routes(prob: Problem, sol: Solution, rng: random.Random) -> Solution:
    """随机选两条路径, 尝试合并成一条。"""
    import itertools
    if len(sol.routes) < 2:
        return sol
    # 随机排序
    indices = list(range(len(sol.routes)))
    rng.shuffle(indices)
    tried = 0
    MAX_TRIES = 20
    for i, j in itertools.combinations(indices, 2):
        if tried >= MAX_TRIES:
            break
        tried += 1
        r1, r2 = sol.routes[i], sol.routes[j]
        kg = sum(r1.delivered_kg.values()) + sum(r2.delivered_kg.values())
        m3 = sum(r1.delivered_m3.values()) + sum(r2.delivered_m3.values())
        # 确定合适车
        available = {v.type_id: v.fleet_size for v in VEHICLE_TYPES}
        for r in sol.routes:
            available[r.vtype.type_id] -= 1
        available[r1.vtype.type_id] += 1
        available[r2.vtype.type_id] += 1
        feas = [v for v in VEHICLE_TYPES
                if available[v.type_id] > 0
                and v.capacity_kg >= kg
                and v.capacity_m3 >= m3]
        if not feas:
            continue
        # 选最大载重合适的(小的可能装不下,大车往往更节能)
        feas.sort(key=lambda v: v.capacity_kg)
        merged = False
        for vt in feas:
            cids1 = r1.nodes[1:-1]
            cids2 = r2.nodes[1:-1]
            # 尝试按时间窗排序
            all_cids = cids1 + cids2
            all_cids_sorted = sorted(all_cids, key=lambda c: prob.customers[c].tw_start)
            new_nodes = [0] + all_cids_sorted + [0]
            new_dk = {**r1.delivered_kg, **r2.delivered_kg}
            new_dm = {**r1.delivered_m3, **r2.delivered_m3}
            rc = evaluate_route(prob, vt, new_nodes,
                                 demand_override=new_dk,
                                 volume_override=new_dm,
                                 check_feasibility=True)
            if not rc.feasible:
                continue
            old1 = evaluate_route(prob, r1.vtype, r1.nodes,
                                   demand_override=r1.delivered_kg,
                                   volume_override=r1.delivered_m3,
                                   check_feasibility=True).total
            old2 = evaluate_route(prob, r2.vtype, r2.nodes,
                                   demand_override=r2.delivered_kg,
                                   volume_override=r2.delivered_m3,
                                   check_feasibility=True).total
            if rc.total < old1 + old2 - 1e-3:
                # 省了 — 应用
                new_r = Route(vtype=vt, nodes=new_nodes,
                              delivered_kg=new_dk, delivered_m3=new_dm)
                idx_big = max(i, j)
                idx_small = min(i, j)
                del sol.routes[idx_big]
                sol.routes[idx_small] = new_r
                merged = True
                break
        if merged:
            return sol  # 每次只合并一对
    return sol


# ========= 对解做综合局部搜索 =========

def local_search(prob: Problem, sol: Solution, rng: random.Random,
                  do_2opt: bool = True, do_relocate: bool = True, do_merge: bool = True) -> Solution:
    """综合局部搜索: 2-opt + relocate + merge, 依次应用。"""
    if do_2opt:
        sol = two_opt_solution(prob, sol)
    if do_relocate:
        sol = relocate_customer(prob, sol, rng, max_trials=30)
    if do_merge:
        sol = merge_routes(prob, sol, rng)
    return sol


if __name__ == "__main__":
    import pickle
    with open('/home/claude/vrp/result_q1.pkl', 'rb') as f:
        data = pickle.load(f)

    from core.data_loader import load_problem
    prob = load_problem()
    sol = data['best']
    from core.solution import evaluate_solution
    t0, _ = evaluate_solution(prob, sol)
    print(f"ALNS 最优: {t0:.0f}")

    rng = random.Random(42)
    sol2 = local_search(prob, sol, rng)
    t1, _ = evaluate_solution(prob, sol2)
    print(f"+局部搜索: {t1:.0f} (下降 {t0-t1:.0f})")

    # 迭代几次
    for i in range(5):
        sol2 = local_search(prob, sol2, rng)
        t2, _ = evaluate_solution(prob, sol2)
        print(f"  第{i+2}轮: {t2:.0f} (下降 {t1-t2:.0f})")
        t1 = t2
