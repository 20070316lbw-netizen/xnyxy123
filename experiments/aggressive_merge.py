"""
专门的车辆压缩器: 把 133 辆车压缩到接近理论下界 100 辆。

策略:
    1. 对所有路径两两尝试合并, 即使要升级车型也可以
    2. 单客户路径之间合并的优先级高 (每合并一对省 400 元启动成本)
    3. 合并后做 2-opt 优化顺序
"""
from __future__ import annotations

import itertools
import math
from copy import deepcopy
from typing import List, Tuple

from core.problem import Problem, VehicleType, VEHICLE_TYPES
from core.solution import Solution, Route
from core.cost import evaluate_route
from alns.operators_v2 import two_opt_route


def _available_vehicles(sol: Solution, exclude_routes: List[int] = None) -> dict:
    """返回每种车的可用余量, 排除 exclude_routes 中占用的车。"""
    used = {v.type_id: 0 for v in VEHICLE_TYPES}
    for i, r in enumerate(sol.routes):
        if exclude_routes and i in exclude_routes:
            continue
        used[r.vtype.type_id] += 1
    return {v.type_id: v.fleet_size - used[v.type_id] for v in VEHICLE_TYPES}


def aggressive_merge(prob: Problem, sol: Solution, max_passes: int = 5) -> Solution:
    """反复尝试合并所有路径对, 直到没有改进。
    
    关键改进:
        - 合并候选不受"原车型"限制, 任意可用更大的车都能尝试
        - 每次合并后立即 2-opt 优化
        - 允许 SDVRP: 一个客户可能在 r1 和 r2 都出现 (分别送一部分), 合并时累加
    """
    for _pass in range(max_passes):
        improved = False
        n = len(sol.routes)
        # 按 "合并潜力" 排序: 优先尝试短路径
        idx_order = sorted(range(n), key=lambda i: len(sol.routes[i].customers()))

        for i_iter, i in enumerate(idx_order):
            if improved:
                break
            for j in idx_order[i_iter+1:]:
                r1, r2 = sol.routes[i], sol.routes[j]
                # 合并后的需求量: 同一客户的量要累加
                merged_dk = {}
                merged_dm = {}
                for cid, q in r1.delivered_kg.items():
                    merged_dk[cid] = merged_dk.get(cid, 0) + q
                for cid, q in r2.delivered_kg.items():
                    merged_dk[cid] = merged_dk.get(cid, 0) + q
                for cid, q in r1.delivered_m3.items():
                    merged_dm[cid] = merged_dm.get(cid, 0) + q
                for cid, q in r2.delivered_m3.items():
                    merged_dm[cid] = merged_dm.get(cid, 0) + q

                total_kg = sum(merged_dk.values())
                total_m3 = sum(merged_dm.values())

                # 找合适车 (排除 r1, r2 占用的位置; 可用的任一车型都试)
                avail = _available_vehicles(sol, exclude_routes=[i, j])
                candidate_vts = [v for v in VEHICLE_TYPES
                                  if avail[v.type_id] > 0
                                  and v.capacity_kg >= total_kg - 1e-6
                                  and v.capacity_m3 >= total_m3 - 1e-6]
                # 按载重从小到大 (小车启动成本一样但能耗可能更低)
                candidate_vts.sort(key=lambda v: v.capacity_kg)

                if not candidate_vts:
                    continue

                # 原始成本
                old1 = evaluate_route(prob, r1.vtype, r1.nodes,
                                       demand_override=r1.delivered_kg,
                                       volume_override=r1.delivered_m3,
                                       check_feasibility=True).total
                old2 = evaluate_route(prob, r2.vtype, r2.nodes,
                                       demand_override=r2.delivered_kg,
                                       volume_override=r2.delivered_m3,
                                       check_feasibility=True).total
                old_total = old1 + old2

                # 构造候选: 合并客户列表, 按时间窗排序
                cids1 = [c for c in r1.nodes if c != 0]
                cids2 = [c for c in r2.nodes if c != 0]
                # 去重合并 (同一客户在两路径都有, 只访问一次)
                seen = set()
                merged_cids = []
                for c in cids1 + cids2:
                    if c not in seen:
                        merged_cids.append(c)
                        seen.add(c)
                merged_cids_sorted = sorted(merged_cids, key=lambda c: prob.customers[c].tw_start)

                # 尝试每种候选车
                best_delta = math.inf
                best_config = None
                for vt in candidate_vts:
                    trial_nodes = [0] + merged_cids_sorted + [0]
                    rc = evaluate_route(prob, vt, trial_nodes,
                                         demand_override=merged_dk,
                                         volume_override=merged_dm,
                                         check_feasibility=True)
                    if not rc.feasible:
                        continue
                    # 对合并后的路径做 2-opt 优化
                    trial_r = Route(vtype=vt, nodes=trial_nodes,
                                     delivered_kg=merged_dk, delivered_m3=merged_dm)
                    opt_r = two_opt_route(prob, trial_r, max_iter=5)
                    rc_opt = evaluate_route(prob, vt, opt_r.nodes,
                                             demand_override=opt_r.delivered_kg,
                                             volume_override=opt_r.delivered_m3,
                                             check_feasibility=True)
                    if not rc_opt.feasible:
                        continue
                    delta = rc_opt.total - old_total
                    if delta < best_delta:
                        best_delta = delta
                        best_config = (vt, opt_r.nodes, merged_dk, merged_dm)

                if best_delta < -1e-3 and best_config is not None:
                    vt, nodes, dk, dm = best_config
                    new_r = Route(vtype=vt, nodes=nodes, delivered_kg=dk, delivered_m3=dm)
                    # 删 j, 替换 i
                    idx_big = max(i, j)
                    idx_small = min(i, j)
                    del sol.routes[idx_big]
                    sol.routes[idx_small] = new_r
                    improved = True
                    break  # 每 pass 只合并一对, 避免索引失效
        if not improved:
            break
    return sol


if __name__ == "__main__":
    import pickle
    with open('/home/claude/vrp/result_q1.pkl', 'rb') as f:
        data = pickle.load(f)

    from core.data_loader import load_problem
    from core.solution import evaluate_solution, solution_summary

    prob = load_problem()
    sol = data['best']
    t0, _ = evaluate_solution(prob, sol)
    print(f"ALNS 最优: {t0:.0f}, 路径 {len(sol.routes)}")

    import time
    t_start = time.time()
    sol2 = aggressive_merge(prob, deepcopy(sol), max_passes=30)
    print(f"用时: {time.time()-t_start:.1f}s")
    t1, d1 = evaluate_solution(prob, sol2)
    n_infeas = sum(1 for d in d1 if not d.feasible)
    print(f"合并后: {t1:.0f} (下降 {t0-t1:.0f}), 路径 {len(sol2.routes)}, 不可行 {n_infeas}")
    info = solution_summary(prob, sol2)
    print(f"\n成本分解:")
    for k in ['start_cost', 'energy_cost', 'carbon_cost', 'early_cost', 'late_cost']:
        print(f"  {k}: {info[k]:.0f}")

    # 保存
    with open('/home/claude/vrp/result_q1_merged.pkl', 'wb') as f:
        pickle.dump({'best': sol2}, f)
