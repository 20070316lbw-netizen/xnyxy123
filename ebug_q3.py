# debug_q3.py
import pickle
import random
from copy import deepcopy
from core.data_loader import load_problem
from core.solution import evaluate_solution
from dynamic.events import Scenario, make_new_order
from dynamic.scheduler import _apply_new_order, fast_repair, _collect_pending_insertions
from alns.main import _demand_covered

prob = load_problem()
with open("result_q1.pkl", "rb") as f:
    q1 = pickle.load(f)
base_sol = q1["best"]

# 复现S1场景（固定seed=42）
rng = random.Random(42)
GHOST_CIDS = [1, 14, 15, 17, 18, 20, 21, 22, 23, 96]
chosen = rng.sample(GHOST_CIDS, 8)
print(f"S1选中的幽灵客户: {chosen}")

from copy import deepcopy

prob2 = deepcopy(prob)
sol2 = deepcopy(base_sol)

for cid in chosen:
    dk = rng.uniform(200, 1200)
    dm = dk / 100
    ts = rng.uniform(13.0, 16.0)
    te = ts + rng.uniform(1.0, 2.5)
    from dynamic.events import Event

    ev = Event(
        time=12.0,
        etype="new_order",
        cid=cid,
        payload=dict(demand_kg=dk, demand_m3=dm, tw_start=ts, tw_end=te),
    )
    _apply_new_order(prob2, sol2, ev)

print(f"事件应用后，pending插入客户: {_collect_pending_insertions(prob2, sol2)}")

sol2, inserted = fast_repair(prob2, sol2, rng_seed=19)
print(f"fast_repair插入了 {inserted} 个客户")

cost, details = evaluate_solution(prob2, sol2)
feasible_all = all(d.feasible for d in details)
print(f"fast_repair后: 成本={cost:.0f}, 全部可行={feasible_all}")
print(f"_demand_covered: {_demand_covered(prob2, sol2)}")

# 检查每个新增客户是否在解里
for cid in chosen:
    in_sol = any(cid in r.nodes for r in sol2.routes)
    demand = prob2.customers[cid].demand_kg
    delivered = sum(r.delivered_kg.get(cid, 0) for r in sol2.routes)
    print(
        f"  c{cid}: demand={demand:.1f}, delivered={delivered:.1f}, in_routes={in_sol}"
    )
