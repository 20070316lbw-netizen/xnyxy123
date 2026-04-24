"""
华中杯 A 题 - 问题 3 端到端求解脚本.

问题 3: 配送过程中出现动态事件, 设计实时调度策略并给出示例结果.

三个标准场景 (可直接写进论文):
    S1 新增订单潮   - 12:00 系统收到 8 个新订单 (来自原幽灵客户)
    S2 时间窗突变   - 若干核心客户最晚到达时间提前 30-60 分钟
    S3 复合事件     - 新增 + 取消 + 时间窗调整同时发生

用法: python3 run_q3.py [--iters 200] [--seed 42]
"""
from __future__ import annotations

import argparse
import pickle
import random
from copy import deepcopy

from core.data_loader import load_problem
from core.solution import Solution, evaluate_solution
from dynamic.events import (
    Scenario,
    make_new_order,
    make_cancel,
    make_tw_change,
    make_address_change,
)
from dynamic.scheduler import apply_scenario, ScenarioResult


# ========= 获取 Q1 基础解 =========

def get_base_solution(q1_path: str = "result_q1.pkl") -> tuple:
    """读取 Q1 结果; 若文件不存在则现场跑一次 ALNS."""
    import os
    prob = load_problem()
    if os.path.exists(q1_path):
        with open(q1_path, "rb") as f:
            q1 = pickle.load(f)
        return prob, q1["best"]
    # Fallback: 跑一次
    print(f"[未找到 {q1_path}, 现场跑 Q1 ALNS]")
    from construct.tiered_init import tiered_construct
    from construct.solution_utils import sort_routes_by_tw
    from alns.main import run_alns, ALNSConfig

    init = tiered_construct(prob, clockwise=True, outward=True)
    init = sort_routes_by_tw(prob, init)
    best, _ = run_alns(prob, init, ALNSConfig(
        max_iterations=800, seed=42, verbose=False
    ))
    return prob, best


# ========= 场景定义 =========

GHOST_CIDS = [1, 14, 15, 17, 18, 20, 21, 22, 23, 96]


def build_scenario_s1(prob, rng: random.Random) -> Scenario:
    """S1 新增订单潮: 12:00 时 8 个幽灵客户产生订单."""
    chosen = rng.sample(GHOST_CIDS, 8)
    events = []
    for cid in chosen:
        # 随机需求: 200-1200 kg, 1-8 m³, 时间窗 13:00-18:00 内随机
        dk = rng.uniform(200, 1200)
        dm = dk / 100  # 粗略 1kg / 0.01m³
        ts = rng.uniform(13.0, 16.0)
        te = ts + rng.uniform(1.0, 2.5)
        events.append(make_new_order(
            time=12.0, cid=cid,
            demand_kg=dk, demand_m3=dm, tw_start=ts, tw_end=te,
        ))
    return Scenario(
        name="S1",
        description="12:00 新增订单潮 (8 个幽灵客户产生订单)",
        events=events,
    )


def build_scenario_s2(prob, rng: random.Random) -> Scenario:
    """S2 时间窗突变: 10 个已有订单客户 tw_end 前移 30-60 分钟."""
    # 选一批有订单且时间窗 > 10:30 开始的客户 (提前影响明显)
    cands = [c.cid for c in prob.customers[1:]
             if c.demand_kg > 0 and c.tw_end > 10.5 and c.tw_end < 18.0]
    chosen = rng.sample(cands, min(10, len(cands)))
    events = []
    for cid in chosen:
        shift_min = rng.uniform(30, 60)
        new_end = prob.customers[cid].tw_end - shift_min / 60
        events.append(make_tw_change(
            time=9.0, cid=cid, tw_end=new_end,
        ))
    return Scenario(
        name="S2",
        description=f"时间窗突变 (10 个客户 tw_end 前移 30-60min)",
        events=events,
    )


def build_scenario_s3(prob, rng: random.Random) -> Scenario:
    """S3 复合: 4 新增 + 3 取消 + 5 时间窗调整."""
    events = []

    # 4 新增
    new_ghosts = rng.sample(GHOST_CIDS, 4)
    for cid in new_ghosts:
        dk = rng.uniform(300, 900)
        dm = dk / 100
        ts = rng.uniform(13.0, 15.0)
        te = ts + rng.uniform(1.0, 2.0)
        events.append(make_new_order(
            time=11.0, cid=cid,
            demand_kg=dk, demand_m3=dm, tw_start=ts, tw_end=te,
        ))

    # 3 取消 (挑已有订单客户中的几个)
    active = [c.cid for c in prob.customers[1:]
              if c.demand_kg > 0 and c.cid not in GHOST_CIDS]
    cancels = rng.sample(active, 3)
    for cid in cancels:
        events.append(make_cancel(time=10.5, cid=cid))

    # 5 时间窗调整
    tw_cands = [c for c in active if c not in cancels]
    tw_chosen = rng.sample(tw_cands, 5)
    for cid in tw_chosen:
        shift_min = rng.uniform(30, 50)
        new_end = prob.customers[cid].tw_end - shift_min / 60
        events.append(make_tw_change(
            time=9.5, cid=cid, tw_end=new_end,
        ))

    return Scenario(
        name="S3",
        description="复合事件 (4 新增 + 3 取消 + 5 时间窗前移)",
        events=events,
    )


# ========= 运行 =========

def run_all_scenarios(iters: int, seed: int) -> list[ScenarioResult]:
    rng = random.Random(seed)
    prob, base_sol = get_base_solution()

    print(f"{'='*64}")
    print(f"基础 Q1 解: {evaluate_solution(prob, base_sol)[0]:.0f} 元, "
          f"{len(base_sol.routes)} 条路径")
    print(f"{'='*64}\n")

    scenarios = [
        build_scenario_s1(prob, rng),
        build_scenario_s2(prob, rng),
        build_scenario_s3(prob, rng),
    ]

    results = []
    for sc in scenarios:
        _, res = apply_scenario(prob, base_sol, sc,
                                reopt_iters=iters, verbose=True)
        results.append(res)
        print()

    # 汇总
    print(f"{'='*64}")
    print("问题 3 场景汇总")
    print(f"{'='*64}")
    header = f"{'场景':<6} {'事件':>5} {'成本Δ':>10} {'晚到Δ':>10} {'响应ms':>10} {'改派%':>8}"
    print(header)
    print("-" * len(header))
    for r in results:
        cost_d = r.cost_after_reopt - r.cost_before
        late_d = r.late_after - r.late_before
        print(f"{r.name:<6} {r.num_events:>5d} {cost_d:>+10.0f} "
              f"{late_d:>+10.0f} {r.response_ms_fast + r.response_ms_reopt:>10.0f} "
              f"{r.reassigned_frac*100:>7.1f}%")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--iters', type=int, default=200,
                        help="每个场景优化层 ALNS 迭代数")
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--out', default='result_q3.pkl')
    args = parser.parse_args()

    results = run_all_scenarios(iters=args.iters, seed=args.seed)

    with open(args.out, 'wb') as f:
        pickle.dump({'results': results}, f)
    print(f"\n结果已保存: {args.out}")
