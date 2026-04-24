"""
华中杯 A 题 - 问题 2 端到端求解脚本。

问题 2 在问题 1 基础上增加: 8:00-16:00 禁止燃油车进入绿色区.

策略:
    1. 用 tiered_construct_q2 (绿色区客户优先 EV) 生成初始解;
    2. ALNS 用 soft 模式跑 (违规加 1e6 罚项), 避免搜索早期全拒绝;
    3. 结束后切 hard 模式验证可行性, 并给出 Q1 vs Q2 对比表.

用法: python3 run_q2.py [--iters 1200] [--seed 42]
"""
from __future__ import annotations

import argparse
import pickle
import time
from copy import deepcopy
from typing import List

from core.data_loader import load_problem
from core.solution import (
    Solution,
    evaluate_solution,
    solution_summary,
)
from construct.tiered_init import tiered_construct
from construct.tiered_init_q2 import tiered_construct_q2
from construct.solution_utils import sort_routes_by_tw
from alns.main import run_alns, ALNSConfig


def solve_q2(max_iters: int = 1200, base_seed: int = 42, verbose: bool = True):
    """求解问题2: 带绿色区政策约束的车辆调度。"""
    prob = load_problem()
    # ALNS 用 soft 模式: 违规加 1e6 罚项, 既避免硬拒绝又强烈惩罚
    prob.policy_mode = "soft"

    if verbose:
        print(f"问题规模: {prob.n_customers} 客户, policy_mode={prob.policy_mode}")

    starts = [
        dict(clockwise=True,  outward=True),
        dict(clockwise=True,  outward=False),
        dict(clockwise=False, outward=True),
        dict(clockwise=False, outward=False),
    ]

    results = []
    for i, kwargs in enumerate(starts):
        init = tiered_construct_q2(prob, **kwargs)
        init = sort_routes_by_tw(prob, init)
        t0, init_details = evaluate_solution(prob, init)
        viol0 = sum(d.policy_violations for d in init_details)

        cfg = ALNSConfig(
            max_iterations=max_iters,
            initial_temp=5000,
            cooling_rate=0.997,
            destroy_min_frac=0.08,
            destroy_max_frac=0.25,
            segment_size=100,
            seed=base_seed + i * 100,
            verbose=False,
        )
        t_start = time.time()
        best, hist = run_alns(prob, init, cfg)
        t1, det = evaluate_solution(prob, best)
        viol1 = sum(d.policy_violations for d in det)
        elapsed = time.time() - t_start
        if verbose:
            print(f"  起点 {i} ({kwargs}): init={t0:.0f} (viol={viol0}) "
                  f"→ best={t1:.0f} (viol={viol1}) ({elapsed:.0f}s)")
        results.append((t1, best, hist, kwargs, viol1))

    # 取最优 (优先零违规, 其次成本低)
    results.sort(key=lambda x: (x[4] > 0, x[0]))
    best_cost, best_sol, best_hist, best_kwargs, best_viol = results[0]

    # 切 hard 模式验证
    prob.policy_mode = "hard"
    hard_cost, hard_details = evaluate_solution(prob, best_sol)
    hard_infeas = sum(1 for d in hard_details if not d.feasible)

    if verbose:
        print(f"\n{'='*60}")
        print(f"问题 2 最优解: {best_cost:.0f} (起点: {best_kwargs})")
        print(f"  soft 模式违规数: {best_viol}")
        print(f"  hard 模式下不可行路径: {hard_infeas}")
        print(f"{'='*60}")
        info = solution_summary(prob, best_sol)
        print(f"  路径数:      {info['num_routes']}")
        print(f"  可行/不可行: {info['num_feasible']}/{info['num_infeasible']}")
        print(f"  总里程:      {info['total_distance_km']:.1f} km")
        print(f"  总碳排:      {info['carbon_kg']:.1f} kg CO2")
        print(f"\n成本分解:")
        for k, label in [('start_cost', '启动'), ('energy_cost', '能耗'),
                         ('carbon_cost', '碳排'), ('early_cost', '早到'),
                         ('late_cost', '晚到'), ('policy_cost', '政策')]:
            pct = info[k] / info['total_cost'] * 100 if info['total_cost'] > 0 else 0
            print(f"  {label}:  {info[k]:10.0f} ({pct:.1f}%)")
        print(f"\n车队使用:")
        print(f"  EV 路径: {info['ev_routes']}; 燃油路径: {info['fuel_routes']}")
        for k, v in info['type_used'].items():
            print(f"  {k}: {v} 辆")

    return best_sol, best_hist, results


def compare_q1_q2(q1_path: str = "result_q1.pkl",
                  q2_sol: Solution | None = None,
                  q2_prob=None):
    """读取 Q1 结果, 对比 Q1 vs Q2."""
    import os
    if not os.path.exists(q1_path):
        print(f"\n[跳过 Q1 对比: {q1_path} 不存在]")
        return
    with open(q1_path, "rb") as f:
        q1 = pickle.load(f)
    q1_sol = q1["best"]

    # Q1 解在 Q1 环境 (policy_mode='off') 下评估
    prob_q1 = load_problem()
    q1_info = solution_summary(prob_q1, q1_sol)

    # 同一 Q1 解在 Q2 环境 (hard) 下会违规 - 展示未做政策调整的代价
    prob_q1_hard = load_problem()
    prob_q1_hard.policy_mode = "hard"
    q1_hard_cost, q1_hard_det = evaluate_solution(prob_q1_hard, q1_sol)
    q1_hard_viol = sum(d.policy_violations for d in q1_hard_det)
    q1_hard_infeas = sum(1 for d in q1_hard_det if not d.feasible)

    if q2_sol is not None:
        q2_info = solution_summary(q2_prob, q2_sol)
    else:
        q2_info = None

    print(f"\n{'='*60}")
    print("问题1 vs 问题2 对比表")
    print(f"{'='*60}")
    rows = [
        ("指标",            "Q1 (policy off)", "Q1→Q2 (硬检)", "Q2 最优解"),
        ("总成本 (元)",      f"{q1_info['total_cost']:.0f}",
                           f"{q1_hard_cost:.0f}",
                           f"{q2_info['total_cost']:.0f}" if q2_info else "-"),
        ("启动成本",         f"{q1_info['start_cost']:.0f}",
                           f"{q1_info['start_cost']:.0f}",
                           f"{q2_info['start_cost']:.0f}" if q2_info else "-"),
        ("能耗成本",         f"{q1_info['energy_cost']:.0f}",
                           f"{q1_info['energy_cost']:.0f}",
                           f"{q2_info['energy_cost']:.0f}" if q2_info else "-"),
        ("碳排成本",         f"{q1_info['carbon_cost']:.0f}",
                           f"{q1_info['carbon_cost']:.0f}",
                           f"{q2_info['carbon_cost']:.0f}" if q2_info else "-"),
        ("早到成本",         f"{q1_info['early_cost']:.0f}",
                           f"{q1_info['early_cost']:.0f}",
                           f"{q2_info['early_cost']:.0f}" if q2_info else "-"),
        ("晚到成本",         f"{q1_info['late_cost']:.0f}",
                           f"{q1_info['late_cost']:.0f}",
                           f"{q2_info['late_cost']:.0f}" if q2_info else "-"),
        ("政策违规路径",     "-",
                           f"{q1_hard_infeas}",
                           f"{q2_info['num_infeasible']}" if q2_info else "-"),
        ("总碳排 (kg)",      f"{q1_info['carbon_kg']:.1f}",
                           f"{q1_info['carbon_kg']:.1f}",
                           f"{q2_info['carbon_kg']:.1f}" if q2_info else "-"),
        ("路径数",           f"{q1_info['num_routes']}",
                           f"{q1_info['num_routes']}",
                           f"{q2_info['num_routes']}" if q2_info else "-"),
        ("EV 路径",          f"{q1_info['ev_routes']}",
                           f"{q1_info['ev_routes']}",
                           f"{q2_info['ev_routes']}" if q2_info else "-"),
        ("燃油路径",         f"{q1_info['fuel_routes']}",
                           f"{q1_info['fuel_routes']}",
                           f"{q2_info['fuel_routes']}" if q2_info else "-"),
    ]
    for row in rows:
        print(f"  {row[0]:<16} | {row[1]:>18} | {row[2]:>14} | {row[3]:>12}")

    # 车型分布对比
    print(f"\n  车型使用分布:")
    all_types = sorted(set(q1_info['type_used']) |
                       (set(q2_info['type_used']) if q2_info else set()))
    for t in all_types:
        n1 = q1_info['type_used'].get(t, 0)
        n2 = q2_info['type_used'].get(t, 0) if q2_info else 0
        print(f"    {t:<14}: Q1={n1:3d}  Q2={n2:3d}  Δ={n2 - n1:+d}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--iters', type=int, default=1200)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--out', default='result_q2.pkl')
    parser.add_argument('--q1', default='result_q1.pkl')
    args = parser.parse_args()

    best, hist, results = solve_q2(max_iters=args.iters, base_seed=args.seed)

    prob_final = load_problem()
    prob_final.policy_mode = "hard"

    with open(args.out, 'wb') as f:
        pickle.dump({
            'best': best,
            'history': hist,
            'all_results': [(r[0], r[3], r[4]) for r in results],
        }, f)
    print(f"\n结果已保存: {args.out}")

    compare_q1_q2(q1_path=args.q1, q2_sol=best, q2_prob=prob_final)
