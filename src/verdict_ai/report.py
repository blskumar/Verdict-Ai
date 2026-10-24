"""Report generators: Markdown, HTML, JSON, CSV."""
from __future__ import annotations

import csv
import json
from html import escape
from io import StringIO
from pathlib import Path
from typing import Any

from verdict_ai.models import GateResult

_ICON: dict[str, str] = {
    "approve": "✅",
    "revise": "✏️",
    "reject": "❌",
    "human_review": "👤",
}


def _mean_score(r: GateResult) -> float:
    return sum(r.scores.values()) / len(r.scores) if r.scores else 1.0


def _md_cell(text: str) -> str:
    """Escape pipe characters and line breaks for Markdown table cells."""
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")


def _csv_cell(val: Any) -> str:
    """Sanitize CSV cells to prevent Excel formula execution."""
    s = str(val) if val is not None else ""
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


# ------------------------------------------------------------------
# Formatters
# ------------------------------------------------------------------

def to_markdown(results: list[GateResult], title: str = "Verdict AI Report") -> str:
    lines = [
        f"# {title}",
        "",
        f"**Cases:** {len(results)}",
        "",
        "| Case ID | Decision | Mean Score | Reasons |",
        "|---------|----------|------------|---------|",
    ]
    for r in results:
        icon = _ICON.get(r.decision, "❔")
        reasons = "; ".join(_md_cell(x) for x in r.reasons) if r.reasons else "—"
        case_id_safe = _md_cell(r.case_id)
        lines.append(
            f"| `{case_id_safe}` | {icon} {r.decision} | {_mean_score(r):.2f} | {reasons} |"
        )
    if any(r.models_used for r in results):
        lines += ["", "## Models used", ""]
        for r in results:
            if r.models_used:
                lines.append(f"- **{r.case_id}**: {', '.join(r.models_used)}")
    return "\n".join(lines) + "\n"


def to_html(results: list[GateResult], title: str = "Verdict AI Report") -> str:
    rows_html = ""
    for r in results:
        icon = _ICON.get(r.decision, "❔")
        badge_cls = f"badge-{r.decision.replace('_', '-')}"
        reasons_html = "<br>".join(escape(x) for x in r.reasons) if r.reasons else "—"
        models_html = (
            f'<br><small class="models">Models: {escape(", ".join(r.models_used))}</small>'
            if r.models_used else ""
        )
        rows_html += (
            f"<tr>"
            f'<td><code>{escape(r.case_id)}</code></td>'
            f'<td><span class="badge {badge_cls}" aria-label="{escape(r.decision)}">'
            f'{icon} <span class="badge-label">{r.decision}</span></span></td>'
            f"<td>{_mean_score(r):.2f}</td>"
            f"<td>{reasons_html}{models_html}</td>"
            f"</tr>\n"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         margin:2rem;color:#1a1a1a;background:#f8f9fa}}
    h1{{margin-bottom:1rem;font-size:1.6rem}}
    table{{width:100%;border-collapse:collapse;background:#fff;
           box-shadow:0 1px 4px rgba(0,0,0,.1);border-radius:6px;overflow:hidden}}
    th,td{{padding:10px 14px;text-align:left;border-bottom:1px solid #eee}}
    th{{background:#f1f3f5;font-weight:600;color:#555}}
    tr:last-child td{{border-bottom:none}}
    .badge{{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;
            border-radius:12px;font-size:.8rem;font-weight:600}}
    .badge-approve{{background:#d3f9d8;color:#2b8a3e}}
    .badge-revise{{background:#fff3bf;color:#7c5800}}
    .badge-reject{{background:#ffe3e3;color:#c92a2a}}
    .badge-human-review{{background:#d0ebff;color:#1864ab}}
    .models{{color:#888}}
  </style>
</head>
<body>
  <h1>{escape(title)}</h1>
  <table>
    <thead><tr>
      <th>Case ID</th><th>Decision</th><th>Mean Score</th><th>Reasons</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</body>
</html>
"""


def to_json(results: list[GateResult]) -> str:
    return json.dumps([r.model_dump() for r in results], indent=2)


def to_csv(results: list[GateResult]) -> str:
    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(["case_id", "decision", "score_mean", "reasons", "models_used"])
    for r in results:
        w.writerow([
            _csv_cell(r.case_id),
            _csv_cell(r.decision),
            f"{_mean_score(r):.4f}",
            _csv_cell(" | ".join(r.reasons)),
            _csv_cell(", ".join(r.models_used)),
        ])
    return buf.getvalue()


def save_reports(
    results: list[GateResult],
    output_dir: str | Path,
    stem: str = "verdict",
) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {
        "md": out / f"{stem}.md",
        "html": out / f"{stem}.html",
        "json": out / f"{stem}.json",
        "csv": out / f"{stem}.csv",
    }
    paths["md"].write_text(to_markdown(results), encoding="utf-8")
    paths["html"].write_text(to_html(results), encoding="utf-8")
    paths["json"].write_text(to_json(results), encoding="utf-8")
    paths["csv"].write_text(to_csv(results), encoding="utf-8")
    return paths
