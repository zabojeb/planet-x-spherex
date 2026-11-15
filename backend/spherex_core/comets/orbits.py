"""Two-body orbits: elements from a state vector, universal-variable propagation, and a digest2-style orbit-class
score for a short arc.

`orbit_classes` asks which orbits could produce an observed position and apparent motion (systematic ranging,
Virtanen et al. 2001, with population priors in the spirit of digest2, Keys et al. 2019). One tracklet fixes the
direction and the angular velocity but not the distance D or the range rate D'. For every (D, D') on a grid
(log-spaced D from 0.03 to 100 au, D' from -60 to +60 km/s) the heliocentric state r = o + D u,
v = v_o + D' u + D du/dt (o, v_o: the observer's heliocentric state over the arc) gives an osculating orbit
(a, e, i). Each cell is weighted by a detectability factor D / r^2 per log D (a shell's volume D^2 dD times the
number of objects bright enough, ~ (r D)^-2 for a size distribution N(<H) ~ 10^(0.4 H)) and, per class, by a
coarse population density in (a, e, i) times the class's relative number; the class probabilities are the
normalised sums. Classes: hyperbolic, long-period comet, Jupiter-family comet, near-Earth object, main belt, outer
asteroid (Hilda/Trojan), Centaur, TNO. The densities are uniform or half-normal per element, so treat the numbers
as a ranking, not calibrated probabilities. The main signal is real: a motion that no main-belt orbit can produce
(a high-latitude field moving across the ecliptic, or too fast for a bound orbit) moves the weight to comets.
"""

from __future__ import annotations

import math

import numpy as np

from .geometry import EQ_TO_ECL, tangent_basis, unit

GM = 0.01720209895 ** 2  # au^3 / day^2
A_JUP = 5.2026
KMS_AU_DAY = 86400.0 / 149_597_870.7
CLASSES = ("hyperbolic", "lpc", "jfc", "neo", "mba", "outer", "centaur", "tno")
COMET_CLASSES = {"hyperbolic": 1.0, "lpc": 1.0, "jfc": 1.0, "centaur": 0.5}  # weight towards comet-likeness
# rough relative numbers of objects per class at SPHEREx depths (V ~ 18-21); main belt = 1
PRIORS = {"mba": 1.0, "neo": 0.02, "outer": 0.05, "jfc": 0.005, "tno": 0.01, "centaur": 0.003, "lpc": 0.003,
          "hyperbolic": 0.0003}
D_GRID = np.geomspace(0.03, 100.0, 120)
DDOT_GRID = np.linspace(-60.0, 60.0, 81)  # km/s


# ---------------------------------------------------------------- elements
def elements(r, v) -> dict[str, np.ndarray]:
    """Osculating heliocentric elements of ecliptic states r (au), v (au/day), [..., 3]: a (au, < 0 hyperbolic),
    e, i (deg), q (au), Tisserand parameter w.r.t. Jupiter."""
    r, v = np.asarray(r, float), np.asarray(v, float)
    rn = np.linalg.norm(r, axis=-1)
    h = np.cross(r, v)
    hn = np.linalg.norm(h, axis=-1)
    ev = np.cross(v, h) / GM - r / rn[..., None]
    e = np.linalg.norm(ev, axis=-1)
    energy = 0.5 * np.sum(v * v, axis=-1) - GM / rn
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.where(np.abs(energy) > 1e-14, -GM / (2 * energy), np.inf)
        q = hn ** 2 / (GM * (1 + e))
        inc = np.degrees(np.arccos(np.clip(h[..., 2] / hn, -1, 1)))
        tj = A_JUP / a + 2 * np.cos(np.radians(inc)) * np.sqrt(np.clip((a / A_JUP) * (1 - e ** 2), 0, None))
    return {"a": a, "e": e, "i": inc, "q": q, "tj": tj}


def _halfnormal(x, sigma):
    return np.sqrt(2 / np.pi) / sigma * np.exp(-0.5 * (x / sigma) ** 2)


def _uniform(x, lo, hi):
    return np.where((x >= lo) & (x < hi), 1.0 / (hi - lo), 0.0)


def _gauss(x, mu, sigma):
    return np.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * math.sqrt(2 * math.pi))


def densities(el: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Coarse population density of every class at the orbits' (a, e, i): per au, per unit e, per degree."""
    a, e, i, q = el["a"], el["e"], el["i"], el["q"]
    iso = np.sin(np.radians(i)) * np.pi / 360.0  # isotropic inclinations, per degree
    bound = e < 1.0
    with np.errstate(all="ignore"):
        v_inf = np.where(a < 0, np.sqrt(np.clip(-GM / a, 0, None)), 0.0) / KMS_AU_DAY
    with np.errstate(all="ignore"):
        d = {
            "mba": _uniform(a, 2.1, 3.3) * _halfnormal(e, 0.1) * (e < 0.4) * _halfnormal(i, 8.0),
            "outer": _uniform(a, 3.7, 5.4) * _halfnormal(e, 0.12) * (e < 0.4) * _halfnormal(i, 12.0),
            # Earth-approaching (q < 1.3, aphelion > 0.983 au), concentrated at a ~ 1.8 au, e ~ 0.45 (Granvik et al.
            # 2018 in broad strokes): near-circular Earth-like orbits are rare
            "neo": _gauss(a, 1.8, 0.6) * (q < 1.3) * (a * (1 + e) > 0.983) * _gauss(e, 0.45, 0.17)
                   * _halfnormal(i, 15.0),
            "jfc": _uniform(a, 2.5, 8.0) * _uniform(e, 0.2, 0.9) * _halfnormal(i, 12.0),
            "centaur": _uniform(a, 5.5, 30.0) * (q > 5.0) * _uniform(e, 0.0, 0.7) * _halfnormal(i, 15.0),
            "tno": _uniform(a, 30.0, 60.0) * _halfnormal(e, 0.15) * _halfnormal(i, 12.0),
            # long-period: a log-uniform over 30-1e5 au, e of 0.9-1, isotropic inclinations
            "lpc": np.where(bound & (a >= 30) & (a <= 1e5), 1.0 / (a * np.log(1e5 / 30.0)), 0.0)
                   * _uniform(e, 0.9, 1.0) * iso,
            # interstellar: excess speed v_inf (km/s) ~ gamma(3, 15 km/s), none above 120 km/s ('Oumuamua 26,
            # Borisov 32, 3I/ATLAS 58); per km/s, not per unit e
            "hyperbolic": np.where(~bound & (v_inf <= 120.0), v_inf ** 2 * np.exp(-v_inf / 15.0) / (2 * 15.0 ** 3), 0.0)
                          * iso,
        }
    return {k: np.nan_to_num(v) for k, v in d.items()}


def classify(el: dict[str, np.ndarray]) -> np.ndarray:
    """The most probable class (index into CLASSES) of every orbit, -1 where no class has density."""
    d = densities(el)
    stack = np.stack([PRIORS[c] * d[c] for c in CLASSES])
    return np.where(stack.max(axis=0) > 0, np.argmax(stack, axis=0), -1)


def orbit_classes(ra: float, dec: float, rate_arcsec_h: float, pa_deg: float, o_au, v_au_day,
                  distance_au: tuple[float, float] | None = None, range_rate_kms: tuple[float, float] | None = None
                  ) -> dict:
    """Probabilities of the orbit classes for a position, an apparent motion (arcsec/h, PA east of north) and the
    observer's heliocentric ICRS state (au, au/day, averaged over the arc). Adds the comet-like total and, per
    class, the weighted median distance. distance_au / range_rate_kms: (low, high) limits when known (an ephemeris,
    or a parallax-constrained slow link), which restrict the grid."""
    u = unit(ra, dec)
    e_, n_ = tangent_basis(ra, dec)
    mu = rate_arcsec_h * 24.0 / 206264.806  # rad / day
    dudt = mu * (math.sin(math.radians(pa_deg)) * e_ + math.cos(math.radians(pa_deg)) * n_)
    dg = D_GRID if distance_au is None else np.geomspace(max(distance_au[0], 1e-3), max(distance_au[1], 1e-3), 25)
    rg = DDOT_GRID if range_rate_kms is None else np.linspace(range_rate_kms[0], range_rate_kms[1], 21)
    D = dg[:, None, None]
    Dd = (rg * KMS_AU_DAY)[None, :, None]
    r = np.asarray(o_au, float)[None, None, :] + D * u[None, None, :]
    v = np.asarray(v_au_day, float)[None, None, :] + Dd * u[None, None, :] + D * dudt[None, None, :]
    r = np.broadcast_to(r, (len(dg), len(rg), 3)) @ EQ_TO_ECL.T
    v = v @ EQ_TO_ECL.T
    el = elements(r, v)
    rn = np.linalg.norm(r, axis=-1)
    dd = np.broadcast_to(dg[:, None], rn.shape)
    # per (log D, D') cell: the phase-space density F(r, v) = g(a, e, i) |d(a,e,i)/d(L,G,H)| (Delaunay actions,
    # whose map from (r, v) has unit Jacobian) ~ g / (sqrt|a| e sin i), times D^2 from the 2-D velocity Jacobian
    # (v_t = D mu), D^3 / r^2 for the shell volume per log D and the number of objects bright enough (~ (r D)^-2)
    jac = 1.0 / (np.sqrt(np.abs(el["a"])) * np.maximum(el["e"], 0.01) * np.maximum(np.sin(np.radians(el["i"])), 0.01))
    detect = dd ** 3 / np.maximum(rn, 0.3) ** 2 * np.nan_to_num(jac)
    dens = densities(el)
    weight = {c: PRIORS[c] * dens[c] * detect for c in CLASSES}
    sums = {c: float(w.sum()) for c, w in weight.items()}
    total = sum(sums.values())
    probs = {c: (sums[c] / total if total > 0 else 0.0) for c in CLASSES}
    dist = {}
    order = np.argsort(dd.ravel())
    for c, w in weight.items():
        if sums[c] > 0:
            cw = np.cumsum(w.ravel()[order])
            dist[c] = float(dd.ravel()[order][min(np.searchsorted(cw, 0.5 * cw[-1]), cw.size - 1)])
    comet = sum(probs[c] * f for c, f in COMET_CLASSES.items())
    best = max(probs, key=probs.get) if total > 0 else None
    return {"probabilities": {c: round(p, 5) for c, p in probs.items()}, "comet_like": round(float(comet), 5),
            "most_likely": best, "median_distance_au": {c: round(x, 3) for c, x in dist.items()},
            "bound_possible": bool((el["e"] < 1).any()), "feasible": total > 0,
            "constrained": distance_au is not None}


# ---------------------------------------------------------------- propagation (universal variables)
def _stumpff(z: float) -> tuple[float, float]:
    if z > 1e-8:
        s = math.sqrt(z)
        return (1 - math.cos(s)) / z, (s - math.sin(s)) / s ** 3
    if z < -1e-8:
        s = math.sqrt(-z)
        return (math.cosh(s) - 1) / -z, (math.sinh(s) - s) / s ** 3
    return 0.5 - z / 24.0, 1.0 / 6.0 - z / 120.0


def perifocal_rotation(i_deg: float, node_deg: float, peri_deg: float) -> np.ndarray:
    """Perifocal -> ecliptic rotation R_z(node) R_x(i) R_z(argument of perihelion)."""
    i, O, w = map(math.radians, (i_deg, node_deg, peri_deg))
    cO, sO, ci, si, cw, sw = math.cos(O), math.sin(O), math.cos(i), math.sin(i), math.cos(w), math.sin(w)
    return np.array([[cO * cw - sO * sw * ci, -cO * sw - sO * cw * ci, sO * si],
                     [sO * cw + cO * sw * ci, -sO * sw + cO * cw * ci, -cO * si],
                     [sw * si, cw * si, ci]])


def propagate(q: float, e: float, i_deg: float, node_deg: float, peri_deg: float, tp_jd: float, jd: float
              ) -> tuple[np.ndarray, np.ndarray]:
    """Heliocentric ecliptic J2000 position (au) and velocity (au/day) at jd of a two-body orbit given by its
    perihelion distance, eccentricity (any conic), angles and time of perihelion (JD TDB)."""
    dt = jd - tp_jd
    alpha = (1.0 - e) / q  # 1/a
    vp = math.sqrt(GM * (1.0 + e) / q)
    sq = math.sqrt(GM)
    k = 1.0 - alpha * q

    def F(x):
        c, s = _stumpff(alpha * x * x)
        return k * x ** 3 * s + q * x - sq * dt, k * x * x * c + q

    if dt == 0:
        x = 0.0
    else:
        lo, hi = (0.0, sq * abs(dt) / q) if dt > 0 else (-sq * abs(dt) / q, 0.0)
        # F is monotonic (F' = r > 0): widen until the root is bracketed, then safeguarded Newton
        for _ in range(200):
            if dt > 0 and F(hi)[0] < 0:
                lo, hi = hi, hi * 2
            elif dt < 0 and F(lo)[0] > 0:
                hi, lo = lo, lo * 2
            else:
                break
        x = 0.5 * (lo + hi)
        for _ in range(100):
            f, fp = F(x)
            if f > 0:
                hi = x
            else:
                lo = x
            nx = x - f / fp
            if not lo < nx < hi:
                nx = 0.5 * (lo + hi)
            if abs(nx - x) < 1e-12 * max(1.0, abs(x)):
                x = nx
                break
            x = nx
    c, s = _stumpff(alpha * x * x)
    r0 = np.array([q, 0.0, 0.0])
    v0 = np.array([0.0, vp, 0.0])
    f = 1 - x * x * c / q
    g = dt - x ** 3 * s / sq
    r = f * r0 + g * v0
    rn = float(np.linalg.norm(r))
    fdot = sq / (rn * q) * (alpha * x ** 3 * s - x)
    gdot = 1 - x * x * c / rn
    v = fdot * r0 + gdot * v0
    rot = perifocal_rotation(i_deg, node_deg, peri_deg)
    return rot @ r, rot @ v


def ecl_to_eq(v) -> np.ndarray:
    return np.asarray(v, float) @ EQ_TO_ECL
