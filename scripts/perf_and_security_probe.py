"""Performance, load and security probes against a throw-away Verdict AI server.

Usage:  python scripts/perf_and_security_probe.py [--port 8004]
Read-only against your files: it starts its own server on its own port and sends only local, invented data.
"""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def rss_mb(pid: int) -> float:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {pid}).WorkingSet64 / 1MB"], capture_output=True, text=True).stdout.strip()
    try:
        return round(float(out), 1)
    except ValueError:
        return -1.0


def children_rss(pid: int) -> float:
    cmd = f"$p=Get-CimInstance Win32_Process | Where-Object {{ $_.ParentProcessId -eq {pid} -or $_.ProcessId -eq {pid} }}; ($p | ForEach-Object {{ (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).WorkingSet64 }} | Measure-Object -Sum).Sum / 1MB"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()
    try:
        return round(float(out), 1)
    except ValueError:
        return -1.0


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))] * 1000, 1)


def timed(fn, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def make_data(n_cases: int):
    reqs = json.loads((DATA / "requirements/insurance_requirements.json").read_text(encoding="utf-8"))
    good = json.loads((DATA / "answer_key/insurance_answer_key.json").read_text(encoding="utf-8"))
    bad = json.loads((DATA / "seeded_bad/insurance_seeded_bad.json").read_text(encoding="utf-8"))
    base = good + bad
    cases = [{**base[i % len(base)], "id": f"{base[i % len(base)]['id']}-{i}"} for i in range(n_cases)]
    return reqs, cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8004)
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    report: dict = {"performance": {}, "security": {}}
    perf, sec = report["performance"], report["security"]

    t0 = time.time()
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "verdict_ai.web.app:create_app", "--factory", "--port", str(a.port), "--log-level", "warning"], cwd=ROOT)
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        perf["startup_seconds"] = round(time.time() - t0, 2)
        perf["memory_mb_idle"] = children_rss(srv.pid)
        c = httpx.Client(base_url=base, timeout=300)
        reqs, cases108 = make_data(108)
        body_eval = {"requirements": reqs, "cases": cases108, "domain": "insurance", "agent_mode": "mock"}
        body_an = {"requirements": reqs, "domain": "insurance", "agent_mode": "mock"}

        # ---- latency
        lat = {}
        for name, fn in {
            "GET / (page)": lambda: c.get("/"),
            "GET /api/domains": lambda: c.get("/api/domains"),
            "GET /api/sample": lambda: c.get("/api/sample", params={"domain": "banking/retail/checking"}),
            "POST /api/domain/check": lambda: c.post("/api/domain/check", json={"domain": "insurance", "requirements": reqs}),
            "POST /api/requirements/analyze (25 reqs)": lambda: c.post("/api/requirements/analyze", json=body_an),
            "POST /api/evaluate (108 cases)": lambda: c.post("/api/evaluate", json=body_eval),
            "POST /api/cases/export/csv (108)": lambda: c.post("/api/cases/export/csv", json=cases108),
        }.items():
            xs = timed(fn, 30)
            lat[name] = {"p50_ms": pct(xs, 0.5), "p95_ms": pct(xs, 0.95), "max_ms": round(max(xs) * 1000, 1)}
        perf["latency_30_sequential"] = lat

        # ---- page weight
        raw = c.get("/", headers={"Accept-Encoding": "identity"})
        gz = c.get("/", headers={"Accept-Encoding": "gzip"})
        perf["page_bytes"] = len(raw.content)
        perf["page_served_compressed"] = gz.headers.get("content-encoding") == "gzip"
        perf["page_gzip_would_be_bytes"] = len(gzip.compress(raw.content))

        # ---- file parsing speed
        parse = {}
        for rel in ["data/requirements/insurance_requirements.json", "data/requirements/insurance_requirements.pdf", "data/answer_key/insurance_test_cases.pdf",
                    "Banking_Domain_Sample_Requirements.docx", "Banking_Domain_Detailed_Test_Cases.xlsx", "banking_requirements_core_catalogue.xlsx", "banking_requirements_core_catalogue.pdf"]:
            p = ROOT / rel
            t = time.perf_counter()
            r = c.post("/api/upload", files={"file": (p.name, p.read_bytes())})
            parse[p.name] = {"status": r.status_code, "seconds": round(time.perf_counter() - t, 2), "kb": round(p.stat().st_size / 1024)}
        perf["upload_parse"] = parse

        # ---- scaling and memory
        scale = {}
        for n in (100, 500, 2000, 5000):
            rq, cs = make_data(n)
            t = time.perf_counter()
            r = c.post("/api/evaluate", json={"requirements": rq, "cases": cs, "domain": "insurance", "agent_mode": "mock"})
            scale[n] = {"status": r.status_code, "seconds": round(time.perf_counter() - t, 2), "response_kb": round(len(r.content) / 1024), "server_mb": children_rss(srv.pid)}
        perf["evaluate_scaling"] = scale
        rq, cs = make_data(20000)
        t = time.perf_counter()
        try:
            r = c.post("/api/evaluate", json={"requirements": rq, "cases": cs, "domain": "insurance", "agent_mode": "mock"})
            perf["evaluate_20000_cases"] = {"status": r.status_code, "seconds": round(time.perf_counter() - t, 1), "server_mb": children_rss(srv.pid)}
        except Exception as exc:
            perf["evaluate_20000_cases"] = {"error": str(exc)[:80]}

        # ---- concurrency
        conc = {}
        for workers, total in ((1, 12), (4, 24), (16, 48), (32, 64)):
            def one(_):
                t = time.perf_counter()
                with httpx.Client(base_url=base, timeout=300) as cl:
                    r = cl.post("/api/evaluate", json=body_eval)
                return r.status_code, time.perf_counter() - t
            t = time.perf_counter()
            with ThreadPoolExecutor(workers) as ex:
                res = list(ex.map(one, range(total)))
            wall = time.perf_counter() - t
            conc[f"{workers} clients"] = {"requests": total, "ok": sum(1 for s, _ in res if s == 200), "req_per_s": round(total / wall, 1),
                                          "p50_ms": pct([x for _, x in res], 0.5), "p95_ms": pct([x for _, x in res], 0.95)}
        perf["concurrency_evaluate_108_cases"] = conc
        perf["memory_mb_after_load"] = children_rss(srv.pid)

        # ---- pathological regex input (ReDoS) and nesting
        redos = {}
        for name, s in {"spaces": " " * 300_000, "a_space_repeat": "a " * 150_000, "numbers": "1." * 150_000, "dollars": "$1,000" * 50_000, "or_chain": "blocked or " * 30_000, "money_pairs": " ".join(f"${i}" for i in range(6000))}.items():
            t = time.perf_counter()
            r = c.post("/api/evaluate", json={"requirements": reqs[:1], "cases": [{"id": "X-1", "requirement_id": reqs[0]["id"], "scenario": s, "expected": s}], "agent_mode": "mock"})
            redos[name] = {"status": r.status_code, "seconds": round(time.perf_counter() - t, 2)}
        perf["pathological_input_seconds"] = redos
        perf["startup_to_end_memory_mb"] = children_rss(srv.pid)

        # =============================== SECURITY
        r = c.get("/")
        hdr = {k.lower(): v for k, v in r.headers.items()}
        wanted = ["content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy", "permissions-policy", "cross-origin-opener-policy"]
        sec["security_headers_missing_on_page"] = [h for h in wanted if h not in hdr]
        sec["server_header"] = hdr.get("server")
        page = r.text
        import re
        ext = re.findall(r'<script[^>]+src="(https?://[^"]+)"[^>]*>', page)
        sec["external_scripts"] = [{"src": s, "has_integrity_attr": "integrity=" in re.search(r'<script[^>]+' + re.escape(s) + r'[^>]*>', page).group(0), "pinned_version": "@latest" not in s and "cdn.tailwindcss.com" not in s} for s in ext]
        sec["inline_event_handlers_in_page"] = len(re.findall(r'\son[a-z]+="', page))

        cors = {}
        for origin in ("http://evil.example", "http://127.0.0.1:8000", "null"):
            pre = c.options("/api/evaluate", headers={"Origin": origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
            cors[origin] = pre.headers.get("access-control-allow-origin")
        sec["cors_allow_origin_by_requesting_origin"] = cors

        sec["methods"] = {m: c.request(m, "/api/evaluate").status_code for m in ("GET", "PUT", "DELETE", "PATCH", "TRACE")}
        trav = {}
        for path in ("/static/../pyproject.toml", "/static/%2e%2e/pyproject.toml", "/..%2fpyproject.toml", "/%2e%2e/%2e%2e/etc/passwd", "/.env", "/.git/config", "/src/verdict_ai/web/app.py", "/static/", "/static/index.html", "/models.yaml"):
            rr = c.get(path)
            trav[path] = rr.status_code if rr.status_code != 200 or path == "/static/index.html" else f"200 ({len(rr.content)} bytes)"
        sec["path_probe_status"] = trav

        leak = {}
        probes = {
            "bad_json": ("POST", "/api/evaluate", {"content": b"{oops", "headers": {"Content-Type": "application/json"}}),
            "wrong_types": ("POST", "/api/evaluate", {"json": {"requirements": "x", "cases": 5}}),
            "deep_nesting": ("POST", "/api/evaluate", {"content": ("[" * 4000 + "]" * 4000).encode(), "headers": {"Content-Type": "application/json"}}),
            "null_bytes_in_domain": ("GET", "/api/sample", {"params": {"domain": "insurance\x00x"}}),
            "huge_domain": ("GET", "/api/sample", {"params": {"domain": "a/" * 5000}}),
            "bad_upload": ("POST", "/api/upload", {"files": {"file": ("x.pdf", b"%PDF-1.4 broken")}}),
        }
        for name, (m, path, kw) in probes.items():
            rr = c.request(m, path, **kw)
            txt = rr.text
            leak[name] = {"status": rr.status_code, "leaks_traceback_or_path": any(k in txt for k in ("Traceback", "C:\\\\", "C:\\", "site-packages", 'File "'))}
        sec["error_handling"] = leak

        # body size limits (denial of service)
        t = time.perf_counter()
        try:
            r = c.post("/api/evaluate", content=b'{"requirements": [], "cases": [], "pad": "' + b"x" * (60 * 1024 * 1024) + b'"}', headers={"Content-Type": "application/json"})
            sec["60MB_json_body"] = {"status": r.status_code, "seconds": round(time.perf_counter() - t, 1)}
        except Exception as exc:
            sec["60MB_json_body"] = {"error": str(exc)[:80]}
        sec["upload_25mb_limit_enforced"] = c.post("/api/upload", files={"file": ("big.json", b"[" + b" " * (26 * 1024 * 1024) + b"]")}).status_code == 413

        # arbitrary file write via resume_path
        target = ROOT / "runs" / "probe_resume_write.json"
        target.unlink(missing_ok=True)
        c.post("/api/evaluate", json={**body_eval, "cases": cases108[:3], "resume_path": str(target)})
        sec["resume_path_writes_client_chosen_file"] = target.exists()
        target.unlink(missing_ok=True)

        # secrets in repo and history
        git = lambda *args: subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout
        sec["env_file_tracked_by_git"] = ".env" in git("ls-files").split()
        sec["env_ignored"] = bool(git("check-ignore", ".env").strip())
        hist = git("log", "--all", "-p", "-G", r"sk-or-v1-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}", "--oneline")
        sec["secret_patterns_in_git_history"] = len(hist.strip()) > 0
        # dependency vulnerabilities via OSV (sends only public package names and versions)
        pk = json.loads(subprocess.run([sys.executable, "-m", "pip", "list", "--format", "json"], capture_output=True, text=True).stdout)
        try:
            qs = {"queries": [{"package": {"name": p["name"], "ecosystem": "PyPI"}, "version": p["version"]} for p in pk]}
            rr = httpx.post("https://api.osv.dev/v1/querybatch", json=qs, timeout=60).json()
            vulns = {p["name"] + "==" + p["version"]: [v["id"] for v in res.get("vulns", [])] for p, res in zip(pk, rr["results"]) if res.get("vulns")}
            sec["dependency_vulnerabilities_osv"] = {"packages_checked": len(pk), "vulnerable": vulns}
        except Exception as exc:
            sec["dependency_vulnerabilities_osv"] = {"error": str(exc)[:80]}
    finally:
        srv.terminate()
    out = ROOT / "runs" / "perf_security.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
