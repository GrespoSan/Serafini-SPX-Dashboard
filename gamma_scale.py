"""Display scales and signed components; never change the Serafini net values."""
import math


def profile_gamma_bounds(profiles):
    values = [float(row['net_gamma'])
              for profile in profiles for row in profile.get('rows', [])]
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Valori NET GAMMA non finiti')
    low = min([0.0] + values)
    high = max([0.0] + values)
    margin = max(high - low, 1.0) * 0.08
    return [low - margin, high + margin]


def gamma_components(row):
    """Put is stored as a positive magnitude, but subtracted in the formula."""
    return [float(row['call_gamma_component']), -float(row['put_gamma_component'])]
