"""
Piggyback 算子: 把小客户路径 "搭便车" 塞进大客户路径的空闲容量里。

策略:
    1. 找出所有 "含大客户" 的路径 (通常满载)
    2. 对于每条 "只含小客户" 的路径, 看其中的客户能否插入到大客户路径
    3. 插入成本 = 增加的行驶时间/能耗, 但省了一条路径 (400 元启动成本)
    4. 如果插入收益 > 0, 应用
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import List

from core.problem import Problem, VEHICLE_TYPES
from core.solution import Solution, Route
from core.cost import evaluate_route


def piggyback_small_into_big(prob: Problem, sol: Solution, max_iter: int = 20) -> Solution:
    """把小路径的客户搭便车到已有路径。"""
    for _iter in range(max_iter):
        improved = False

        # 按载重余量从大到小排序所有路径 (更松的路径更可能接受乘客)
        route_slacks = []
        for i, r in enumerate(sol.routes):
            used_kg = sum(r.delivered_kg.values())
            used_m3 = sum(r.delivered_m3.values())
            slack_kg = r.vtype.capacity_kg - used_kg
            slack_m3 = r.vtype.capacity_m3 - used_m3
            route_slacks.append((i, slack_kg, slack_m3))

        # 寻找可被消解的路径 (客户数少 + 可以完全迁移)
        # 按客户数升序
        route_order = sorted(range(len(sol.routes)),
                              key=lambda i: len(sol.routes[i].customers()))

        for src_i in route_order:
            if improved:
                break
            src = sol.routes[src_i]
            src_cids = src.customers()
            if not src_cids:
                continue
            # 如果这条路径超过 3 个客户, 跳过 (合并成本太高)
            if len(src_cids) > 3:
                continue

            # 源路径的成本
            src_cost = evaluate_route(prob, src.vtype, src.nodes,
                                        demand_override=src.delivered_kg,
                                        volume_override=src.delivered_m3,
                                        check_feasibility=True).total

            # 尝试把 src 的所有客户分散插入到其他路径
            # 需要为每个客户找一个"目标路径+位置"
            # 贪心: 一个一个插入, 每个客户找当前最佳位置
            assignments = []  # (cid, target_route_idx, insert_pos, qty_kg, qty_m3, delta)
            candidate = deepcopy(sol)
            src_cids_remaining = list(src_cids)
            tmp_slacks = {i: (rs[1], rs[2]) for i, rs in enumerate([(i, *x) for i, x in zip(range(len(sol.routes)), [(r.vtype.capacity_kg - sum(r.delivered_kg.values()), r.vtype.capacity_m3 - sum(r.delivered_m3.values())) for r in sol.routes])])}
            # 简化: 重新算
            tmp_slacks = {}
            for i, r in enumerate(sol.routes):
                if i == src_i:
                    continue
                uk = sum(r.delivered_kg.values())
                um = sum(r.delivered_m3.values())
                tmp_slacks[i] = (r.vtype.capacity_kg - uk, r.vtype.capacity_m3 - um)

            total_new_cost = 0
            ok = True
            insertions = []  # 每次插入的 (target_i, pos, cid, dk, dm, old_target_cost, new_target_cost)
            for cid in src_cids_remaining:
                qty_kg = src.delivered_kg.get(cid, 0)
                qty_m3 = src.delivered_m3.get(cid, 0)
                best_delta = math.inf
                best_ti = -1
                best_pos = -1
                best_new_cost = 0
                best_old_cost = 0

                for ti, (sk, sm) in tmp_slacks.items():
                    if sk < qty_kg - 1e-6 or sm < qty_m3 - 1e-6:
                        continue
                    tr = candidate.routes[ti]
                    # 同一路径不重复配送
                    if cid in tr.delivered_kg:
                        continue
                    # 找最佳插入位置
                    old_cost = evaluate_route(prob, tr.vtype, tr.nodes,
                                                demand_override=tr.delivered_kg,
                                                volume_override=tr.delivered_m3,
                                                check_feasibility=True).total
                    for pos in range(1, len(tr.nodes)):
                        new_nodes = tr.nodes[:pos] + [cid] + tr.nodes[pos:]
                        new_dk = {**tr.delivered_kg, cid: qty_kg}
                        new_dm = {**tr.delivered_m3, cid: qty_m3}
                        rc = evaluate_route(prob, tr.vtype, new_nodes,
                                             demand_override=new_dk,
                                             volume_override=new_dm,
                                             check_feasibility=True)
                        if not rc.feasible:
                            continue
                        delta = rc.total - old_cost
                        if delta < best_delta:
                            best_delta = delta
                            best_ti = ti
                            best_pos = pos
                            best_new_cost = rc.total
                            best_old_cost = old_cost

                if best_ti < 0:
                    ok = False
                    break

                # 应用到 candidate (供后续客户参考)
                tr = candidate.routes[best_ti]
                new_nodes = tr.nodes[:best_pos] + [cid] + tr.nodes[best_pos:]
                new_dk = {**tr.delivered_kg, cid: qty_kg}
                new_dm = {**tr.delivered_m3, cid: qty_m3}
                candidate.routes[best_ti] = Route(
                    vtype=tr.vtype, nodes=new_nodes,
                    delivered_kg=new_dk, delivered_m3=new_dm,
                )
                # 更新余量
                sk, sm = tmp_slacks[best_ti]
                tmp_slacks[best_ti] = (sk - qty_kg, sm - qty_m3)
                total_new_cost += best_delta
                insertions.append(best_delta)

            if not ok:
                continue

            # 总收益 = 原来 src 的成本 - (所有 delta)
            # 删掉 src 后省了 src_cost 元, 但每次插入有 delta 元额外
            saved = src_cost - total_new_cost
            if saved > 1e-3:
                # 应用: 从 candidate 里删 src
                # 因为 candidate 和 sol 同步变化, src_i 位置还是 src
                del candidate.routes[src_i]
                sol.routes = candidate.routes
                improved = True

        if not improved:
            break
    return sol


if __name__ == "__main__":
    import pickle
    from core.data_loader import load_problem
    from core.solution import evaluate_solution, solution_summary

    with open('/home/claude/vrp/result_q1.pkl', 'rb') as f:
        data = pickle.load(f)

    prob = load_problem()
    sol = data['best']
    t0, _ = evaluate_solution(prob, sol)
    print(f"原始: {t0:.0f}, 路径 {len(sol.routes)}")

    import time
    t_start = time.time()
    sol2 = piggyback_small_into_big(prob, deepcopy(sol), max_iter=50)
    print(f"用时: {time.time()-t_start:.1f}s")
    t1, d1 = evaluate_solution(prob, sol2)
    n_infeas = sum(1 for d in d1 if not d.feasible)
    print(f"搭便车后: {t1:.0f} (降 {t0-t1:.0f}), 路径 {len(sol2.routes)}, 不可行 {n_infeas}")

    info = solution_summary(prob, sol2)
    print(f"\n成本分解:")
    for k in ['start_cost', 'energy_cost', 'carbon_cost', 'early_cost', 'late_cost']:
        print(f"  {k}: {info[k]:.0f}")

    with open('/home/claude/vrp/result_q1_pig.pkl', 'wb') as f:
        pickle.dump({'best': sol2}, f)
