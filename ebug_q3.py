# debug_q3_v3.py
import pickle, random
from copy import deepcopy
from core.data_loader import load_problem
from dynamic.events import Event
from dynamic.scheduler import _apply_new_order, fast_repair, _collect_pending_insertions
from alns.main import _demand_covered
from core.solution import evaluate_solution

prob = load_problem()
with open("result_q1.pkl", "rb") as f:
    q1 = pickle.load(f)

rng = random.Random(42)
GHOST_CIDS = [1, 14, 15, 17, 18, 20, 21, 22, 23, 96]
chosen = rng.sample(GHOST_CIDS, 8)

prob2 = deepcopy(prob)
sol2 = deepcopy(q1["best"])

for cid in chosen:
    dk = rng.uniform(200, 1200)
    dm = dk / 100
    ts = rng.uniform(13.0, 16.0)
    te = ts + rng.uniform(1.0, 2.5)
    ev = Event(
        time=12.0,
        etype="new_order",
        cid=cid,
        payload=dict(demand_kg=dk, demand_m3=dm, tw_start=ts, tw_end=te),
    )
    _apply_new_order(prob2, sol2, ev)

routes_before = len(sol2.routes)
sol2, inserted = fast_repair(prob2, sol2, rng_seed=19)
routes_after = len(sol2.routes)

print(
    f"路径数: {routes_before} → {routes_after} (+{routes_after - routes_before}条新路径)"
)
print(f"_demand_covered: {_demand_covered(prob2, sol2)}")

for cid in chosen:
    demand = prob2.customers[cid].demand_kg
    delivered = sum(r.delivered_kg.get(cid, 0) for r in sol2.routes)
    routes_for_cid = [i for i, r in enumerate(sol2.routes) if cid in r.nodes]
    print(
        f"  c{cid}: demand={demand:.1f}, delivered={delivered:.1f}, "
        f"routes={routes_for_cid}"
    )

cost, details = evaluate_solution(prob2, sol2)
infeas = sum(1 for d in details if not d.feasible)
print(f"\n成本={cost:.0f}, 不可行路径={infeas}")
