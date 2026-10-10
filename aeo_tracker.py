#!/usr/bin/env python3
"""
AEO Visibility Tracker

Asks ChatGPT, Claude, Perplexity and Gemini the questions your buyers ask, then
measures how often your brand shows up in the answers compared with competitors.

Usage:
    python aeo_tracker.py --config config.yaml
    python aeo_tracker.py --config config.example.yaml --mock     # demo, no API keys
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import yaml

import engines
from report import build_html

ENGINE_LABELS = {"openai": "ChatGPT", "anthropic": "Claude", "perplexity": "Perplexity",
                 "gemini": "Gemini"}


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    brand = cfg["brand"]
    brand.setdefault("aliases", [brand["name"]])
    entities = [{"name": brand["name"], "aliases": brand["aliases"], "is_brand": True,
                 "domain": brand.get("domain", "")}]
    for c in cfg.get("competitors", []):
        if isinstance(c, str):
            c = {"name": c}
        entities.append({"name": c["name"], "aliases": c.get("aliases", [c["name"]]),
                         "is_brand": False, "domain": c.get("domain", "")})
    cfg["entities"] = entities
    cfg.setdefault("runs_per_prompt", 1)
    return cfg


def alias_pattern(aliases):
    parts = sorted((re.escape(a) for a in aliases), key=len, reverse=True)
    return re.compile(r"(?<![\w.])(" + "|".join(parts) + r")(?![\w])", re.IGNORECASE)


def analyse(answer, citations, entities):
    """Which entities appear, how often, and in what order."""
    found = []
    for e in entities:
        matches = list(alias_pattern(e["aliases"]).finditer(answer))
        cited = bool(e["domain"]) and any(e["domain"].lower() in (u or "").lower() for u in citations)
        found.append({"name": e["name"], "is_brand": e["is_brand"], "count": len(matches),
                      "first": matches[0].start() if matches else None, "cited": cited})
    ranked = sorted([f for f in found if f["first"] is not None], key=lambda f: f["first"])
    for i, f in enumerate(ranked, 1):
        f["rank"] = i
    for f in found:
        f.setdefault("rank", None)
    return found


def run(cfg, mock, workers):
    jobs = []
    for engine_name, engine_cfg in cfg["engines"].items():
        if engine_name not in engines.ENGINES:
            print(f"Skipping unknown engine: {engine_name}")
            continue
        model = (engine_cfg or {}).get("model", "")
        for prompt in cfg["prompts"]:
            for run_no in range(1, cfg["runs_per_prompt"] + 1):
                jobs.append((engine_name, model, prompt, run_no))

    def do(job):
        engine_name, model, prompt, run_no = job
        try:
            if mock:
                text, cites = engines.ask_mock(prompt + f"#{run_no}", model, cfg["entities"], engine_name)
            else:
                text, cites = engines.ENGINES[engine_name](prompt, model)
            return {"engine": engine_name, "model": model, "prompt": prompt, "run": run_no,
                    "answer": text, "citations": cites, "error": None}
        except engines.EngineError as e:
            return {"engine": engine_name, "model": model, "prompt": prompt, "run": run_no,
                    "answer": "", "citations": [], "error": str(e)}

    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(do, j) for j in jobs]
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            status = "error" if r["error"] else "ok"
            print(f"  [{i}/{len(jobs)}] {ENGINE_LABELS.get(r['engine'], r['engine'])}: "
                  f"{r['prompt'][:60]} ({status})")
            if not r["error"]:
                r["mentions"] = analyse(r["answer"], r["citations"], cfg["entities"])
            results.append(r)
    return results


def summarise(results, entities):
    ok = [r for r in results if not r["error"]]
    engines_seen = sorted({r["engine"] for r in ok})
    summary = {"by_engine": {}, "leaderboard": [], "by_prompt": {}, "domains": []}

    for eng in engines_seen + ["all"]:
        rows = [r for r in ok if eng == "all" or r["engine"] == eng]
        n = len(rows)
        stats = {}
        total_answer_mentions = 0
        for e in entities:
            hits = [next(m for m in r["mentions"] if m["name"] == e["name"]) for r in rows]
            mentioned = [h for h in hits if h["count"] > 0]
            total_answer_mentions += len(mentioned)
            ranks = [h["rank"] for h in mentioned]
            stats[e["name"]] = {
                "mention_rate": len(mentioned) / n if n else 0,
                "answers": len(mentioned),
                "avg_rank": sum(ranks) / len(ranks) if ranks else None,
                "first_place": sum(1 for h in mentioned if h["rank"] == 1),
                "cited": sum(1 for h in hits if h["cited"]),
            }
        for name in stats:
            stats[name]["share_of_voice"] = (stats[name]["answers"] / total_answer_mentions
                                             if total_answer_mentions else 0)
        summary["by_engine"][eng] = {"answers": n, "entities": stats}

    overall = summary["by_engine"].get("all", {"entities": {}})["entities"]
    summary["leaderboard"] = sorted(
        [{"name": e["name"], "is_brand": e["is_brand"], **overall.get(e["name"], {})} for e in entities],
        key=lambda x: (-x.get("share_of_voice", 0), x.get("avg_rank") or 99))

    brand = next(e["name"] for e in entities if e["is_brand"])
    for r in ok:
        p = summary["by_prompt"].setdefault(r["prompt"], {})
        hit = next(m for m in r["mentions"] if m["name"] == brand)
        others = [m["name"] for m in sorted(r["mentions"], key=lambda m: m["rank"] or 99)
                  if m["count"] and not m["is_brand"]]
        cell = p.setdefault(r["engine"], {"mentioned": 0, "runs": 0, "best_rank": None, "competitors": set()})
        cell["runs"] += 1
        if hit["count"]:
            cell["mentioned"] += 1
            if cell["best_rank"] is None or hit["rank"] < cell["best_rank"]:
                cell["best_rank"] = hit["rank"]
        cell["competitors"].update(others[:3])

    domain_counts = defaultdict(int)
    for r in ok:
        for u in r["citations"]:
            host = urlparse(u).netloc.replace("www.", "") if u else ""
            if host:
                domain_counts[host] += 1
    summary["domains"] = sorted(domain_counts.items(), key=lambda x: -x[1])[:15]
    summary["engines"] = engines_seen
    summary["brand"] = brand
    return summary


def write_outputs(cfg, results, summary, out_dir, mock):
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    run_dir = os.path.join(out_dir, stamp + ("_mock" if mock else ""))
    os.makedirs(run_dir, exist_ok=True)
    brand = summary["brand"]

    with open(os.path.join(run_dir, "answers.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(os.path.join(run_dir, "answers.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        names = [e["name"] for e in cfg["entities"]]
        w.writerow(["Engine", "Model", "Prompt", "Run", "Brand mentioned", "Brand rank"] +
                   [f"{n} rank" for n in names] + ["Citations", "Error"])
        for r in results:
            m = {x["name"]: x for x in r.get("mentions", [])}
            b = m.get(brand, {})
            w.writerow([ENGINE_LABELS.get(r["engine"], r["engine"]), r["model"], r["prompt"], r["run"],
                        "yes" if b.get("count") else "no", b.get("rank") or ""] +
                       [m.get(n, {}).get("rank") or "" for n in names] +
                       [" ".join(r["citations"]), r["error"] or ""])

    with open(os.path.join(run_dir, "summary.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Engine", "Entity", "Mention rate", "Share of voice", "Avg rank", "Ranked first", "Cited"])
        for eng, data in summary["by_engine"].items():
            for name, s in data["entities"].items():
                w.writerow([ENGINE_LABELS.get(eng, "All engines"), name, round(s["mention_rate"], 3),
                            round(s["share_of_voice"], 3),
                            round(s["avg_rank"], 2) if s["avg_rank"] else "", s["first_place"], s["cited"]])

    # History across runs, for trend tracking
    history_path = os.path.join(out_dir, "history.csv")
    new_file = not os.path.exists(history_path)
    with open(history_path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["Run", "Mock", "Engine", "Entity", "Mention rate", "Share of voice", "Avg rank"])
        for eng, data in summary["by_engine"].items():
            for name, s in data["entities"].items():
                w.writerow([stamp, mock, eng, name, round(s["mention_rate"], 3),
                            round(s["share_of_voice"], 3), round(s["avg_rank"], 2) if s["avg_rank"] else ""])

    html = build_html(cfg, results, summary, stamp, mock, ENGINE_LABELS)
    with open(os.path.join(run_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return run_dir


def main():
    p = argparse.ArgumentParser(description="Track how often AI assistants mention your brand.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--out", default="results")
    p.add_argument("--mock", action="store_true", help="Use fake answers (no API keys, for demos)")
    p.add_argument("--workers", type=int, default=4, help="Parallel requests (default 4)")
    args = p.parse_args()

    load_env()
    if not os.path.exists(args.config):
        sys.exit(f"Config not found: {args.config}. Copy config.example.yaml to config.yaml and edit it.")
    cfg = load_config(args.config)

    total = len(cfg["prompts"]) * len(cfg["engines"]) * cfg["runs_per_prompt"]
    print(f"Running {total} queries for {cfg['brand']['name']}" + (" (mock mode)" if args.mock else ""))
    results = run(cfg, args.mock, args.workers)
    errors = [r for r in results if r["error"]]
    if len(errors) == len(results):
        sys.exit(f"Every query failed. First error: {errors[0]['error']}")

    summary = summarise(results, cfg["entities"])
    run_dir = write_outputs(cfg, results, summary, args.out, args.mock)

    s = summary["by_engine"]["all"]["entities"][summary["brand"]]
    print(f"\n{summary['brand']} was mentioned in {s['mention_rate']:.0%} of answers, "
          f"share of voice {s['share_of_voice']:.0%}.")
    if errors:
        print(f"{len(errors)} queries failed. See answers.csv for details.")
    print(f"Report: {os.path.join(run_dir, 'report.html')}")


if __name__ == "__main__":
    main()
