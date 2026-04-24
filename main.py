"""
华中杯 A 题 - 统一命令行入口。

子命令:
    q1       求解问题1 (静态 VRP)
    q2       求解问题2 (绿色区限行政策)
    q3       求解问题3 (动态事件三场景)
    compare  打印 Q1 vs Q2 对比表 (需要已有 result_q1.pkl / result_q2.pkl)
    summary  打印所有已保存结果的简要摘要

示例:
    uv run python main.py --help
    uv run python main.py q1 --iters 1200
    uv run python main.py q2 --iters 1200
    uv run python main.py q3 --iters 200
    uv run python main.py compare
    uv run python main.py summary
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
import time
from typing import Optional

from core.data_loader import load_problem
from core.solution import evaluate_solution, solution_summary
from construct.tiered_init import tiered_construct
from construct.solution_utils import sort_routes_by_tw
from alns.main import run_alns, ALNSConfig


# ========= Q1 =========

def solve_q1(max_iters: int = 1200, base_seed: int = 42, verbose: bool = True):
    """求解问题1: 静态环境下的车辆调度。"""
    prob = load_problem()
    if verbose:
        print(f"[Q1] 问题规模: {prob.n_customers} 客户")

    starts = [
        dict(clockwise=True,  outward=True),
        dict(clockwise=True,  outward=False),
        dict(clockwise=False, outward=True),
        dict(clockwise=False, outward=False),
    ]

    results = []
    for i, kwargs in enumerate(starts):
        init = tiered_construct(prob, **kwargs)
        init = sort_routes_by_tw(prob, init)
        t0, _ = evaluate_solution(prob, init)

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
        t1, _ = evaluate_solution(prob, best)
        elapsed = time.time() - t_start
        if verbose:
            print(f"  起点 {i} ({kwargs}): init={t0:.0f} → best={t1:.0f} "
                  f"({elapsed:.0f}s)")
        results.append((t1, best, hist, kwargs))

    results.sort(key=lambda x: x[0])
    best_cost, best_sol, best_hist, best_kwargs = results[0]

    if verbose:
        _print_solution_report(prob, best_sol, title=f"问题 1 最优解 (起点 {best_kwargs})")

    return best_sol, best_hist, results


def cmd_q1(args):
    best, hist, results = solve_q1(max_iters=args.iters, base_seed=args.seed)
    with open(args.out, 'wb') as f:
        pickle.dump({
            'best': best,
            'history': hist,
            'all_results': [(r[0], r[3]) for r in results],
        }, f)
    print(f"\n[Q1] 结果已保存: {args.out}")


# ========= Q2 =========

def cmd_q2(args):
    from run_q2 import solve_q2, compare_q1_q2
    best, hist, results = solve_q2(max_iters=args.iters, base_seed=args.seed)
    prob_final = load_problem()
    prob_final.policy_mode = "hard"
    with open(args.out, 'wb') as f:
        pickle.dump({
            'best': best,
            'history': hist,
            'all_results': [(r[0], r[3], r[4]) for r in results],
        }, f)
    print(f"\n[Q2] 结果已保存: {args.out}")
    compare_q1_q2(q1_path=args.q1, q2_sol=best, q2_prob=prob_final)


# ========= Q3 =========

def cmd_q3(args):
    from run_q3 import run_all_scenarios
    results = run_all_scenarios(iters=args.iters, seed=args.seed)
    with open(args.out, 'wb') as f:
        pickle.dump({'results': results}, f)
    print(f"\n[Q3] 结果已保存: {args.out}")


# ========= Compare =========

def cmd_compare(args):
    from run_q2 import compare_q1_q2
    if not os.path.exists(args.q2):
        print(f"[compare] 找不到 Q2 结果: {args.q2}")
        sys.exit(1)
    with open(args.q2, 'rb') as f:
        q2 = pickle.load(f)
    prob = load_problem()
    prob.policy_mode = "hard"
    compare_q1_q2(q1_path=args.q1, q2_sol=q2['best'], q2_prob=prob)


# ========= Summary =========

def cmd_summary(args):
    """打印所有已保存结果的简要摘要。"""
    for tag, path in [("Q1", args.q1), ("Q2", args.q2), ("Q3", args.q3)]:
        if not os.path.exists(path):
            print(f"[{tag}]  {path} 不存在 - 跳过")
            continue
        with open(path, 'rb') as f:
            data = pickle.load(f)
        print(f"\n[{tag}] {path}")
        if tag in ("Q1", "Q2"):
            prob = load_problem()
            if tag == "Q2":
                prob.policy_mode = "hard"
            info = solution_summary(prob, data['best'])
            print(f"  总成本:   {info['total_cost']:.0f}")
            print(f"  路径数:   {info['num_routes']} "
                  f"(可行 {info['num_feasible']})")
            print(f"  总里程:   {info['total_distance_km']:.1f} km")
            print(f"  总碳排:   {info['carbon_kg']:.1f} kg CO2")
            print(f"  EV/燃油:  {info['ev_routes']}/{info['fuel_routes']}")
            if tag == "Q2":
                print(f"  违规数:   {info['policy_violations']}")
        else:  # Q3
            for r in data['results']:
                cost_d = r.cost_after_reopt - r.cost_before
                print(f"  {r.name}: {r.description}")
                print(f"    成本Δ={cost_d:+.0f}, 晚到Δ={r.late_after-r.late_before:+.0f}, "
                      f"改派率={r.reassigned_frac*100:.1f}%")


# ========= 辅助 =========

def _print_solution_report(prob, sol, title: str = ""):
    if title:
        print(f"\n{'='*60}")
        print(title)
        print('='*60)
    info = solution_summary(prob, sol)
    print(f"  总成本:   {info['total_cost']:.0f}")
    print(f"  路径数:   {info['num_routes']}")
    print(f"  可行/不可行: {info['num_feasible']}/{info['num_infeasible']}")
    print(f"  总里程:   {info['total_distance_km']:.1f} km")
    print(f"  总碳排:   {info['carbon_kg']:.1f} kg CO2")
    print(f"\n  成本分解:")
    for k, label in [('start_cost', '启动'), ('energy_cost', '能耗'),
                     ('carbon_cost', '碳排'), ('early_cost', '早到'),
                     ('late_cost', '晚到'), ('policy_cost', '政策')]:
        if k not in info:
            continue
        val = info[k]
        pct = val / info['total_cost'] * 100 if info['total_cost'] > 0 else 0
        print(f"    {label}:  {val:10.0f} ({pct:.1f}%)")
    print(f"\n  车型使用:")
    for k, v in sorted(info['type_used'].items()):
        print(f"    {k}: {v} 辆")


# ========= CLI =========

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="华中杯 A 题 - 城市绿色物流配送调度 (统一命令行入口)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  uv run python main.py q1 --iters 1200
  uv run python main.py q2 --iters 1200
  uv run python main.py q3 --iters 200
  uv run python main.py compare
  uv run python main.py summary
""",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="命令")

    # q1
    p1 = sub.add_parser("q1", help="求解问题1 (静态 VRP)")
    p1.add_argument("--iters", type=int, default=1200, help="每起点 ALNS 迭代数")
    p1.add_argument("--seed", type=int, default=42)
    p1.add_argument("--out", default="result_q1.pkl")
    p1.set_defaults(func=cmd_q1)

    # q2
    p2 = sub.add_parser("q2", help="求解问题2 (绿色区限行)")
    p2.add_argument("--iters", type=int, default=1200)
    p2.add_argument("--seed", type=int, default=42)
    p2.add_argument("--out", default="result_q2.pkl")
    p2.add_argument("--q1", default="result_q1.pkl", help="Q1 结果路径 (用于对比)")
    p2.set_defaults(func=cmd_q2)

    # q3
    p3 = sub.add_parser("q3", help="求解问题3 (动态三场景)")
    p3.add_argument("--iters", type=int, default=200, help="优化层 ALNS 迭代数")
    p3.add_argument("--seed", type=int, default=42)
    p3.add_argument("--out", default="result_q3.pkl")
    p3.set_defaults(func=cmd_q3)

    # compare
    pc = sub.add_parser("compare", help="Q1 vs Q2 对比表")
    pc.add_argument("--q1", default="result_q1.pkl")
    pc.add_argument("--q2", default="result_q2.pkl")
    pc.set_defaults(func=cmd_compare)

    # summary
    ps = sub.add_parser("summary", help="所有已保存结果的简要摘要")
    ps.add_argument("--q1", default="result_q1.pkl")
    ps.add_argument("--q2", default="result_q2.pkl")
    ps.add_argument("--q3", default="result_q3.pkl")
    ps.set_defaults(func=cmd_summary)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
