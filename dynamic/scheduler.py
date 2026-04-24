"""
动态事件调度器 (问题3).

核心思想: 双层决策.
  - 快速层 (秒级): Greedy/Regret 插入 + 可行性修补, 立即响应;
  - 优化层 (分钟级): 对响应后的解跑小步 ALNS (100~300 iter) 进一步降成本.

稳定性: 记录受影响客户与原承诺车辆的差异, 作为改派率指标.
"""
from __future__ import annotations

import time as _time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

from core.problem import Problem, Customer
from core.solution import Solution, Route, evaluate_solution, solution_summary
from dynamic.events import Event, Scenario
from alns.operators import (
    _insert_one_customer,
    _compute_available,
)
from alns.main import run_alns, ALNSConfig


# ========= 事件应用 =========

def _apply_new_order(prob: Problem, sol: Solution, ev: Event) -> None:
    """把新订单写入 Problem 里对应客户的 demand/tw."""
    c = prob.customers[ev.cid]
    c.demand_kg = float(ev.payload["demand_kg"])
    c.demand_m3 = float(ev.payload["demand_m3"])
    c.tw_start = float(ev.payload["tw_start"])
    c.tw_end = float(ev.payload["tw_end"])


def _apply_cancel(prob: Problem, sol: Solution, ev: Event) -> None:
    """从所有路径里移除该客户, 清理空路径."""
    cid = ev.cid
    for r in sol.routes:
        if cid in r.nodes:
            r.nodes = [n for n in r.nodes if n != cid]
            r.delivered_kg.pop(cid, None)
            r.delivered_m3.pop(cid, None)
    sol.routes = [r for r in sol.routes if len(r.nodes) > 2]
    # 需求清零, 避免后续 _demand_covered 误判
    prob.customers[cid].demand_kg = 0.0
    prob.customers[cid].demand_m3 = 0.0


def _apply_address_change(prob: Problem, sol: Solution, ev: Event) -> None:
    """更新客户坐标, 并更新距离矩阵的行列."""
    cid = ev.cid
    c = prob.customers[cid]
    new_x = float(ev.payload["x"])
    new_y = float(ev.payload["y"])
    c.x = new_x
    c.y = new_y
    # 重算 cid 到所有其他节点的距离
    for j, other in enumerate(prob.customers):
        d = float(np.hypot(new_x - other.x, new_y - other.y))
        prob.distance[cid, j] = d
        prob.distance[j, cid] = d
    prob.distance[cid, cid] = 0.0


def _apply_tw_change(prob: Problem, sol: Solution, ev: Event) -> None:
    """修改客户时间窗."""
    c = prob.customers[ev.cid]
    if ev.payload.get("tw_start") is not None:
        c.tw_start = float(ev.payload["tw_start"])
    if ev.payload.get("tw_end") is not None:
        c.tw_end = float(ev.payload["tw_end"])


_APPLIERS = {
    "new_order": _apply_new_order,
    "cancel_order": _apply_cancel,
    "address_change": _apply_address_change,
    "tw_change": _apply_tw_change,
}


# ========= 快速重调度层 =========

def _collect_pending_insertions(prob: Problem, sol: Solution) -> List[int]:
    """找出还没在当前解里但有需求的客户 id。"""
    covered = set()
    for r in sol.routes:
        covered.update(c for c in r.nodes if c != 0)
    pending = []
    for c in prob.customers[1:]:
        if c.demand_kg > 1e-6 and c.cid not in covered:
            pending.append(c.cid)
    return pending


def fast_repair(prob: Problem, sol: Solution,
                rng_seed: int = 0) -> Tuple[Solution, int]:
    """快速层: 对当前未覆盖客户做贪心插入.
    返回 (修补后的解, 成功插入客户数)."""
    import random

    rng = random.Random(rng_seed)
    pending = _collect_pending_insertions(prob, sol)
    inserted = 0
    for cid in pending:
        c = prob.customers[cid]
        ok = _insert_one_customer(prob, sol, cid, c.demand_kg, c.demand_m3, rng)
        if ok:
            inserted += 1
    return sol, inserted


def reoptimize(prob: Problem, sol: Solution,
               iterations: int = 200, seed: int = 17) -> Solution:
    """优化层: 跑小步 ALNS."""
    cfg = ALNSConfig(
        max_iterations=iterations,
        initial_temp=1500,
        cooling_rate=0.993,
        destroy_min_frac=0.05,
        destroy_max_frac=0.18,
        segment_size=50,
        seed=seed,
        verbose=False,
    )
    best, _ = run_alns(prob, sol, cfg)
    return best


# ========= 稳定性度量 =========

def _customer_route_signature(sol: Solution) -> Dict[int, frozenset]:
    """客户 → 同车伙伴集合 (路径内其他客户的 frozenset).
    这是对路径索引鲁棒的结构指纹 - 只要伙伴没变, 就算同一路径."""
    m: Dict[int, frozenset] = {}
    for r in sol.routes:
        cids_in_route = frozenset(c for c in r.nodes if c != 0)
        for cid in cids_in_route:
            # 同一客户可能在多条路径 (SDVRP); 这里取"所有伙伴的并集"
            existing = m.get(cid, frozenset())
            m[cid] = existing | (cids_in_route - {cid})
    return m


def stability_delta(before: Solution, after: Solution) -> float:
    """改派比例 = 同车伙伴集合发生变化的客户数 / 客户总数."""
    m1 = _customer_route_signature(before)
    m2 = _customer_route_signature(after)
    all_cids = set(m1) | set(m2)
    if not all_cids:
        return 0.0
    changed = 0
    for cid in all_cids:
        if m1.get(cid, frozenset()) != m2.get(cid, frozenset()):
            changed += 1
    return changed / len(all_cids)


# ========= 场景执行器 =========

@dataclass
class ScenarioResult:
    name: str
    description: str
    cost_before: float
    cost_after_fast: float
    cost_after_reopt: float
    response_ms_fast: float
    response_ms_reopt: float
    late_before: float
    late_after: float
    routes_before: int
    routes_after: int
    reassigned_frac: float
    num_events: int
    events_by_type: Dict[str, int] = field(default_factory=dict)


def apply_scenario(
    base_prob: Problem,
    base_sol: Solution,
    scenario: Scenario,
    reopt_iters: int = 200,
    verbose: bool = True,
) -> Tuple[Solution, ScenarioResult]:
    """在基础解之上执行一个场景并给出指标.

    步骤:
        1. clone 问题和解 (隔离, 不污染基础解);
        2. 依序应用所有事件;
        3. 快速层: 贪心插入新增客户;
        4. 优化层: 小步 ALNS;
        5. 算指标.
    """
    prob = deepcopy(base_prob)
    sol = deepcopy(base_sol)

    before_snapshot = deepcopy(sol)

    # 基线成本
    cost_before, before_details = evaluate_solution(base_prob, base_sol)
    late_before = sum(d.late_cost for d in before_details)

    # 应用事件
    ev_counts: Dict[str, int] = {}
    for ev in scenario.events:
        _APPLIERS[ev.etype](prob, sol, ev)
        ev_counts[ev.etype] = ev_counts.get(ev.etype, 0) + 1

    # 快速层
    t0 = _time.time()
    sol, inserted = fast_repair(prob, sol, rng_seed=19)
    t_fast_ms = (_time.time() - t0) * 1000
    cost_fast, _ = evaluate_solution(prob, sol)

    # 优化层
    t0 = _time.time()
    sol = reoptimize(prob, sol, iterations=reopt_iters)
    t_reopt_ms = (_time.time() - t0) * 1000
    cost_reopt, after_details = evaluate_solution(prob, sol)
    late_after = sum(d.late_cost for d in after_details)

    # 稳定性
    reassigned = stability_delta(before_snapshot, sol)

    res = ScenarioResult(
        name=scenario.name,
        description=scenario.description,
        cost_before=cost_before,
        cost_after_fast=cost_fast,
        cost_after_reopt=cost_reopt,
        response_ms_fast=t_fast_ms,
        response_ms_reopt=t_reopt_ms,
        late_before=late_before,
        late_after=late_after,
        routes_before=len(before_snapshot.routes),
        routes_after=len(sol.routes),
        reassigned_frac=reassigned,
        num_events=len(scenario.events),
        events_by_type=ev_counts,
    )

    if verbose:
        print(f"[{scenario.name}] {scenario.description}")
        print(f"  事件数: {res.num_events}  分类: {res.events_by_type}")
        print(f"  成本: {cost_before:.0f} → (快速) {cost_fast:.0f} "
              f"→ (优化) {cost_reopt:.0f}  Δ={cost_reopt - cost_before:+.0f}")
        print(f"  响应时间: 快速 {t_fast_ms:.1f}ms, 优化 {t_reopt_ms:.1f}ms")
        print(f"  晚到: {late_before:.0f} → {late_after:.0f}")
        print(f"  路径数: {res.routes_before} → {res.routes_after}  "
              f"改派率: {reassigned*100:.1f}%")

    return sol, res
