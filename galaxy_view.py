"""Star-field geometry adapted from Maanav's original ui_galaxy.py theme."""
import math
import random


def generate_spiral_stars(core_count=700, arm_count=1400, seed=37, arms=4):
    """Build a deterministic dense center and four-arm galaxy of tiny stars."""
    rng = random.Random(seed)
    stars = []
    for _ in range(core_count):
        stars.append({
            "angle": rng.uniform(0, math.tau),
            "radius": rng.uniform(5, 110),
            "size": rng.choice((1, 1, 1, 1, 2)),
            "speed": rng.uniform(0.0003, 0.001),
            "brightness": rng.randint(105, 165),
            "phase": rng.uniform(0, math.tau),
        })
    for _ in range(arm_count):
        radius = rng.uniform(70, 650)
        arm = rng.randrange(arms)
        angle = arm * (math.tau / arms) + radius * 0.012 + rng.gauss(0, 0.16)
        stars.append({
            "angle": angle,
            "radius": radius,
            "size": rng.choice((1, 1, 1, 1, 2, 2)),
            "speed": rng.uniform(0.0004, 0.0015),
            "brightness": rng.randint(75, 160),
            "phase": rng.uniform(0, math.tau),
        })
    return stars
