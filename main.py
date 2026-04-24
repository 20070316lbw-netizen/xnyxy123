"""
华中杯 A 题 - 问题 1 端到端求解脚本。

用法: python3 main.py [--iters 1200] [--seed 42]

流程:
    1. 加载数据 (4 个 xlsx → Problem 对象)
    2. 分层构造初始解 (大/中/小客户分别处理)
    3. 内部按时间窗排序 (一个便宜的预优化)
    4. 多起点 ALNS: 用4种方向组合分别跑, 取最优
    5. 输出成本分解 + 保存结果
"""
import argparse
import pickle
import time

from core.data_loader import load_problem
from core.solution import evaluate_solution, solution_summary
from construct.tiered_init import tiered_construct
from construct.solution_utils import sort_routes_by_tw
from alns.main import run_alns, ALNSConfig


def solve_q1(max_iters: int = 1200, base_seed: int = 42, verbose: bool = True):
    """求解问题1: 静态环境下的车辆调度。"""
    prob = load_problem()
    if verbose:
        print(f"问题规模: {prob.n_customers} 客户")

    # 多起点: 4 个 (顺/逆 × 内外/外内)
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

    # 取最优
    results.sort(key=lambda x: x[0])
    best_cost, best_sol, best_hist, best_kwargs = results[0]

    if verbose:
        print(f"\n{'='*60}")
        print(f"问题 1 最优解: {best_cost:.0f} (起点: {best_kwargs})")
        print(f"{'='*60}")
        info = solution_summary(prob, best_sol)
        print(f"  路径数:      {info['num_routes']}")
        print(f"  可行/不可行: {info['num_feasible']}/{info['num_infeasible']}")
        print(f"  总里程:      {info['total_distance_km']:.1f} km")
        print(f"  总碳排:      {info['carbon_kg']:.1f} kg CO2")
        print(f"\n成本分解:")
        for k, label in [('start_cost', '启动'), ('energy_cost', '能耗'),
                          ('carbon_cost', '碳排'), ('early_cost', '早到'),
                          ('late_cost', '晚到')]:
            pct = info[k] / info['total_cost'] * 100
            print(f"  {label}:  {info[k]:8.0f} ({pct:.1f}%)")
        print(f"\n车型使用:")
        for k, v in info['type_used'].items():
            print(f"  {k}: {v} 辆")

    return best_sol, best_hist, results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--iters', type=int, default=1200,
                         help="每个起点的 ALNS 迭代数")
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--out', default='result_q1.pkl',
                         help="结果保存路径 (pickle)")
    args = parser.parse_args()

    best, hist, results = solve_q1(max_iters=args.iters, base_seed=args.seed)

    with open(args.out, 'wb') as f:
        pickle.dump({
            'best': best,
            'history': hist,
            'all_results': [(r[0], r[3]) for r in results],
        }, f)
    print(f"\n结果已保存: {args.out}")
