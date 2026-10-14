"""Build maps/classic/map.svg (one <path> per Risk territory) from the
country-level world.svg (Simplemaps, MIT licence).

Usage: python3 tools/build_classic_svg.py [--preview]
Pure stdlib. Territories are unions of whole countries, optionally clipped
to a pixel rectangle (x0, x1, y0, y1) to split countries like Canada or Russia.
"""
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from svgworld import load_countries, view  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MAPDIR = ROOT / "maps" / "classic"
INF = 1e9
ALL = (-INF, INF, -INF, INF)

# ---- the Risk geography -------------------------------------------------
# territory id -> list of "Country" or ("Country", (x0, x1, y0, y1))
T = {
    # North America
    "alaska": [("United States", (-INF, 372, -INF, 186))],
    "northwest_territory": [("Canada", (-INF, 526, -INF, 125)), ("Canada", (526, 620, -INF, 125)),
                            ("Canada", (620, INF, -INF, 107))],
    "greenland": ["Greenland"],
    "alberta": [("Canada", (-INF, 526, 125, INF))],
    "ontario": [("Canada", (526, 620, 125, INF))],
    "quebec": [("Canada", (620, INF, 107, INF))],
    "western_united_states": [("United States", (-INF, 526, 186, 340))],
    "eastern_united_states": [("United States", (526, INF, 186, 340))],
    "central_america": ["Mexico", "Guatemala", "El Salvador", "Belize", "Honduras", "Nicaragua",
                        "Costa Rica", "Panama", "Cuba", "Jamaica", "Haiti", "Dominican Republic",
                        "Bahamas", "Puerto Rico", "Cayman Islands"],
    # South America
    "venezuela": ["Venezuela", "Colombia", "Guyana", "Suriname", "French Guiana"],
    "peru": ["Peru", "Ecuador", "Bolivia"],
    "brazil": ["Brazil"],
    "argentina": ["Argentina", "Chile", "Paraguay", "Uruguay", "Falkland Islands"],
    # Europe
    "iceland": ["Iceland", "Faeroe Islands"],
    "great_britain": ["United Kingdom", "Ireland"],
    "scandinavia": ["Norway", "Sweden", "Finland", "Denmark"],
    "ukraine": ["Ukraine", "Belarus", "Moldova", "Romania", "Lithuania", "Latvia", "Estonia",
                ("Russian Federation", (-INF, 1263, -INF, INF))],
    "northern_europe": ["Germany", "Poland", "Netherlands", "Belgium", "Luxembourg",
                        "Czech Republic", "Slovakia", "Austria", "Switzerland"],
    "southern_europe": ["Italy", "Greece", "Albania", "Macedonia", "Bulgaria", "Serbia", "Croatia",
                        "Bosnia and Herzegovina", "Montenegro", "Kosovo", "Slovenia", "Hungary",
                        "Malta", "Cyprus"],
    "western_europe": ["France", "Spain", "Portugal"],
    # Africa
    "north_africa": ["Morocco", "Algeria", "Tunisia", "Western Sahara", "Mauritania", "Mali", "Niger",
                     "Nigeria", "Senegal", "The Gambia", "Guinea-Bissau", "Guinea", "Sierra Leone",
                     "Liberia", "Côte d'Ivoire", "Burkina Faso", "Ghana", "Togo", "Benin", "Chad",
                     "Canary Islands (Spain)", "Cape Verde"],
    "egypt": ["Egypt", "Libya"],
    "east_africa": ["Ethiopia", "Eritrea", "Djibouti", "Somalia", "Kenya", "Uganda", "Tanzania",
                    "Rwanda", "Burundi", "South Sudan", "Sudan"],
    "congo": ["Democratic Republic of the Congo", "Republic of Congo", "Gabon",
              "Central African Republic", "Cameroon", "Equatorial Guinea", "Angola",
              "São Tomé and Principe"],
    "south_africa": ["South Africa", "Namibia", "Botswana", "Zimbabwe", "Zambia", "Mozambique",
                     "Malawi", "Lesotho", "Swaziland"],
    "madagascar": ["Madagascar", "Comoros", "Mayotte", "Reunion", "Mauritius"],
    # Asia
    "ural": [("Russian Federation", (1263, 1425, -INF, INF))],
    "siberia": [("Russian Federation", (1425, 1520, -INF, INF))],
    "yakutsk": [("Russian Federation", (1520, 1690, -INF, 150))],
    "irkutsk": [("Russian Federation", (1520, 1690, 150, INF))],
    "kamchatka": [("Russian Federation", (1690, INF, -INF, INF))],
    "mongolia": ["Mongolia"],
    "japan": ["Japan"],
    "afghanistan": ["Afghanistan", "Kazakhstan", "Uzbekistan", "Turkmenistan", "Tajikistan",
                    "Kyrgyzstan"],
    "china": ["China", "Taiwan", "Dem. Rep. Korea", "Republic of Korea"],
    "middle_east": ["Turkey", "Syria", "Lebanon", "Israel", "Palestine", "Jordan", "Iraq", "Iran",
                    "Saudi Arabia", "Kuwait", "Bahrain", "Qatar", "United Arab Emirates", "Oman",
                    "Yemen", "Georgia", "Armenia", "Azerbaijan"],
    "india": ["India", "Pakistan", "Nepal", "Bhutan", "Bangladesh", "Sri Lanka"],
    "siam": ["Thailand", "Myanmar", "Lao PDR", "Cambodia", "Vietnam",
             ("Malaysia", (-INF, 1600, -INF, INF))],
    # Oceania
    "indonesia": [("Indonesia", (-INF, 1735, -INF, INF)), ("Malaysia", (1600, INF, -INF, INF)),
                  "Brunei Darussalam", "Philippines", "Timor-Leste"],
    "new_guinea": [("Indonesia", (1735, INF, -INF, INF)), "Papua New Guinea", "Solomon Islands"],
    "western_australia": [("Australia", (-INF, 1718, -INF, INF))],
    "eastern_australia": [("Australia", (1718, INF, -INF, INF)), "New Zealand"],
}


# ---- geometry -------------------------------------------------------------
def clip_axis(poly, keep, intersect):
    out = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        kc, kp = keep(cur), keep(prev)
        if kc:
            if not kp:
                out.append(intersect(prev, cur))
            out.append(cur)
        elif kp:
            out.append(intersect(prev, cur))
    return out


def clip_rect(poly, rect):
    x0, x1, y0, y1 = rect
    if rect == ALL:
        return poly
    for keep, inter in (
        (lambda p: p[0] >= x0, lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0]))),
        (lambda p: p[0] <= x1, lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0]))),
        (lambda p: p[1] >= y0, lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0)),
        (lambda p: p[1] <= y1, lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1)),
    ):
        if not poly:
            return []
        poly = clip_axis(poly, keep, inter)
    return poly


def area(poly):
    return sum(poly[i - 1][0] * poly[i][1] - poly[i][0] * poly[i - 1][1] for i in range(len(poly))) / 2


def centroid(poly):
    a = area(poly)
    if abs(a) < 1e-9:
        return poly[0]
    cx = cy = 0.0
    for i in range(len(poly)):
        p, q = poly[i - 1], poly[i]
        f = p[0] * q[1] - q[0] * p[1]
        cx += (p[0] + q[0]) * f
        cy += (p[1] + q[1]) * f
    return cx / (6 * a), cy / (6 * a)


def min_distance(a_polys, b_polys):
    best = INF
    for a in a_polys:
        for b in b_polys:
            for ax, ay in a[::2]:
                for bx, by in b[::2]:
                    d = (ax - bx) ** 2 + (ay - by) ** 2
                    if d < best:
                        best = d
    return math.sqrt(best)


# ---- build ----------------------------------------------------------------
def build():
    countries = load_countries(MAPDIR / "world.svg")
    shapes = {}
    for tid, parts in T.items():
        polys = []
        for part in parts:
            name, rect = (part, ALL) if isinstance(part, str) else part
            if name not in countries:
                sys.exit(f"{tid}: unknown country {name!r}")
            for poly in countries[name]:
                clipped = clip_rect(poly, rect)
                if len(clipped) >= 3 and abs(area(clipped)) >= 3:
                    polys.append(clipped)
        if not polys:
            sys.exit(f"{tid}: empty shape")
        shapes[tid] = polys
    return shapes


def main():
    gm = json.loads((MAPDIR / "map.json").read_text(encoding="utf-8"))
    ids = list(gm["territories"])
    missing = set(ids) - set(T)
    extra = set(T) - set(ids)
    if missing or extra:
        sys.exit(f"mapping mismatch: missing={sorted(missing)} extra={sorted(extra)}")
    shapes = build()

    # sanity: declared neighbours should touch (or be a known sea/wrap link)
    pairs = sorted({tuple(sorted((a, b))) for a, t in gm["territories"].items() for b in t["neighbors"]})
    far, touching_undeclared = [], []
    for a, b in pairs:
        d = min_distance(shapes[a], shapes[b])
        if d > 8:
            far.append((a, b, round(d)))
    declared = set(pairs)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if tuple(sorted((a, b))) not in declared and min_distance(shapes[a], shapes[b]) < 1.5:
                touching_undeclared.append((a, b))
    print("declared neighbours drawn far apart (sea/wrap links):", far)
    print("touching on map but NOT neighbours in map.json:", touching_undeclared)

    if "--preview" in sys.argv:
        sym = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        reg = {sym[i]: shapes[t] for i, t in enumerate(ids)}
        print(view(reg, 0, 2000, 0, 800, cw=10, ch=16))
        print({sym[i]: t for i, t in enumerate(ids)})
        return

    write_svg(gm, ids, shapes, far)


def fmt(poly):
    return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in poly) + "Z"


def write_svg(gm, ids, shapes, far):
    xs = [x for p in shapes.values() for poly in p for x, _ in poly]
    ys = [y for p in shapes.values() for poly in p for _, y in poly]
    pad = 10
    x0, y0 = math.floor(min(xs)) - pad, math.floor(min(ys)) - pad
    w, h = math.ceil(max(xs)) + pad - x0, math.ceil(max(ys)) + pad - y0
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!-- Generated by tools/build_classic_svg.py from Simplemaps world.svg",
        "     (MIT License, (c) 2020 Pareto Softare, LLC DBA Simplemaps.com). Do not edit by hand. -->",
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {w} {h}">',
        '<g id="territories">',
    ]
    for tid in ids:
        polys = shapes[tid]
        big = max(polys, key=lambda p: abs(area(p)))
        cx, cy = centroid(big)
        d = "".join(fmt(p) for p in polys)
        out.append(f'<path id="{tid}" data-name="{gm["territories"][tid]["name"]}" '
                   f'data-continent="{gm["territories"][tid]["continent"]}" '
                   f'data-cx="{cx:.1f}" data-cy="{cy:.1f}" d="{d}"/>')
    out.append("</g>")
    # sea / wrap-around connections: neighbours that do not touch on the map
    out.append('<g id="links">')
    cent = {t: (float(re.search(r'id="%s"[^>]*data-cx="([^"]+)" data-cy="([^"]+)"' % t, "\n".join(out)).group(1)),
                float(re.search(r'id="%s"[^>]*data-cx="([^"]+)" data-cy="([^"]+)"' % t, "\n".join(out)).group(2)))
            for t in ids}
    for a, b, _ in far:
        (ax, ay), (bx, by) = cent[a], cent[b]
        if abs(ax - bx) > w / 2:  # wraps around the edge of the map: two stubs
            left, right = ((a, ax, ay), (b, bx, by)) if ax < bx else ((b, bx, by), (a, ax, ay))
            (la, lx, ly), (ra, rx, ry) = left, right
            out.append(f'<line data-a="{la}" data-b="{ra}" x1="{lx:.1f}" y1="{ly:.1f}" x2="{x0}" y2="{ly:.1f}"/>')
            out.append(f'<line data-a="{la}" data-b="{ra}" x1="{rx:.1f}" y1="{ry:.1f}" x2="{x0 + w}" y2="{ry:.1f}"/>')
        else:
            out.append(f'<line data-a="{a}" data-b="{b}" x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}"/>')
    out.append("</g>")
    out.append("</svg>")
    (MAPDIR / "map.svg").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {MAPDIR / 'map.svg'} ({(MAPDIR / 'map.svg').stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
