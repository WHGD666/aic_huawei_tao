"""Fake GNN tier partitioner (stand-in until the official baseline arrives)."""
import random

TIMING_WEIGHT = 0.0
AREA_BALANCE_TOL = 0.05


def build_features(cells, nets):
    feats = {}
    for c in cells:
        feats[c] = [cells[c]["area"], len(cells[c]["pins"])]
    return feats


def partition(cells, nets, seed=0):
    rng = random.Random(seed)
    tier = {c: rng.randint(0, 1) for c in cells}
    return repair_balance(tier, cells)


def repair_balance(tier, cells):
    total = sum(v["area"] for v in cells.values())
    top = sum(cells[c]["area"] for c, t in tier.items() if t == 1)
    while abs(top / total - 0.5) > AREA_BALANCE_TOL:
        c = next(iter(cells))
        tier[c] = 1 - tier[c]
        top = sum(cells[c]["area"] for c, t in tier.items() if t == 1)
    return tier
