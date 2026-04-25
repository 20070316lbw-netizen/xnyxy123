import pickle
import random
from copy import deepcopy
from core.data_loader import load_problem
from alns.operators import _compute_available

prob = load_problem()
with open("result_q1.pkl", "rb") as f:
    q1 = pickle.load(f)

sol2 = q1["best"]
print(_compute_available(sol2))
