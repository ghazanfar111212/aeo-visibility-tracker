"""Builds a single self-contained HTML report for one tracker run."""

import html
import re


def esc(s):
    return html.escape(str(s if s is not None else ""))


def highlight(answer, entities):
    text = esc(answer)
    for e in entities:
        for alias in sorted(e["aliases"], key=len, reverse=True):
            pat = re.compile(r"(?<![\w.])(" + re.escape(esc(alias)) + r")(?![\w])", re.IGNORECASE)
            cls = "hl-brand" if e["is_brand"] else "hl-comp"
            text = pat.sub(lambda m: f'<mark class="{cls}">{m.group(1)}</mark>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text.replace("\n", "<br>")


def pct(x):
    return f"{x * 100:.0f}%"


def build_html(cfg, results, summary, stamp, mock, labels):
    brand = summary["brand"]
    engines = summary["engines"]
    overall = summary["by_engine"]["all"]
    b = overall["entities"][brand]
    leader = summary["leaderboard"][0]

    if leader["name"] == brand:
        headline = (f"{esc(brand)} leads: mentioned in {pct(b['mention_rate'])} of "
                    f"{overall['answers']} AI answers.")
    else:
        headline = (f"{esc(brand)} appeared in {pct(b['mention_rate'])} of {overall['answers']} AI answers. "
                    f"{esc(leader['name'])} appeared in {pct(leader['mention_rate'])}.")

    max_sov = max((x.get("share_of_voice", 0) for x in summary["leaderboard"]), default=0) or 1
    bars = []
    for x in summary["leaderboard"]:
        w = x.get("share_of_voice", 0) / max_sov * 100
        rank = f"avg position {x['avg_rank']:.1f}" if x.get("avg_rank") else "not mentioned"
        bars.append(
            f'<div class="bar-row{" is-brand" if x["is_brand"] else ""}">'
            f'<div class="bar-name">{esc(x["name"])}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{w:.1f}%"></div></div>'
            f'<div class="bar-val">{pct(x.get("share_of_voice", 0))}</div>'
            f'<div class="bar-meta">in {pct(x.get("mention_rate", 0))} of answers, {rank}</div></div>')

    # Engine x entity mention-rate grid
    head = "".join(f"<th>{esc(labels.get(e, e))}</th>" for e in engines)
    grid_rows = []
    for x in summary["leaderboard"]:
        cells = []
        for eng in engines:
            r = summary["by_engine"][eng]["entities"][x["name"]]["mention_rate"]
            cells.append(f'<td class="heat" style="--a:{r:.2f}">{pct(r)}</td>')
        grid_rows.append(f'<tr class="{"is-brand" if x["is_brand"] else ""}"><th scope="row">'
                         f'{esc(x["name"])}</th>{"".join(cells)}</tr>')

    # Prompt coverage
    prompt_rows = []
    for prompt, by_eng in summary["by_prompt"].items():
        cells = []
        for eng in engines:
            c = by_eng.get(eng)
            if not c:
                cells.append('<td class="na">n/a</td>')
            elif c["mentioned"]:
                runs = " ({}/{})".format(c["mentioned"], c["runs"]) if c["runs"] > 1 else ""
                cells.append('<td class="yes">Yes, position {}{}</td>'.format(c["best_rank"], runs))
            else:
                comp = ", ".join(sorted(c["competitors"])) or "no tracked brands"
                cells.append(f'<td class="no">No<span>{esc(comp)}</span></td>')
        prompt_rows.append(f'<tr><th scope="row">{esc(prompt)}</th>{"".join(cells)}</tr>')

    domains = ""
    if summary["domains"]:
        items = "".join(f"<li><span>{esc(d)}</span><span>{n}</span></li>" for d, n in summary["domains"])
        domains = (f'<section><h2>Sources the engines cited</h2><p class="note">Getting mentioned on these '
                   f'sites is the most direct way to show up in answers that use web search.</p>'
                   f'<ul class="domains">{items}</ul></section>')

    answers = []
    for r in sorted(results, key=lambda r: (r["prompt"], r["engine"], r["run"])):
        hit = next((m for m in r.get("mentions", []) if m["name"] == brand), None)
        tag = ("error" if r["error"] else "mentioned" if hit and hit["count"] else "missing")
        tag_text = {"error": "Error", "mentioned": f"Mentioned, position {hit['rank'] if hit else ''}",
                    "missing": "Not mentioned"}[tag]
        body = esc(r["error"]) if r["error"] else highlight(r["answer"], cfg["entities"])
        cites = ""
        if r["citations"]:
            cites = "<p class='cites'>Sources: " + " ".join(
                f'<a href="{esc(u)}" rel="noopener">{esc(u)}</a>' for u in r["citations"][:8]) + "</p>"
        answers.append(
            f'<details><summary><span class="eng">{esc(labels.get(r["engine"], r["engine"]))}</span>'
            f'<span class="q">{esc(r["prompt"])}</span><span class="tag tag-{tag}">{tag_text}</span></summary>'
            f'<div class="answer">{body}{cites}</div></details>')

    mock_note = ('<p class="mock">Demo run with simulated answers. Run without --mock to query the real '
                 'engines.</p>') if mock else ""
    date = stamp.replace("_", " ")
    date = date[:-2] + ":" + date[-2:]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI visibility report for {esc(brand)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{ --bg:#F2F5F7; --panel:#FFFFFF; --ink:#1B2A35; --muted:#5B6B77; --rule:#D6DEE4;
  --brand:#0F7B6C; --brand-soft:#D7EFEA; --comp:#9AAAB6; --miss:#B4441E; --miss-soft:#F7E3DB; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.55 "IBM Plex Sans", "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
main {{ max-width:1040px; margin:0 auto; padding:48px 24px 80px; }}
header p {{ color:var(--muted); margin:4px 0; }}
h1 {{ font-size:2.1rem; line-height:1.2; font-weight:700; margin:12px 0 8px; max-width:24ch; }}
h2 {{ font-size:1.25rem; margin:0 0 4px; }}
section {{ background:var(--panel); border:1px solid var(--rule); border-radius:10px;
  padding:28px; margin-top:24px; }}
.note {{ color:var(--muted); margin:0 0 20px; max-width:70ch; }}
.mock {{ background:#FFF4D6; border:1px solid #E8D49A; padding:10px 14px; border-radius:6px; }}
.bar-row {{ display:grid; grid-template-columns:180px 1fr 56px; gap:4px 14px; align-items:center;
  padding:10px 0; border-top:1px solid var(--rule); }}
.bar-row:first-of-type {{ border-top:0; }}
.bar-name {{ font-weight:500; }}
.bar-track {{ height:14px; background:var(--bg); border-radius:7px; overflow:hidden; }}
.bar-fill {{ height:100%; background:var(--comp); border-radius:7px; }}
.bar-val {{ font-weight:600; text-align:right; font-variant-numeric:tabular-nums; }}
.bar-meta {{ grid-column:2 / 4; color:var(--muted); font-size:.85rem; }}
.is-brand .bar-fill {{ background:var(--brand); }}
.is-brand .bar-name, .is-brand .bar-val {{ color:var(--brand); }}
.table-wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; font-size:.95rem; }}
th, td {{ padding:10px 12px; text-align:left; border-bottom:1px solid var(--rule); vertical-align:top; }}
thead th {{ color:var(--muted); font-weight:500; white-space:nowrap; }}
tbody th {{ font-weight:500; }}
tr.is-brand th {{ color:var(--brand); }}
td.heat {{ background:rgba(15,123,108,var(--a)); font-variant-numeric:tabular-nums; }}
tr:not(.is-brand) td.heat {{ background:rgba(91,107,119,calc(var(--a) * .55)); }}
td.yes {{ color:var(--brand); font-weight:500; }}
td.no {{ color:var(--miss); background:var(--miss-soft); font-weight:500; }}
td.no span {{ display:block; color:var(--muted); font-weight:400; font-size:.85rem; }}
td.na {{ color:var(--muted); }}
.domains {{ list-style:none; padding:0; margin:0; columns:2; column-gap:32px; }}
.domains li {{ display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--rule);
  break-inside:avoid; }}
details {{ border-top:1px solid var(--rule); }}
summary {{ display:grid; grid-template-columns:110px 1fr auto; gap:12px; padding:12px 4px; cursor:pointer;
  list-style:none; align-items:baseline; }}
summary::-webkit-details-marker {{ display:none; }}
summary:focus-visible, a:focus-visible {{ outline:2px solid var(--brand); outline-offset:2px; }}
.eng {{ color:var(--muted); }}
.tag {{ font-size:.85rem; padding:2px 10px; border-radius:20px; white-space:nowrap; }}
.tag-mentioned {{ background:var(--brand-soft); color:var(--brand); }}
.tag-missing {{ background:var(--miss-soft); color:var(--miss); }}
.tag-error {{ background:#EEE; color:#555; }}
.answer {{ padding:4px 4px 20px 126px; max-width:90ch; }}
.cites {{ font-size:.85rem; color:var(--muted); word-break:break-all; }}
mark.hl-brand {{ background:var(--brand-soft); color:var(--brand); font-weight:600; padding:0 2px; }}
mark.hl-comp {{ background:#E6EBEF; color:var(--ink); padding:0 2px; }}
@media (max-width:640px) {{
  .bar-row {{ grid-template-columns:1fr 56px; }} .bar-track {{ grid-column:1 / 3; }}
  .bar-meta {{ grid-column:1 / 3; }} .bar-name {{ grid-column:1; }}
  summary {{ grid-template-columns:1fr; }} .answer {{ padding-left:4px; }} .domains {{ columns:1; }}
}}
</style></head>
<body><main>
<header>
<p>AI visibility report, {esc(date)}</p>
<h1>{headline}</h1>
<p>{len(cfg["prompts"])} buyer questions asked across {", ".join(esc(labels.get(e, e)) for e in engines)}.</p>
{mock_note}
</header>

<section>
<h2>Share of voice</h2>
<p class="note">Of all the times a tracked brand appeared in an answer, the share that went to each one.
Position is the order brands were first mentioned in the answer.</p>
{"".join(bars)}
</section>

<section>
<h2>Mention rate by engine</h2>
<p class="note">Share of each engine's answers that mention the brand. Engines draw on different sources,
so gaps here show where to focus.</p>
<div class="table-wrap"><table><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(grid_rows)}</tbody></table></div>
</section>

<section>
<h2>Question by question</h2>
<p class="note">Where {esc(brand)} is missing, the brands that were named instead are listed. These are the
questions to create content for.</p>
<div class="table-wrap"><table><thead><tr><th>Question</th>{head}</tr></thead><tbody>{"".join(prompt_rows)}</tbody></table></div>
</section>

{domains}

<section>
<h2>Every answer</h2>
<p class="note">Open any answer to read it in full, with tracked brands highlighted.</p>
{"".join(answers)}
</section>
</main></body></html>"""
