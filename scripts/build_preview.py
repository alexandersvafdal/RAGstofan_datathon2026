"""The link preview image for the demo site (frontend/preview.png, 1200x630), drawn from the demo's saved answers:
the population of each municipality on the official IS 50V map, and Iceland's population since 1950.

  python scripts/build_preview.py      writes _preview/preview.html; render it with headless Chrome:
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --hide-scrollbars \
     --window-size=1200,630 --force-device-scale-factor=2 --screenshot=frontend/preview.png _preview/preview.html
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_preview"
answers = json.loads((ROOT / "frontend/demo/answers.json").read_text())
geo = json.loads((ROOT / "frontend/municipalities.geo.json").read_text())


def answer(key):
    return answers[key]["lines"][-1]["answer"]


pop = {d["code"]: d["y"] for d in answer("show a map of the population by municipality||")["parts"][0]["chart"]["data"]}
series = answer("how has iceland's population grown since 1950||")["parts"][0]["chart"]["data"]

# ---- the map: equirectangular, longitude shrunk by cos(65°), fitted to the box ----
W, H, PAD = 560, 360, 6
k = math.cos(math.radians(65))
pts = [(x * k, -y) for f in geo["features"] for poly in f["geometry"]["coordinates"] for ring in poly for x, y in ring]
x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
s = min((W - 2 * PAD) / (x1 - x0), (H - 2 * PAD) / (y1 - y0))
ox, oy = (W - (x1 - x0) * s) / 2, (H - (y1 - y0) * s) / 2


def mix(a, b, t):
    a, b = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(u + (v - u) * t):02x}" for u, v in zip(a, b))


lo, hi = math.log(min(pop.values())), math.log(max(pop.values()))
paths = []
for f in geo["features"]:
    v = pop.get(f["id"])
    t = (math.log(v) - lo) / (hi - lo) if v else None   # light sand, gold, then navy for the most people
    fill = "#ece9e2" if t is None else mix("#efe6d2", "#b89660", t / .5) if t < .5 else mix("#b89660", "#0b1f3a", (t - .5) / .5)
    d = "".join("M" + "L".join(f"{ox + (x * k - x0) * s:.1f},{oy + (-y - y0) * s:.1f}" for x, y in ring) + "Z"
                for poly in f["geometry"]["coordinates"] for ring in poly)
    paths.append(f'<path d="{d}" fill="{fill}" stroke="#f8f6f1" stroke-width="0.7" stroke-linejoin="round"/>')
map_svg = f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}">{"".join(paths)}</svg>'

# ---- the line: population 1950-2026 ----
LW, LH = 254, 80
ys = [d["y"] for d in series]
pts = [(6 + i / (len(ys) - 1) * (LW - 14), LH - 6 - (y - min(ys)) / (max(ys) - min(ys)) * (LH - 14)) for i, y in enumerate(ys)]
line = "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
area = line + f"L{pts[-1][0]:.1f},{LH}L{pts[0][0]:.1f},{LH}Z"
line_svg = (f'<svg viewBox="0 0 {LW} {LH}" width="{LW}" height="{LH}"><path d="{area}" fill="rgba(11,31,58,.07)"/>'
            f'<path d="{line}" fill="none" stroke="#0b1f3a" stroke-width="2.4" stroke-linejoin="round"/>'
            f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4.5" fill="#a88a57" stroke="#fdfcfa" stroke-width="2"/></svg>')
fmt = lambda v: f"{round(v):,}"

html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 1200px; height: 630px; overflow: hidden; }}
body {{ background: #f5f3ee radial-gradient(900px 420px at 20% -10%, #ebe6db 0%, transparent 70%);
       font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Helvetica Neue", Arial, sans-serif;
       color: #12161f; -webkit-font-smoothing: antialiased; position: relative; }}
.left {{ position: absolute; left: 64px; top: 66px; width: 470px; }}
.eyebrow {{ font-size: 17px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: #a88a57; }}
h1 {{ font-size: 88px; letter-spacing: -.035em; line-height: .95; margin-top: 22px; color: #0b1f3a; }}
h2 {{ font-size: 40px; letter-spacing: -.02em; line-height: 1.05; margin-top: 14px; font-weight: 700; color: #12161f; }}
p {{ font-size: 23px; line-height: 1.38; color: #474d57; margin-top: 24px; }}
.chips {{ position: absolute; left: 64px; bottom: 58px; display: flex; gap: 10px; }}
.chip {{ font-size: 17px; font-weight: 600; color: #0b1f3a; background: #fdfcfa; border: 1px solid rgba(18,22,31,.09);
         padding: 9px 15px; border-radius: 999px; }}
.chip.gold {{ background: #0b1f3a; color: #f5f3ee; border-color: #0b1f3a; }}
.map {{ position: absolute; right: 36px; top: 40px; width: 600px; height: 470px; background: #fdfcfa; border-radius: 28px;
        box-shadow: 0 1px 2px rgba(18,22,31,.04), 0 12px 36px rgba(18,22,31,.08); padding: 22px 20px 0; }}
.map .cap {{ font-size: 15px; color: #6b6f77; font-weight: 600; padding: 0 6px; }}
.map .cap b {{ color: #12161f; }}
.map svg {{ display: block; margin: 8px 0 0 -10px; }}
.trend {{ position: absolute; right: 52px; bottom: 30px; width: 290px; background: #fdfcfa; border-radius: 22px; padding: 16px 18px 10px;
          box-shadow: 0 2px 4px rgba(18,22,31,.05), 0 16px 40px rgba(18,22,31,.12); }}
.trend .k {{ font-size: 13.5px; color: #6b6f77; font-weight: 600; }}
.trend .v {{ font-size: 26px; font-weight: 750; letter-spacing: -.02em; margin-top: 2px; }}
.trend .v small {{ font-size: 15px; color: #6b6f77; font-weight: 600; }}
.trend svg {{ display: block; margin-top: 6px; }}
</style></head><body>
<div class="left">
  <div class="eyebrow">Statistics Iceland · Gagnaþon 2026</div>
  <h1>RAGstofan</h1>
  <h2>Iceland, in numbers.</h2>
  <p>Ask in Icelandic or English. Get the official figure back with its source, a chart or a map.</p>
</div>
<div class="chips"><span class="chip gold">2nd place</span><span class="chip">1,857 tables</span><span class="chip">~3 s per answer</span></div>
<div class="map"><div class="cap"><b>Population by municipality</b> · 2026 · Hagstofa Íslands</div>{map_svg}</div>
<div class="trend"><div class="k">Iceland's population since 1950</div>
  <div class="v">{fmt(ys[-1])} <small>from {fmt(ys[0])}</small></div>{line_svg}</div>
</body></html>"""
OUT.mkdir(exist_ok=True)
(OUT / "preview.html").write_text(html)
print(f"wrote {(OUT / 'preview.html').relative_to(ROOT)}")
