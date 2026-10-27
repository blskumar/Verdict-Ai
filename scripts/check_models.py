"""Check the models in models.yaml against OpenRouter, and optionally try each one.

    python scripts/check_models.py            # is every configured model still listed? price? context?
    python scripts/check_models.py --ping     # also send one tiny request to each (uses your key and quota)
    python scripts/check_models.py --free     # list the free models available right now

The key comes from OPENROUTER_API_KEY in .env. It is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "models.yaml" if (ROOT / "models.yaml").exists() else ROOT / "src" / "verdict_ai" / "models.yaml"
API = "https://openrouter.ai/api/v1"


def load_key() -> str:
    try:
        from dotenv import dotenv_values
        return (dotenv_values(ROOT / ".env").get("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY") or "").strip()
    except ImportError:
        return os.getenv("OPENROUTER_API_KEY", "").strip()


def per_million(model: dict) -> str:
    p = model.get("pricing", {})
    try:
        a, b = float(p.get("prompt", 0)) * 1e6, float(p.get("completion", 0)) * 1e6
    except (TypeError, ValueError):
        return "?"
    return "free" if a == 0 and b == 0 else f"${a:.3f} in / ${b:.3f} out per 1M"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true", help="send one tiny request to each configured model")
    ap.add_argument("--free", action="store_true", help="list the free models available now")
    args = ap.parse_args()

    catalogue = {m["id"]: m for m in httpx.get(f"{API}/models", timeout=60).json()["data"]}
    if args.free:
        for m in sorted((m for m in catalogue.values() if m["id"].endswith(":free")), key=lambda m: -(m.get("context_length") or 0)):
            print(f"  {m['id']:52s} ctx {m.get('context_length')}")
        return 0

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    print(f"config: {CONFIG.relative_to(ROOT)}")
    missing, seen = [], {}
    for role, spec in cfg["roles"].items():
        print(f"\n{role}")
        for i, mid in enumerate(spec["models"], 1):
            m = catalogue.get(mid)
            if not m:
                missing.append(mid)
                print(f"  {i}. MISSING   {mid}")
            else:
                print(f"  {i}. ok        {mid}   {per_million(m)}   ctx {m.get('context_length')}")
                seen[mid] = m
    if missing:
        print(f"\n{len(set(missing))} configured model(s) are not on OpenRouter any more: {', '.join(sorted(set(missing)))}")
        print("Replace them in models.yaml (see: python scripts/check_models.py --free).")

    if args.ping:
        key = load_key()
        if not key:
            print("\nNo OPENROUTER_API_KEY in .env: cannot ping.")
            return 1
        print("\nping (one request each):")
        for mid in seen:
            t = time.time()
            try:
                r = httpx.post(f"{API}/chat/completions", timeout=60,
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                               json={"model": mid, "max_tokens": 1500,
                                     "messages": [{"role": "user", "content": 'Reply with only this JSON: {"ok": true}'}]})
                if r.status_code == 200:
                    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
                    try:
                        json.loads(text.strip("`").removeprefix("json").strip())
                        verdict = "JSON ok"
                    except ValueError:
                        verdict = f"reply not JSON: {text[:40]!r}"
                else:
                    verdict = f"HTTP {r.status_code}: {r.text[:80]}"
            except Exception as exc:
                verdict = f"error: {str(exc)[:60]}"
            print(f"  {mid:46s} {time.time() - t:5.1f}s  {verdict}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
