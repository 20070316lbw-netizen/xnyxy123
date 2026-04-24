"""
ALNS 算子库。

破坏算子 (destroy operators)：
    - random_removal:     随机抽走 k 个客户 (访问记录, 不是 cid)
    - worst_removal:      按"抽走能降多少代价"排序, 抽走代价最高的 k 个
    - shaw_removal:       抽走一个客户 + 与其"相似"的 k-1 个 (距离+时间窗相似度)
    - route_removal:      随机抽走 1-2 整条路径上的所有客户

修复算子 (repair operators)：
    - greedy_insertion:   对每个 unassigned 客户, 找插入成本最低的位置插入
    - regret_insertion:   优先插入 "regret 最大" 的客户 (后悔值=次优插入-最优插入)
    - random_insertion:   随机选位置插入

注意 SDVRP: 我们把每条 (route, cid) 访问视作一个"访问记录", 破坏是移除访问记录,
修复时为每条需要重新配送的客户单独决策插入位置 (一个客户可以被插到多条路径)。
"""
from __future__ import annotations

import math
import random
from copy import deepcopy
from typing import List, Tuple

from core.problem import (
    Problem, VehicleType, VEHICLE_TYPES,
    DEPART_TIME, SERVICE_TIME, MAX_WORK_HOURS,
)
from core.solution import Solution, Route
from core.cost import evaluate_route, RouteCost


# ========= 工具 =========

def _total_remaining(cid: int, sol: Solution, full_demand_kg: float) -> float:
    """计算给定客户当前在所有路径上已配送的总量。用于 SDVRP 判断是否还需要配送。"""
    total = 0.0
    for r in sol.routes:
        total += r.delivered_kg.get(cid, 0.0)
    return full_demand_kg - total


def _clean_empty_routes(sol: Solution) -> None:
    """移除空路径 (只有 depot)。"""
    sol.routes = [r for r in sol.routes if len(r.nodes) > 2]


def _customer_visits(sol: Solution) -> List[Tuple[int, int, int]]:
    """返回所有访问记录: [(route_idx, position_in_route, cid), ...]。
    position_in_route 是 cid 在 route.nodes 中的索引 (1-based, 去掉 depot)。"""
    visits = []
    for ri, r in enumerate(sol.routes):
        for pi, cid in enumerate(r.nodes):
            if cid != 0:  # 跳过 depot
                visits.append((ri, pi, cid))
    return visits


# ========= 破坏算子 =========

def random_removal(prob: Problem, sol: Solution, k: int, rng: random.Random) -> List[int]:
    """随机抽走 k 个访问记录。返回被移除的 cid 列表 (含重复, 因为同一客户可能有多个访问)。"""
    visits = _customer_visits(sol)
    if not visits:
        return []
    k = min(k, len(visits))
    chosen = rng.sample(visits, k)
    return _remove_visits(sol, chosen)


def worst_removal(prob: Problem, sol: Solution, k: int, rng: random.Random) -> List[int]:
    """对每个访问记录, 算"抽走后本路径成本下降多少", 挑前 k 个抽走。"""
    visits = _customer_visits(sol)
    if not visits:
        return []
    # 当前每条路径的成本
    current_cost = [evaluate_route(
        prob, r.vtype, r.nodes,
        demand_override=r.delivered_kg, volume_override=r.delivered_m3,
    ).total for r in sol.routes]

    # 每个访问的"抽走收益"
    gains = []
    for (ri, pi, cid) in visits:
        r = sol.routes[ri]
        new_nodes = r.nodes[:pi] + r.nodes[pi + 1:]
        new_dk = {k_: v for k_, v in r.delivered_kg.items() if k_ != cid}
        new_dm = {k_: v for k_, v in r.delivered_m3.items() if k_ != cid}
        new_cost = evaluate_route(
            prob, r.vtype, new_nodes,
            demand_override=new_dk, volume_override=new_dm,
        ).total if len(new_nodes) > 2 else 0.0
        gain = current_cost[ri] - new_cost
        # 加一点随机扰动防止每次都选同一批
        gains.append((gain + rng.random() * 0.1, ri, pi, cid))
    gains.sort(reverse=True)  # 收益大在前
    k = min(k, len(gains))
    chosen = [(g[1], g[2], g[3]) for g in gains[:k]]
    return _remove_visits(sol, chosen)


def shaw_removal(prob: Problem, sol: Solution, k: int, rng: random.Random) -> List[int]:
    """抽走 1 个种子客户 + 与其最"相似"的 k-1 个。
    相似度 = 空间距离 + 时间窗起点距离的加权。"""
    visits = _customer_visits(sol)
    if not visits:
        return []
    seed = rng.choice(visits)
    _, _, seed_cid = seed
    sc = prob.customers[seed_cid]

    # 对其他访问计算相似度 (数值越小越相似)
    similarities = []
    for (ri, pi, cid) in visits:
        if (ri, pi, cid) == seed:
            continue
        c = prob.customers[cid]
        dist = prob.distance[seed_cid, cid]
        tw_diff = abs(sc.tw_start - c.tw_start) * 10.0  # 时间差权重
        score = dist + tw_diff + rng.random() * 0.5
        similarities.append((score, ri, pi, cid))
    similarities.sort()
    k = min(k, len(similarities) + 1)  # +1 因为包含 seed
    chosen = [seed] + [(s[1], s[2], s[3]) for s in similarities[:k - 1]]
    return _remove_visits(sol, chosen)


def route_removal(prob: Problem, sol: Solution, k: int, rng: random.Random) -> List[int]:
    """随机删 1-2 条路径, 其上的所有访问全部进池。k 在这里只用于估算。"""
    if not sol.routes:
        return []
    # 挑成本最高或随机
    n_to_kill = min(1 + rng.randint(0, 1), len(sol.routes))
    idx_kill = rng.sample(range(len(sol.routes)), n_to_kill)
    removed_cids = []
    # 按 idx 从大到小删, 防止索引失效
    for i in sorted(idx_kill, reverse=True):
        for cid in sol.routes[i].nodes:
            if cid != 0:
                removed_cids.append(cid)
        del sol.routes[i]
    return removed_cids


def _remove_visits(sol: Solution, visit_list: List[Tuple[int, int, int]]) -> List[int]:
    """按 (ri, pi, cid) 批量删除。注意同路径多条访问时索引偏移。"""
    # 按 ri → pi 倒序删除
    by_route: dict[int, list] = {}
    for ri, pi, cid in visit_list:
        by_route.setdefault(ri, []).append((pi, cid))

    removed_cids = []
    for ri, items in by_route.items():
        r = sol.routes[ri]
        items.sort(key=lambda x: x[0], reverse=True)
        for pi, cid in items:
            # 同步清理 delivered_kg/m3 (记录原本该访问的配送量)
            # SDVRP: 只移除这一次访问的量
            if cid in r.delivered_kg:
                # 这里简化: 假设一个 cid 只出现一次在同一路径
                del r.delivered_kg[cid]
            if cid in r.delivered_m3:
                del r.delivered_m3[cid]
            del r.nodes[pi]
            removed_cids.append(cid)
    _clean_empty_routes(sol)
    return removed_cids


# ========= 修复算子：插入 =========

def _best_insertion(
    prob: Problem, route: Route, cid: int, qty_kg: float, qty_m3: float
) -> Tuple[float, int] | None:
    """在给定路径上寻找插入 (cid, qty) 的最佳位置。
    返回 (成本增量, 插入位置) 或 None 表示无法插入。"""
    # 容量检查 (当前路径是否还装得下)
    curr_kg = sum(route.delivered_kg.values())
    curr_m3 = sum(route.delivered_m3.values())
    if curr_kg + qty_kg > route.vtype.capacity_kg + 1e-6:
        return None
    if curr_m3 + qty_m3 > route.vtype.capacity_m3 + 1e-6:
        return None

    # 当前成本
    curr_cost = evaluate_route(
        prob, route.vtype, route.nodes,
        demand_override=route.delivered_kg,
        volume_override=route.delivered_m3,
        check_feasibility=False,
    ).total

    best_delta = math.inf
    best_pos = -1

    for pos in range(1, len(route.nodes)):  # 不能插到 depot 之前
        new_nodes = route.nodes[:pos] + [cid] + route.nodes[pos:]
        new_dk = {**route.delivered_kg, cid: qty_kg}
        new_dm = {**route.delivered_m3, cid: qty_m3}
        rc = evaluate_route(
            prob, route.vtype, new_nodes,
            demand_override=new_dk, volume_override=new_dm,
            check_feasibility=True,
        )
        if not rc.feasible:
            continue
        delta = rc.total - curr_cost
        if delta < best_delta:
            best_delta = delta
            best_pos = pos
    if best_pos < 0:
        return None
    return best_delta, best_pos


def _pick_new_vehicle(available: dict, qty_kg: float, qty_m3: float) -> VehicleType | None:
    """挑一辆合适的新车 (小而能装)。"""
    feasible = [
        vt for vt in VEHICLE_TYPES
        if available[vt.type_id] > 0
        and vt.capacity_kg >= qty_kg
        and vt.capacity_m3 >= qty_m3
    ]
    if feasible:
        return min(feasible, key=lambda v: v.capacity_kg)
    # 没人能整装 → 取最大可用
    cands = [vt for vt in VEHICLE_TYPES if available[vt.type_id] > 0]
    if not cands:
        return None
    return max(cands, key=lambda v: v.capacity_kg)


def _compute_available(sol: Solution) -> dict:
    """计算当前还能使用的每种车的余量。"""
    used = {vt.type_id: 0 for vt in VEHICLE_TYPES}
    for r in sol.routes:
        used[r.vtype.type_id] += 1
    return {vt.type_id: vt.fleet_size - used[vt.type_id] for vt in VEHICLE_TYPES}


def _insert_one_customer(
    prob: Problem, sol: Solution, cid: int, qty_kg: float, qty_m3: float, rng: random.Random
) -> bool:
    """把一个客户 (可能是部分需求) 插入到某条路径或新开一条。
    返回是否成功。"""
    # 1. 找当前所有可行路径中的最佳插入
    best_delta = math.inf
    best_ri = -1
    best_pos = -1
    for ri, r in enumerate(sol.routes):
        # 跳过同一路径已经配送过该客户的情况（避免重复访问同一客户）
        if cid in r.delivered_kg:
            continue
        res = _best_insertion(prob, r, cid, qty_kg, qty_m3)
        if res is None:
            continue
        delta, pos = res
        if delta < best_delta:
            best_delta = delta
            best_ri = ri
            best_pos = pos

    # 2. 新开一条路径的代价(启动成本 + 基本 depot→c→depot 成本)
    available = _compute_available(sol)
    vt_new = _pick_new_vehicle(available, qty_kg, qty_m3)
    new_route_cost = math.inf
    if vt_new is not None:
        new_rc = evaluate_route(
            prob, vt_new, [0, cid, 0],
            demand_override={cid: qty_kg},
            volume_override={cid: qty_m3},
            check_feasibility=True,
        )
        if new_rc.feasible:
            new_route_cost = new_rc.total

    # 3. 选代价低的
    if best_delta <= new_route_cost:
        if best_ri < 0:
            return False
        r = sol.routes[best_ri]
        r.nodes.insert(best_pos, cid)
        r.delivered_kg[cid] = qty_kg
        r.delivered_m3[cid] = qty_m3
        return True
    else:
        if vt_new is None or math.isinf(new_route_cost):
            return False
        sol.routes.append(Route(
            vtype=vt_new, nodes=[0, cid, 0],
            delivered_kg={cid: qty_kg}, delivered_m3={cid: qty_m3},
        ))
        return True


def greedy_insertion(prob: Problem, sol: Solution, removed_cids: List[int], rng: random.Random) -> bool:
    """对每个被移除的 cid, 贪心插回去。支持 SDVRP: 失败则尝试拆分。"""
    # 把同一客户的多次移除合并成"还需多少总量"
    from collections import Counter

    # 每个 cid 需要配送的总量 = 全量 - 当前所有路径已配送
    need_kg: dict[int, float] = {}
    need_m3: dict[int, float] = {}
    for cid in removed_cids:
        if cid in need_kg:
            continue  # 同一个 cid 多次进池, 只需要算一次剩余
        remain_kg = _total_remaining(cid, sol, prob.customers[cid].demand_kg)
        remain_m3 = _total_remaining(cid, sol, prob.customers[cid].demand_m3)
        if remain_kg > 1e-6 or remain_m3 > 1e-6:
            need_kg[cid] = max(0, remain_kg)
            need_m3[cid] = max(0, remain_m3)

    # 按需求量从大到小插入 (大客户优先)
    order = sorted(need_kg.keys(), key=lambda c: -need_kg[c])

    all_success = True
    for cid in order:
        remain_kg = need_kg[cid]
        remain_m3 = need_m3[cid]
        MAX_SPLITS = 10  # 防止无限拆分
        splits = 0
        while remain_kg > 1e-6 or remain_m3 > 1e-6:
            if splits >= MAX_SPLITS:
                all_success = False
                break
            # 尝试整装插入
            ok = _insert_one_customer(prob, sol, cid, remain_kg, remain_m3, rng)
            if ok:
                remain_kg = 0
                remain_m3 = 0
                break
            # 整装失败 → 用最大车能装的量拆分
            available = _compute_available(sol)
            vts = sorted(VEHICLE_TYPES, key=lambda v: -v.capacity_kg)
            # 找最大可用车
            max_cap_kg = 0
            max_cap_m3 = 0
            for vt in vts:
                if available[vt.type_id] > 0:
                    max_cap_kg = vt.capacity_kg
                    max_cap_m3 = vt.capacity_m3
                    break
            if max_cap_kg <= 0:
                all_success = False
                break
            # 按容量约束计算能拆多少
            kg_frac = max_cap_kg / remain_kg if remain_kg > 0 else 1.0
            m3_frac = max_cap_m3 / remain_m3 if remain_m3 > 0 else 1.0
            frac = min(kg_frac, m3_frac, 1.0)
            take_kg = remain_kg * frac * 0.95  # 留点缓冲
            take_m3 = remain_m3 * frac * 0.95
            ok = _insert_one_customer(prob, sol, cid, take_kg, take_m3, rng)
            if ok:
                remain_kg -= take_kg
                remain_m3 -= take_m3
                splits += 1
            else:
                all_success = False
                break

    return all_success


def random_insertion(prob: Problem, sol: Solution, removed_cids: List[int], rng: random.Random) -> bool:
    """随机位置插入 - 贪心变体，随机打乱插入顺序。"""
    shuffled = list(set(removed_cids))
    rng.shuffle(shuffled)
    from collections import OrderedDict

    need_kg = {}
    need_m3 = {}
    for cid in shuffled:
        remain_kg = _total_remaining(cid, sol, prob.customers[cid].demand_kg)
        remain_m3 = _total_remaining(cid, sol, prob.customers[cid].demand_m3)
        if remain_kg > 1e-6:
            need_kg[cid] = remain_kg
            need_m3[cid] = max(0, remain_m3)

    all_success = True
    for cid in need_kg:
        remain_kg = need_kg[cid]
        remain_m3 = need_m3[cid]
        MAX_SPLITS = 10
        splits = 0
        while remain_kg > 1e-6:
            if splits >= MAX_SPLITS:
                all_success = False
                break
            ok = _insert_one_customer(prob, sol, cid, remain_kg, remain_m3, rng)
            if ok:
                break
            available = _compute_available(sol)
            vts = sorted(VEHICLE_TYPES, key=lambda v: -v.capacity_kg)
            max_cap_kg = 0
            max_cap_m3 = 0
            for vt in vts:
                if available[vt.type_id] > 0:
                    max_cap_kg = vt.capacity_kg
                    max_cap_m3 = vt.capacity_m3
                    break
            if max_cap_kg <= 0:
                all_success = False
                break
            kg_frac = max_cap_kg / remain_kg if remain_kg > 0 else 1
            m3_frac = max_cap_m3 / remain_m3 if remain_m3 > 0 else 1
            frac = min(kg_frac, m3_frac, 1.0)
            take_kg = remain_kg * frac * 0.95
            take_m3 = remain_m3 * frac * 0.95
            ok = _insert_one_customer(prob, sol, cid, take_kg, take_m3, rng)
            if ok:
                remain_kg -= take_kg
                remain_m3 -= take_m3
                splits += 1
            else:
                all_success = False
                break
    return all_success


# ========= 算子注册 =========

DESTROY_OPS = [
    ("random_removal", random_removal),
    ("worst_removal", worst_removal),
    ("shaw_removal", shaw_removal),
    ("route_removal", route_removal),
]

REPAIR_OPS = [
    ("greedy_insertion", greedy_insertion),
    ("random_insertion", random_insertion),
]


if __name__ == "__main__":
    from core.data_loader import load_problem
    from construct.spiral_init import spiral_construct
    from core.solution import evaluate_solution

    prob = load_problem()
    sol = spiral_construct(prob, clockwise=True, outward=True)
    total0, _ = evaluate_solution(prob, sol)
    print(f"初始解成本: {total0:.2f}")

    rng = random.Random(42)
    # 测试每个算子
    for name, op in DESTROY_OPS:
        sol_copy = deepcopy(sol)
        removed = op(prob, sol_copy, k=15, rng=rng)
        print(f"{name}: 移除了 {len(removed)} 个访问，路径数: {len(sol_copy.routes)}")

    # 完整 destroy + repair 流程
    print("\n=== Destroy + Repair 测试 ===")
    sol_copy = deepcopy(sol)
    removed = random_removal(prob, sol_copy, k=15, rng=rng)
    print(f"破坏后路径数: {len(sol_copy.routes)}, 移除 {len(removed)} 访问")
    ok = greedy_insertion(prob, sol_copy, removed, rng)
    total1, _ = evaluate_solution(prob, sol_copy)
    print(f"修复后路径数: {len(sol_copy.routes)}, 修复成功={ok}, 成本: {total1:.2f}")
    print(f"变化: {total1 - total0:+.2f}")
