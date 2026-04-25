"""
增强 ALNS v2:
    - 每次 repair 后自动跑局部搜索 (2-opt + relocate + merge)
    - 大幅破坏: destroy_frac 0.15-0.40
    - 无改进时重启: 如果 N 轮没找到新 best, 从 best 重启 (current=best)
    - 多初始解: 跑两个方向(内外/外内)分别做 ALNS, 取更好的
"""
from __future__ import annotations

import math
import random
import time
from copy import deepcopy
from typing import List, Tuple

from core.problem import Problem
from core.solution import Solution, evaluate_solution
from alns.operators import DESTROY_OPS, REPAIR_OPS
from alns.operators_v2 import local_search
from alns.main import (
    ALNSConfig, ALNSHistory, _weighted_choice,
    _demand_covered, _infeasibility_penalty,
)


def run_alns_v2(
    prob: Problem,
    initial_sol: Solution,
    config: ALNSConfig | None = None,
    do_local_search: bool = True,
    restart_after_no_improve: int = 500,
) -> Tuple[Solution, ALNSHistory]:
    if config is None:
        config = ALNSConfig()
    rng = random.Random(config.seed)

    current_sol = deepcopy(initial_sol)
    current_cost, init_details = evaluate_solution(prob, current_sol)
    init_feasible = all(d.feasible for d in init_details)
    best_sol = deepcopy(current_sol)
    best_cost = current_cost if init_feasible else math.inf

    n_destroy = len(DESTROY_OPS)
    n_repair = len(REPAIR_OPS)
    d_weights = [1.0] * n_destroy
    r_weights = [1.0] * n_repair
    d_scores = [0.0] * n_destroy
    r_scores = [0.0] * n_repair
    d_uses = [0] * n_destroy
    r_uses = [0] * n_repair

    T = config.initial_temp
    history = ALNSHistory()
    total_visits = sum(len(r.nodes) - 2 for r in current_sol.routes)

    t_start = time.time()
    iter_since_improve = 0

    for it in range(config.max_iterations):
        d_idx = _weighted_choice(d_weights, rng)
        r_idx = _weighted_choice(r_weights, rng)
        d_name, d_op = DESTROY_OPS[d_idx]
        r_name, r_op = REPAIR_OPS[r_idx]

        frac = rng.uniform(config.destroy_min_frac, config.destroy_max_frac)
        k = max(2, int(total_visits * frac))

        candidate = deepcopy(current_sol)
        try:
            removed = d_op(prob, candidate, k, rng)
            repair_ok = r_op(prob, candidate, removed, rng)
        except Exception:
            repair_ok = False

        if not _demand_covered(prob, candidate):
            repair_ok = False

        if not repair_ok:
            d_uses[d_idx] += 1
            r_uses[r_idx] += 1
            history.best_costs.append(best_cost if not math.isinf(best_cost) else current_cost)
            history.current_costs.append(current_cost)
            history.temps.append(T)
            history.accept_types.append("rejected")
            T = max(config.min_temp, T * config.cooling_rate)
            iter_since_improve += 1
            continue

        # 在 repair 后做局部搜索精修 (每 5 轮做一次, 避免太慢)
        if do_local_search and it % 5 == 0:
            candidate = local_search(prob, candidate, rng,
                                      do_2opt=True, do_relocate=True, do_merge=(it % 20 == 0))

        cand_cost, cand_details = evaluate_solution(prob, candidate)
        penalty = _infeasibility_penalty(candidate, cand_details)
        cand_all_feasible = penalty == 0
        cand_score = cand_cost + penalty
        cand_best_cost = cand_cost if cand_all_feasible else math.inf

        curr_details = evaluate_solution(prob, current_sol)[1]
        curr_penalty = _infeasibility_penalty(current_sol, curr_details)
        curr_score = current_cost + curr_penalty

        accept_type = "rejected"
        score_delta = 0.0
        if cand_all_feasible and cand_best_cost < best_cost - 1e-6:
            best_sol = deepcopy(candidate)
            best_cost = cand_best_cost
            current_sol = candidate
            current_cost = cand_cost
            accept_type = "best"
            score_delta = config.score_best
            iter_since_improve = 0
        elif cand_score < curr_score - 1e-6:
            current_sol = candidate
            current_cost = cand_cost
            accept_type = "better"
            score_delta = config.score_better
        else:
            delta = cand_score - curr_score
            if rng.random() < math.exp(-delta / max(T, 1e-9)):
                current_sol = candidate
                current_cost = cand_cost
                accept_type = "accepted"
                score_delta = config.score_accepted
            else:
                accept_type = "rejected"
                score_delta = config.score_rejected

        d_scores[d_idx] += score_delta
        r_scores[r_idx] += score_delta
        d_uses[d_idx] += 1
        r_uses[r_idx] += 1

        history.best_costs.append(best_cost if not math.isinf(best_cost) else current_cost)
        history.current_costs.append(current_cost)
        history.temps.append(T)
        history.accept_types.append(accept_type)

        # 重启机制
        if accept_type != "best":
            iter_since_improve += 1
        if iter_since_improve >= restart_after_no_improve:
            # 重启: current = best, T 升温
            current_sol = deepcopy(best_sol)
            current_cost = best_cost
            T = config.initial_temp * 0.5
            iter_since_improve = 0
            if config.verbose:
                print(f"  [it {it}] RESTART from best ({best_cost:.0f}), T={T:.0f}")

        if (it + 1) % config.segment_size == 0:
            for i in range(n_destroy):
                if d_uses[i] > 0:
                    d_weights[i] = ((1 - config.reaction_factor) * d_weights[i]
                                     + config.reaction_factor * (d_scores[i] / d_uses[i]))
                    d_weights[i] = max(d_weights[i], 0.1)
            for i in range(n_repair):
                if r_uses[i] > 0:
                    r_weights[i] = ((1 - config.reaction_factor) * r_weights[i]
                                     + config.reaction_factor * (r_scores[i] / r_uses[i]))
                    r_weights[i] = max(r_weights[i], 0.1)
            history.destroy_weights.append(list(d_weights))
            history.repair_weights.append(list(r_weights))
            d_scores = [0.0] * n_destroy
            r_scores = [0.0] * n_repair
            d_uses = [0] * n_destroy
            r_uses = [0] * n_repair
            if config.verbose:
                print(f"[it {it+1}] best={best_cost:.0f} curr={current_cost:.0f} "
                      f"T={T:.0f} no_improve={iter_since_improve}")

        T = max(config.min_temp, T * config.cooling_rate)

    history.elapsed_s = time.time() - t_start
    # 最后做一次完整局部搜索
    best_sol = local_search(prob, best_sol, rng, do_2opt=True, do_relocate=True, do_merge=True)
    return best_sol, history


if __name__ == "__main__":
    from core.data_loader import load_problem
    from construct.tiered_init import tiered_construct
    from construct.solution_utils import sort_routes_by_tw
    import pickle

    prob = load_problem()
    init = tiered_construct(prob, clockwise=True, outward=True)
    init = sort_routes_by_tw(prob, init)
    t0, _ = evaluate_solution(prob, init)
    print(f"初始: {t0:.0f}")

    cfg = ALNSConfig(
        max_iterations=1500,
        initial_temp=6000,
        cooling_rate=0.997,
        destroy_min_frac=0.08,
        destroy_max_frac=0.30,  # 更大扰动
        segment_size=100,
        verbose=True,
    )
    best, hist = run_alns_v2(prob, init, cfg, do_local_search=True, restart_after_no_improve=300)
    t1, _ = evaluate_solution(prob, best)
    print(f"\n最终: {t1:.0f} ({(t0-t1)/t0*100:+.1f}%), 用时 {hist.elapsed_s:.1f}s")
    # 保存
    with open('/home/claude/vrp/result_q1_v2.pkl', 'wb') as f:
        pickle.dump({'init_cost': t0, 'best': best, 'history': hist}, f)
