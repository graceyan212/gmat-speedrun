#!/usr/bin/env python3
"""Calibration analysis for the AI-difficulty model.

Companion to eval_difficulty.py. Where that script asks "does AI difficulty
beat the coarse baseline?", this one asks "when the AI-difficulty Rasch model
says P(correct)=x, is the student actually right about x of the time?".

Method (kept consistent with eval_difficulty.py):
  * Pull the student's real reviews from a collection's revlog
    (ease>0, type!=4; correct = ease>=2).
  * Join each review to its item via the note's `id::<item_id>` tag ->
    AI difficulty (from content/ai_difficulty.json).
  * Fit a 1-parameter Rasch ability theta on the first 70% of reviews (by time).
  * On the held-out last 30%, predict P(correct)=sigmoid(theta - b) where
    b = logit(ai_difficulty/100).
  * Bin the held-out predictions into 10 buckets [0,0.1),...,[0.9,1.0] and
    report per-bin (mean predicted P, observed frequency, n).
  * Report overall Brier score and log loss.
  * Print a reliability table and write a reliability-diagram chart:
      matplotlib -> docs/calibration.png, else self-contained inline-SVG
      docs/calibration.html (no external/CDN deps).

Usage:
  python content/tools/calibration.py /path/to/collection.anki2
  python content/tools/calibration.py /path/to/collection.anki2 --split 0.7
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SIDECAR = REPO / "content" / "ai_difficulty.json"
DOCS = REPO / "docs"
SCALE = 4.0
COARSE = {"easy": 20.0, "medium": 50.0, "hard": 80.0}
N_BINS = 10
EPS = 1e-12  # clamp for log loss


def sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def logit(diff_0_100: float) -> float:
    return (diff_0_100 / 100.0 - 0.5) * SCALE


def estimate_theta(obs: list[tuple[float, int]]) -> float:
    """Newton MLE for Rasch ability from (b_logit, correct) observations."""
    if not obs:
        return 0.0
    theta = 0.0
    for _ in range(50):
        g = 0.0  # gradient of log-likelihood
        h = 0.0  # (negative) second derivative
        for b, y in obs:
            p = sigmoid(theta - b)
            g += y - p
            h += p * (1.0 - p)
        if h < 1e-9:
            break
        step = g / h
        theta += step
        theta = max(-4.0, min(4.0, theta))
        if abs(step) < 1e-6:
            break
    return theta


def parse_tags(tags: str) -> tuple[str | None, float | None]:
    """Return (item_id from id::, coarse difficulty 0-100 from difficulty::)."""
    item_id = None
    coarse = None
    for t in tags.split():
        low = t.lower()
        if low.startswith("id::"):
            item_id = t.split("::", 1)[1]
        elif low.startswith("difficulty::"):
            val = t.split("::", 1)[1].lower()
            if val in COARSE:
                coarse = COARSE[val]
            else:
                try:
                    coarse = max(0.0, min(100.0, float(val)))
                except ValueError:
                    pass
    return item_id, coarse


def load_reviews(col_path: str):
    con = sqlite3.connect(col_path)
    rows = con.execute(
        """
        SELECT r.id, r.ease, n.tags
        FROM revlog r
        JOIN cards c ON r.cid = c.id
        JOIN notes n ON c.nid = n.id
        WHERE r.ease > 0 AND r.type != 4
        ORDER BY r.id
        """
    ).fetchall()
    con.close()
    return rows


def brier_and_logloss(theta: float, held: list[tuple[float, int]]):
    """Overall Brier score and log loss on held-out (b_logit, correct)."""
    if not held:
        return None, None
    bs = 0.0
    ll = 0.0
    for b, y in held:
        p = sigmoid(theta - b)
        bs += (p - y) ** 2
        pc = min(1.0 - EPS, max(EPS, p))
        ll += -(y * math.log(pc) + (1 - y) * math.log(1.0 - pc))
    n = len(held)
    return bs / n, ll / n


def reliability_bins(theta: float, held: list[tuple[float, int]]):
    """Bin held-out predictions into N_BINS equal-width buckets on [0,1].

    Returns list of dicts with lo, hi, n, mean_pred, obs_freq (None-freq if n==0).
    """
    bins = [
        {"lo": i / N_BINS, "hi": (i + 1) / N_BINS, "sum_pred": 0.0,
         "sum_y": 0, "n": 0}
        for i in range(N_BINS)
    ]
    for b, y in held:
        p = sigmoid(theta - b)
        idx = int(p * N_BINS)
        if idx >= N_BINS:  # p == 1.0 lands in the last bucket
            idx = N_BINS - 1
        bins[idx]["sum_pred"] += p
        bins[idx]["sum_y"] += y
        bins[idx]["n"] += 1
    out = []
    for bn in bins:
        n = bn["n"]
        out.append({
            "lo": bn["lo"],
            "hi": bn["hi"],
            "n": n,
            "mean_pred": (bn["sum_pred"] / n) if n else None,
            "obs_freq": (bn["sum_y"] / n) if n else None,
        })
    return out


def print_table(bins, brier, logloss, theta, n_held):
    print("\nReliability table (AI-difficulty Rasch model, held-out reviews)")
    print(f"  fitted theta (train) = {theta:+.3f} | held-out n = {n_held}")
    print()
    print("  bucket        n   mean_pred   observed   gap")
    print("  ----------  ---   ---------   --------   ------")
    for bn in bins:
        label = f"[{bn['lo']:.1f},{bn['hi']:.1f})"
        if bn["n"] == 0:
            print(f"  {label:<10}  {0:>3}          -          -        -")
        else:
            gap = bn["obs_freq"] - bn["mean_pred"]
            print(f"  {label:<10}  {bn['n']:>3}     {bn['mean_pred']:.3f}"
                  f"      {bn['obs_freq']:.3f}   {gap:+.3f}")
    print()
    print(f"  Overall Brier score = {brier:.4f}   (0 = perfect, lower is better)")
    print(f"  Overall log loss    = {logloss:.4f}   (0 = perfect, lower is better)")


def write_matplotlib_chart(bins, brier, logloss, path: Path) -> bool:
    """Try to render a reliability diagram with matplotlib. Return True on success."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # ImportError, or broken numpy/mpl ABI, etc.
        print(f"  (matplotlib unavailable: {type(exc).__name__}: {exc})")
        return False
    try:
        xs = [bn["mean_pred"] for bn in bins if bn["n"] > 0]
        ys = [bn["obs_freq"] for bn in bins if bn["n"] > 0]
        ns = [bn["n"] for bn in bins if bn["n"] > 0]

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot([0, 1], [0, 1], "--", color="#999", label="perfect calibration")
        if xs:
            sizes = [30 + 25 * n for n in ns]
            ax.scatter(xs, ys, s=sizes, color="#2b6cb0", alpha=0.75,
                       zorder=3, label="observed (size ~ n)")
            ax.plot(xs, ys, color="#2b6cb0", alpha=0.5, zorder=2)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xlabel("mean predicted P(correct)")
        ax.set_ylabel("observed frequency correct")
        ax.set_title(f"Reliability diagram (held-out)\n"
                     f"Brier={brier:.4f}  LogLoss={logloss:.4f}")
        ax.legend(loc="upper left")
        ax.set_aspect("equal")
        fig.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=110)
        plt.close(fig)
        return True
    except Exception as exc:
        print(f"  (matplotlib render failed: {type(exc).__name__}: {exc})")
        return False


def write_svg_chart(bins, brier, logloss, theta, n_held, path: Path) -> None:
    """Self-contained inline-SVG reliability diagram. No external/CDN deps."""
    W = H = 460
    M = 60  # margin
    plot = W - 2 * M  # plot area size (square)

    def sx(p: float) -> float:
        return M + p * plot

    def sy(p: float) -> float:  # y grows downward; invert so 1.0 is at top
        return M + (1.0 - p) * plot

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="system-ui,-apple-system,'
        f'Segoe UI,Roboto,sans-serif">'
    )
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>')

    # gridlines + tick labels at 0,0.2,...,1.0
    for i in range(6):
        t = i / 5.0
        x = sx(t)
        y = sy(t)
        parts.append(
            f'<line x1="{x:.1f}" y1="{sy(0):.1f}" x2="{x:.1f}" y2="{sy(1):.1f}" '
            f'stroke="#eee" stroke-width="1"/>'
        )
        parts.append(
            f'<line x1="{sx(0):.1f}" y1="{y:.1f}" x2="{sx(1):.1f}" y2="{y:.1f}" '
            f'stroke="#eee" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{sy(0)+18:.1f}" font-size="11" fill="#666" '
            f'text-anchor="middle">{t:.1f}</text>'
        )
        parts.append(
            f'<text x="{sx(0)-10:.1f}" y="{y+4:.1f}" font-size="11" fill="#666" '
            f'text-anchor="end">{t:.1f}</text>'
        )

    # axes box
    parts.append(
        f'<rect x="{sx(0):.1f}" y="{sy(1):.1f}" width="{plot}" height="{plot}" '
        f'fill="none" stroke="#333" stroke-width="1.5"/>'
    )
    # diagonal = perfect calibration
    parts.append(
        f'<line x1="{sx(0):.1f}" y1="{sy(0):.1f}" x2="{sx(1):.1f}" '
        f'y2="{sy(1):.1f}" stroke="#999" stroke-width="1.5" '
        f'stroke-dasharray="6,4"/>'
    )
    parts.append(
        f'<text x="{sx(0.62):.1f}" y="{sy(0.66):.1f}" font-size="11" '
        f'fill="#999" transform="rotate(-45 {sx(0.62):.1f} {sy(0.66):.1f})">'
        f'perfect calibration</text>'
    )

    # observed points + connecting polyline
    pts = [(bn["mean_pred"], bn["obs_freq"], bn["n"]) for bn in bins if bn["n"]]
    if len(pts) >= 2:
        poly = " ".join(f"{sx(px):.1f},{sy(py):.1f}" for px, py, _ in pts)
        parts.append(
            f'<polyline points="{poly}" fill="none" stroke="#2b6cb0" '
            f'stroke-width="2" opacity="0.55"/>'
        )
    for px, py, n in pts:
        r = 4 + min(14, math.sqrt(n) * 2.2)
        parts.append(
            f'<circle cx="{sx(px):.1f}" cy="{sy(py):.1f}" r="{r:.1f}" '
            f'fill="#2b6cb0" fill-opacity="0.75" stroke="#1a4a80" '
            f'stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{sx(px):.1f}" y="{sy(py)-r-3:.1f}" font-size="9" '
            f'fill="#1a4a80" text-anchor="middle">n={n}</text>'
        )

    # axis titles
    parts.append(
        f'<text x="{sx(0.5):.1f}" y="{H-14:.1f}" font-size="12" fill="#333" '
        f'text-anchor="middle">mean predicted P(correct)</text>'
    )
    parts.append(
        f'<text x="16" y="{sy(0.5):.1f}" font-size="12" fill="#333" '
        f'text-anchor="middle" transform="rotate(-90 16 {sy(0.5):.1f})">'
        f'observed frequency correct</text>'
    )
    parts.append('</svg>')
    svg = "\n".join(parts)

    # per-bin rows for the HTML table
    rows_html = []
    for bn in bins:
        label = f"[{bn['lo']:.1f},{bn['hi']:.1f})"
        if bn["n"] == 0:
            rows_html.append(
                f"<tr><td>{label}</td><td>0</td><td>&ndash;</td>"
                f"<td>&ndash;</td><td>&ndash;</td></tr>"
            )
        else:
            gap = bn["obs_freq"] - bn["mean_pred"]
            rows_html.append(
                f"<tr><td>{label}</td><td>{bn['n']}</td>"
                f"<td>{bn['mean_pred']:.3f}</td><td>{bn['obs_freq']:.3f}</td>"
                f"<td>{gap:+.3f}</td></tr>"
            )
    table_html = "\n".join(rows_html)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AI-difficulty calibration</title>
<style>
  body {{ font-family: system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
         margin: 2rem auto; max-width: 720px; color: #1a1a1a; padding: 0 1rem; }}
  h1 {{ font-size: 1.3rem; }}
  .meta {{ color: #555; font-size: 0.9rem; margin-bottom: 1rem; }}
  .scores {{ display: flex; gap: 2rem; margin: 1rem 0; font-size: 1rem; }}
  .scores b {{ font-size: 1.25rem; }}
  table {{ border-collapse: collapse; margin-top: 1rem; font-size: 0.9rem;
           width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 4px 10px; text-align: right; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ background: #f5f7fa; }}
  figure {{ margin: 0; overflow-x: auto; }}
</style>
</head>
<body>
<h1>AI-difficulty calibration &mdash; reliability diagram</h1>
<p class="meta">Rasch/1PL model. Fitted &theta; (train) = {theta:+.3f}.
   Held-out reviews = {n_held}. Points on the dashed diagonal = perfectly
   calibrated; point size &prop; number of held-out reviews in the bin.</p>
<figure>
{svg}
</figure>
<div class="scores">
  <span>Brier score: <b>{brier:.4f}</b></span>
  <span>Log loss: <b>{logloss:.4f}</b></span>
  <span style="color:#888">(0 = perfect, lower is better)</span>
</div>
<table>
  <thead>
    <tr><th>bucket</th><th>n</th><th>mean pred</th><th>observed</th>
        <th>gap</th></tr>
  </thead>
  <tbody>
{table_html}
  </tbody>
</table>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)


def main() -> None:
    ap = argparse.ArgumentParser(description="AI-difficulty calibration analysis")
    ap.add_argument("collection", help="path to a collection.anki2")
    ap.add_argument("--split", type=float, default=0.7, help="train fraction (by time)")
    args = ap.parse_args()

    ai = json.loads(SIDECAR.read_text())
    rows = load_reviews(args.collection)

    # Build aligned (correct, ai_logit) points for reviews that have id::+ai.
    pts = []  # (correct, ai_logit)
    skipped_no_id = skipped_no_ai = 0
    for _rid, ease, tags in rows:
        item_id, _coarse = parse_tags(tags)
        y = 1 if ease >= 2 else 0
        if not item_id:
            skipped_no_id += 1
            continue
        entry = ai.get(item_id) or ai.get(item_id.rsplit("-p", 1)[0])
        if not entry:
            skipped_no_ai += 1
            continue
        pts.append((y, logit(float(entry["ai_difficulty"]))))

    n = len(pts)
    print(f"reviews in log: {len(rows)} | usable (have id::+ai): {n}")
    print(f"  skipped: no id:: tag={skipped_no_id}, no AI rating={skipped_no_ai}")
    if n < 4:
        print("\nNot enough usable reviews to split — answer more questions, then re-run.")
        return

    k = max(1, int(n * args.split))
    train, held = pts[:k], pts[k:]
    print(f"  train={len(train)}  held-out={len(held)}  (split={args.split})")
    if not held:
        print("\nHeld-out split is empty — lower --split or add reviews.")
        return

    theta = estimate_theta([(b, y) for (y, b) in train])
    held_obs = [(b, y) for (y, b) in held]

    brier, logloss = brier_and_logloss(theta, held_obs)
    bins = reliability_bins(theta, held_obs)

    print_table(bins, brier, logloss, theta, len(held))

    png = DOCS / "calibration.png"
    html = DOCS / "calibration.html"
    print("\nChart:")
    if write_matplotlib_chart(bins, brier, logloss, png):
        print(f"  wrote matplotlib PNG -> {png}")
    else:
        write_svg_chart(bins, brier, logloss, theta, len(held), html)
        print(f"  matplotlib not available; wrote self-contained SVG -> {html}")


if __name__ == "__main__":
    main()
