"""Iceland's municipalities as a small map file for the apps, from the official boundaries.

Source: IS 50V municipality boundaries (layer IS_50V:mork_sveitarf_flakar) from Landmælingar Íslands' map server,
gis.lmi.is, owned by Náttúrufræðistofnun and published under CC BY 4.0, which asks for the author, the dataset and the
licence to be credited (the apps show it under every map). Download it once:

  curl -o data/geo/municipalities_is50v_raw.geojson "https://gis.lmi.is/geoserver/wfs?service=WFS&version=2.0.0\
&request=GetFeature&typeNames=IS_50V:mork_sveitarf_flakar&outputFormat=application/json&srsName=EPSG:4326"

then run  python scripts/build_map.py . The 7 MB of surveyed outlines become a few hundred KB: each ring simplified
(Douglas-Peucker, about 300 m), coordinates rounded to about 10 m, slivers and islets under 0.5 km² left out, and the
pieces of one municipality joined into one shape keyed by its official 4-digit number ("0000" is Reykjavík).
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/geo/municipalities_is50v_raw.geojson"
OUT = ROOT / "frontend/municipalities.geo.json"
TOLERANCE = 0.003      # degrees of latitude, about 300 m
DIGITS = 4             # about 10 m
MIN_AREA_KM2 = 0.5
ATTRIBUTION = "IS 50V Sveitarfélög, Náttúrufræðistofnun / Landmælingar Íslands, CC BY 4.0"


def _dist(p, a, b):
    """Distance from p to the segment a-b, longitude scaled by the latitude's cosine so both axes are in like units."""
    k = math.cos(math.radians(a[1]))
    px, py, ax, ay, bx, by = p[0] * k, p[1], a[0] * k, a[1], b[0] * k, b[1]
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def simplify(points, tol):
    """Douglas-Peucker, iterative (rings of 100,000 points would overflow the recursion)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        far, at = 0.0, None
        for n in range(i + 1, j):
            d = _dist(points[n], points[i], points[j])
            if d > far:
                far, at = d, n
        if at is not None and far > tol:
            keep[at] = True
            stack += [(i, at), (at, j)]
    return [p for p, k in zip(points, keep) if k]


def area_km2(ring):
    """Shoelace area on a local equirectangular projection; enough to tell an islet from a valley."""
    lat = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111.32 * math.cos(math.radians(lat)), 110.57
    s = sum(a[0] * kx * b[1] * ky - b[0] * kx * a[1] * ky for a, b in zip(ring, ring[1:] + ring[:1]))
    return abs(s) / 2


def main():
    raw = json.loads(RAW.read_text())
    by_number: dict[str, dict] = {}
    for f in raw["features"]:
        props = f["properties"]
        number = f"{int(props['nrsveitarfelags']):04d}"
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        entry = by_number.setdefault(number, {"name": props["sveitarfelag"], "polygons": []})
        for poly in polys:
            outer = simplify([tuple(p[:2]) for p in poly[0]], TOLERANCE)
            if len(outer) < 4 or area_km2(outer) < MIN_AREA_KM2:
                continue
            rings = [outer] + [r for r in (simplify([tuple(p[:2]) for p in h], TOLERANCE) for h in poly[1:])
                               if len(r) >= 4 and area_km2(r) >= MIN_AREA_KM2]
            entry["polygons"].append([[[round(x, DIGITS), round(y, DIGITS)] for x, y in r] for r in rings])
    features = [{"type": "Feature", "id": n, "properties": {"number": n, "name": e["name"]},
                 "geometry": {"type": "MultiPolygon", "coordinates": e["polygons"]}}
                for n, e in sorted(by_number.items()) if e["polygons"]]
    OUT.write_text(json.dumps({"type": "FeatureCollection", "attribution": ATTRIBUTION, "source": raw["features"][0]["properties"].get("gagnasafn", ""),
                               "features": features}, ensure_ascii=False, separators=(",", ":")))
    points = sum(len(r) for f in features for poly in f["geometry"]["coordinates"] for r in poly)
    print(f"{len(features)} municipalities, {points} points, {OUT.stat().st_size // 1024} KB -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
