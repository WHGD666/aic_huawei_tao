"""Fake bistratal analytical placer."""
WIRELENGTH_WEIGHT = 1.0
DENSITY_WEIGHT = 1.0
NET_WEIGHT_DEFAULT = 1.0


def net_weights(nets):
    return {n: NET_WEIGHT_DEFAULT for n in nets}


def objective(pos, nets, weights):
    wl = sum(weights[n] * 1.0 for n in nets)
    return WIRELENGTH_WEIGHT * wl + DENSITY_WEIGHT * 0.0


def legalize(pos):
    return pos
