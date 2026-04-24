"""
ALNS 主循环实现。

流程:
    初始解 → 主循环 N 次迭代:
        1. 按当前权重随机选 destroy + repair 算子
        2. 拷贝当前解, 应用破坏 + 修复
        3. 评估新解, 按模拟退火接受判据
        4. 更新算子分数 (找到新全局最优 → 大奖, 次优 → 中奖, 接受劣解 → 小奖)
        5. 每 segment_size 次迭代更新算子权重
    → 返回最优解 + 收敛历史
"""
from __future__ import annotations

import math
import random
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Tuple, Callable

from core.problem import Problem
from core.solution import Solution, evaluate_solution
from alns.operators import DESTROY_OPS, REPAIR_OPS


# ========= 配置 =========

@dataclass
class ALNSConfig:
    max_iterations: int = 2000
    # 破坏客户数比例 (总访问的 5%~20%)
    destroy_min_frac: float = 0.05
    destroy_max_frac: float = 0.20
    # 模拟退火
    initial_temp: float = 1000.0
    cooling_rate: float = 0.9985  # T *= cooling_rate 每次迭代
    min_temp: float = 1.0
    # 自适应权重
    segment_size: int = 100     # 每多少次迭代更新一次权重
    reaction_factor: float = 0.3  # w_new = (1-λ)*w_old + λ * score/used
    # 打分
    score_best: float = 33.0    # 找到新全局最优
    score_better: float = 13.0  # 比当前解好
    score_accepted: float = 9.0  # 被 SA 接受
    score_rejected: float = 0.0
    # 随机种子
    seed: int = 42
    # 日志
    verbose: bool = True


# ========= 运行统计 =========

@dataclass
class ALNSHistory:
    best_costs: List[float] = field(default_factory=list)      # 每次迭代的全局最优
    current_costs: List[float] = field(default_factory=list)   # 每次迭代的当前解成本
    temps: List[float] = field(default_factory=list)
    accept_types: List[str] = field(default_factory=list)      # 'best'/'better'/'accepted'/'rejected'
    destroy_weights: List[List[float]] = field(default_factory=list)
    repair_weights: List[List[float]] = field(default_factory=list)
    elapsed_s: float = 0.0


# ========= 主函数 =========

def run_alns(
    prob: Problem,
    initial_sol: Solution,
    config: ALNSConfig | None = None,
) -> Tuple[Solution, ALNSHistory]:
    if config is None:
        config = ALNSConfig()
    rng = random.Random(config.seed)

    # 初始化
    current_sol = deepcopy(initial_sol)
    current_cost, init_details = evaluate_solution(prob, current_sol)
    init_feasible = all(d.feasible for d in init_details)
    best_sol = deepcopy(current_sol)
    # 如果初始解可行, best_cost=实际成本; 否则 best_cost=+∞, 等待 ALNS 找到可行解
    best_cost = current_cost if init_feasible else math.inf

    # 算子权重与计数
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

    # 访问总数用于计算 destroy 大小
    total_visits = sum(len(r.nodes) - 2 for r in current_sol.routes)

    t_start = time.time()

    for it in range(config.max_iterations):
        # ---- 选算子 (按权重加权随机) ----
        d_idx = _weighted_choice(d_weights, rng)
        r_idx = _weighted_choice(r_weights, rng)
        d_name, d_op = DESTROY_OPS[d_idx]
        r_name, r_op = REPAIR_OPS[r_idx]

        # ---- 破坏大小 k ----
        frac = rng.uniform(config.destroy_min_frac, config.destroy_max_frac)
        k = max(2, int(total_visits * frac))

        # ---- 应用破坏+修复 ----
        candidate = deepcopy(current_sol)
        try:
            removed = d_op(prob, candidate, k, rng)
            repair_ok = r_op(prob, candidate, removed, rng)
        except Exception as e:
            # 不稳定算子? 记日志, 继续
            if config.verbose and it % 100 == 0:
                print(f"  [it {it}] {d_name}+{r_name} exception: {e}")
            repair_ok = False

        # ---- 检查修复是否成功 (所有需求都被配送) ----
        if not _demand_covered(prob, candidate):
            # 修复失败: 算子未能把所有客户送回路径
            d_uses[d_idx] += 1
            r_uses[r_idx] += 1
            history.best_costs.append(best_cost)
            history.current_costs.append(current_cost)
            history.temps.append(T)
            history.accept_types.append("rejected")
            T = max(config.min_temp, T * config.cooling_rate)
            continue

        # 计算候选解的"带惩罚成本": 基础成本 + 不可行路径的额外罚款
        cand_cost, cand_details = evaluate_solution(prob, candidate)
        penalty = _infeasibility_penalty(candidate, cand_details)
        cand_score = cand_cost + penalty
        cand_all_feasible = penalty == 0

        # 为 best 比较的"真实成本": 如果整体可行就用 cand_cost, 否则用 +∞
        cand_best_cost = cand_cost if cand_all_feasible else math.inf

        # ---- 接受判定 ----
        # 当前解的带惩罚分数
        curr_details = evaluate_solution(prob, current_sol)[1]
        curr_penalty = _infeasibility_penalty(current_sol, curr_details)
        curr_score = current_cost + curr_penalty

        accept_type = "rejected"
        score_delta = 0.0

        # Best 只能由完全可行的解更新
        if cand_all_feasible and cand_best_cost < best_cost - 1e-6:
            best_sol = deepcopy(candidate)
            best_cost = cand_best_cost
            current_sol = candidate
            current_cost = cand_cost
            accept_type = "best"
            score_delta = config.score_best
        elif cand_score < curr_score - 1e-6:
            # 带惩罚成本比当前好
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

        # ---- 记录 ----
        history.best_costs.append(best_cost)
        history.current_costs.append(current_cost)
        history.temps.append(T)
        history.accept_types.append(accept_type)

        # ---- 更新权重 (每 segment 次) ----
        if (it + 1) % config.segment_size == 0:
            for i in range(n_destroy):
                if d_uses[i] > 0:
                    d_weights[i] = (
                        (1 - config.reaction_factor) * d_weights[i]
                        + config.reaction_factor * (d_scores[i] / d_uses[i])
                    )
                    d_weights[i] = max(d_weights[i], 0.1)  # 下限
            for i in range(n_repair):
                if r_uses[i] > 0:
                    r_weights[i] = (
                        (1 - config.reaction_factor) * r_weights[i]
                        + config.reaction_factor * (r_scores[i] / r_uses[i])
                    )
                    r_weights[i] = max(r_weights[i], 0.1)
            history.destroy_weights.append(list(d_weights))
            history.repair_weights.append(list(r_weights))
            d_scores = [0.0] * n_destroy
            r_scores = [0.0] * n_repair
            d_uses = [0] * n_destroy
            r_uses = [0] * n_repair

            if config.verbose:
                print(f"[it {it+1}/{config.max_iterations}] "
                      f"best={best_cost:.0f} curr={current_cost:.0f} T={T:.1f}")

        T = max(config.min_temp, T * config.cooling_rate)

    history.elapsed_s = time.time() - t_start
    return best_sol, history


def _weighted_choice(weights: List[float], rng: random.Random) -> int:
    total = sum(weights)
    if total <= 0:
        return rng.randint(0, len(weights) - 1)
    r = rng.random() * total
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if r < acc:
            return i
    return len(weights) - 1


def _demand_covered(prob: Problem, sol: Solution) -> bool:
    """检查每个客户需求是否被完全配送。"""
    tol = 1e-3
    for c in prob.customers[1:]:
        if c.demand_kg <= 0:
            continue
        delivered = sum(r.delivered_kg.get(c.cid, 0.0) for r in sol.routes)
        if delivered < c.demand_kg - tol:
            return False
    return True


def _infeasibility_penalty(sol: Solution, details) -> float:
    """对不可行路径的惩罚: 每条不可行路径罚 2000 元。
    这让 ALNS 倾向于避免不可行解, 但仍允许短暂探索。"""
    penalty = 0.0
    for d in details:
        if not d.feasible:
            penalty += 2000.0
    return penalty


if __name__ == "__main__":
    from core.data_loader import load_problem
    from construct.spiral_init import spiral_construct

    prob = load_problem()
    init_sol = spiral_construct(prob, clockwise=True, outward=True)
    total0, _ = evaluate_solution(prob, init_sol)
    print(f"初始解成本: {total0:.2f}")

    # 小规模测试: 500 次迭代先看看是否稳定
    config = ALNSConfig(
        max_iterations=500,
        initial_temp=5000,
        cooling_rate=0.995,
        segment_size=50,
        verbose=True,
    )
    best_sol, history = run_alns(prob, init_sol, config)
    total1, _ = evaluate_solution(prob, best_sol)
    print(f"\n最终成本: {total1:.2f}")
    print(f"下降: {total0 - total1:.2f} ({(total0-total1)/total0*100:.1f}%)")
    print(f"用时: {history.elapsed_s:.1f}s")
    print(f"路径数: 初始 {len(init_sol.routes)} → 最终 {len(best_sol.routes)}")
