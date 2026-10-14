"""Parse the Simplemaps-style world SVG into {country name: [polygon, ...]}.

Pure stdlib. Handles the subset of path syntax used there: M/m, L, l, Z/z.
Polygon = list of (x, y).
"""
import re
from collections import defaultdict

NUM = re.compile(r'-?\d*\.?\d+(?:e-?\d+)?')


def parse_path(d):
    polys, cur, x, y = [], [], 0.0, 0.0
    for cmd, args in re.findall(r'([MmLlZz])([^MmLlZz]*)', d):
        if cmd in 'Zz':
            if cur:
                polys.append(cur)
                cur = []
            continue
        nums = [float(n) for n in NUM.findall(args)]
        for i, (a, b) in enumerate(zip(nums[::2], nums[1::2])):
            if cmd in 'ML':
                x, y = a, b
            else:
                x, y = x + a, y + b
            if cmd in 'Mm' and i == 0 and cur:
                polys.append(cur)
                cur = []
            cur.append((x, y))
    if cur:
        polys.append(cur)
    return polys


def load_countries(svg_path):
    text = open(svg_path, encoding='utf8').read()
    out = defaultdict(list)
    for m in re.finditer(r'<path\b([^>]*)>', text):
        attrs = m.group(1)
        d = re.search(r' d="([^"]*)"', attrs).group(1)
        name = re.search(r' name="([^"]*)"', attrs) or re.search(r'class="([^"]*)"', attrs)
        out[name.group(1)] += parse_path(d)
    return dict(out)


def inside(pt, poly):
    x, y = pt
    c = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def ascii_render(regions, x0, x1, y0, y1, cw=8, ch=14, grid=True):
    """regions: {label_char: [polygons]}; prints a char grid with pixel rulers."""
    lines = []
    for y in range(int(y0), int(y1), ch):
        row = []
        for x in range(int(x0), int(x1), cw):
            c = '.'
            for label, polys in regions.items():
                if any(inside((x + cw / 2, y + ch / 2), p) for p in polys):
                    c = label
                    break
            row.append(c)
        lines.append(f"{y:4d} " + ''.join(row))
    ruler = '     ' + ''.join(str((int(x0) + i * cw) // 100 % 10) if (int(x0) + i * cw) % 100 < cw else ' '
                              for i in range((int(x1) - int(x0)) // cw))
    return '\n'.join([ruler] + lines)


def view(regions, x0, x1, y0, y1, cw=4, ch=8):
    """ASCII render with a ruler: header lines show x in tens of pixels, rows show y."""
    xs = list(range(int(x0), int(x1), cw))
    h1 = ''.join(str((x // 100) % 10) if x % 100 < cw else ' ' for x in xs)
    h2 = ''.join(str((x // 10) % 10) if x % 20 < cw else ' ' for x in xs)
    out = ['     ' + h1, '     ' + h2]
    for y in range(int(y0), int(y1), ch):
        row = ''
        for x in xs:
            c = '.'
            for label, polys in regions.items():
                if any(inside((x + cw / 2, y + ch / 2), p) for p in polys):
                    c = label
                    break
            row += c
        out.append(f"{y:4d} {row}")
    return '\n'.join(out)
