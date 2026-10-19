"""Static dashboard + charts generated from analysis results (Phase 8). stdlib + matplotlib.
Predicted and measured values are drawn/labelled separately. Nothing is hard-coded."""
from __future__ import annotations
import html
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def _e(x): return html.escape("" if x is None else str(x))
def _f(x, nd=3): return "n/a" if x is None or (isinstance(x, float) and x != x) else (f"{x:.{nd}f}" if isinstance(x, float) else _e(x))


def chart_run(res: dict, out_dir: Path) -> list[str]:
    files = []
    cps, committed = res.get("_checkpoints") or [], res.get("_committed") or []
    v = (res.get("vulnerability_s") or {}).get("values")
    if v:
        fig, ax = plt.subplots(figsize=(6, 3.2)); ax.hist(v, bins=30, color="#2b6cb0")
        ax.set_xlabel("measured per-event vulnerability (s)"); ax.set_ylabel("events")
        ax.set_title(f"{res['run_id']}: measured vulnerability (confirmation-based)")
        p = out_dir / f"{res['run_id']}.vulnerability.png"; fig.tight_layout(); fig.savefig(p, dpi=110); plt.close(fig)
        files.append(p.name)
    if cps:
        t_max = max([c["confirmed_t"] or c["created_t"] for c in cps] + [0]) + 1
        grid = np.arange(0, t_max, max(t_max / 400, 0.01)); age = m_age(cps, grid)
        fig, ax = plt.subplots(figsize=(6, 3.2)); ax.plot(grid, age, color="#2b6cb0", label="measured: age of latest CONFIRMED checkpoint")
        for o in res.get("outages", []): ax.axvspan(o["observed_start_s"], o["observed_end_s"], color="#e53e3e", alpha=0.15, label="outage (observed)")
        h, l = ax.get_legend_handles_labels(); ax.legend(dict(zip(l, h)).values(), dict(zip(l, h)).keys(), fontsize=7)
        ax.set_xlabel("time (s)"); ax.set_ylabel("age (s)")
        p = out_dir / f"{res['run_id']}.age.png"; fig.tight_layout(); fig.savefig(p, dpi=110); plt.close(fig)
        files.append(p.name)
    return files


def m_age(cps, grid):
    from . import metrics
    return metrics.confirmed_age_series(cps, grid)


def build_dashboard(results: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, sections = [], []
    for r in results:
        charts = chart_run(r, out_dir)
        cc = r.get("checkpoint_counts"); vs = r.get("vulnerability_s"); rq = r.get("requests")
        banner = ('<p class="warn">MOCK harness run: not real HMS data.</p>' if r.get("mock") else "")
        rows.append(f"<tr><td><a href='#{_e(r['run_id'])}'>{_e(r['run_id'])}</a></td><td>{_e(r['status'])}</td>"
                    f"<td>{_e(r['mode'])}</td><td>{_e(r['seed'])}</td>"
                    f"<td>{_f(rq['p95_ms']) if rq else 'n/a'}</td><td>{_f(vs['mean']) if vs else 'n/a'}</td></tr>")
        cc_html = ("<table><tr>" + "".join(f"<th>{k}</th>" for k in cc) + "</tr><tr>" +
                   "".join(f"<td>{v}</td>" for v in cc.values()) + "</tr></table>") if cc else \
                  "<p>No checkpoint telemetry in this run (requires Person 1's events).</p>"
        out_rows = "".join(f"<tr><td>{_e(o['label'])}</td><td>{_e(o['mode'])}</td><td>{_f(o['planned'][0],1)}–{_f(o['planned'][1],1)}</td>"
                           f"<td>{_f(o['observed_start_s'])}–{_f(o['observed_end_s'])}</td><td>{_f(o['max_confirmed_age_s'])}</td>"
                           f"<td>{_f(o['recovery_lag_s'])}</td></tr>" for o in r.get("outages", []))
        tam = "".join(f"<tr><td>{_e(t['scenario'])}</td><td>{_e(t['expected'])}</td><td>{_e(t['observed'])}</td>"
                      f"<td>{_e(t['verifier_source'])}</td><td>{_e(t['matches_expectation'])}</td></tr>" for t in r.get("tamper_results", []))
        imgs = "".join(f"<img src='{c}' alt='{c}'>" for c in charts)
        sections.append(f"""<section id="{_e(r['run_id'])}"><h2>{_e(r['run_id'])}</h2>{banner}
<p>status <b>{_e(r['status'])}</b> · seed {_e(r['seed'])} · config sha256 <code>{_e((r.get('config_sha256') or '')[:16])}…</code></p>
<h3>Checkpoint monitor</h3>{cc_html}
<h3>Measured exposure</h3><p>{('mean %s s, p95 %s s over %s events (%s uncovered)' % (_f(vs['mean']), _f(vs['p95']), vs['n_events'], vs['n_uncovered'])) if vs else 'n/a'}</p>
<h3>Outages</h3><table><tr><th>label</th><th>mode</th><th>planned (s)</th><th>observed (s)</th><th>max confirmed age</th><th>recovery lag</th></tr>{out_rows}</table>
<h3>Tamper verification</h3><table><tr><th>scenario</th><th>expected</th><th>observed</th><th>source</th><th>match</th></tr>{tam}</table>
<div class="charts">{imgs}</div></section>""")
    page = f"""<!doctype html><meta charset="utf-8"><title>HMS anchoring research</title>
<style>body{{font:14px system-ui;margin:2rem;max-width:980px}}table{{border-collapse:collapse;margin:.5rem 0}}td,th{{border:1px solid #ccc;padding:3px 8px}}
.warn{{background:#fff3cd;padding:4px 8px}}img{{max-width:48%}}code{{background:#eee}}</style>
<h1>Experiment results</h1><p>Generated from raw telemetry only. Predicted series (when present) are drawn separately from measured series.</p>
<table><tr><th>run</th><th>status</th><th>mode</th><th>seed</th><th>p95 latency ms</th><th>mean vulnerability s</th></tr>{''.join(rows)}</table>{''.join(sections)}"""
    p = out_dir / "index.html"; p.write_text(page, encoding="utf-8"); return p
