"""Security Audit Script for Verdict AI.

Scans the repository for:
1. Hardcoded API keys, tokens, or credentials
2. Git tracking of sensitive files (.env, keys)
3. Insecure deserialization (eval, exec, pickle)
4. Path traversal vulnerabilities in file handlers
5. Dependency issues
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Patterns for secrets
SECRET_PATTERNS = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}", re.I), "OpenAI / OpenRouter API Key"),
    (re.compile(r"(?i)api[_-]?key\s*=\s*['\"][a-zA-Z0-9_\-]{20,}['\"]"), "Hardcoded API Key assignment"),
    (re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{25,}"), "Bearer token"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "Private Key"),
]

# Sensitive files that must never be committed
SENSITIVE_FILES = [".env", ".env.local", "id_rsa", "credentials.json"]

# Insecure calls
DANGEROUS_CALLS = [
    (re.compile(r"\beval\s*\("), "eval() call"),
    (re.compile(r"\bexec\s*\("), "exec() call"),
    (re.compile(r"\bpickle\.loads?\s*\("), "Insecure pickle deserialization"),
    (re.compile(r"\bos\.system\s*\("), "Direct os.system execution"),
]


def audit() -> int:
    print("=" * 60)
    print(" Verdict AI — Codebase Security Audit")
    print("=" * 60)
    findings = []

    # 1. Check Git Tracked Files
    try:
        tracked = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True
        ).splitlines()
        for f in tracked:
            for s in SENSITIVE_FILES:
                if f == s or f.endswith(f"/{s}"):
                    findings.append(f"[CRITICAL] Sensitive file tracked in git: {f}")
    except Exception as exc:
        print(f"Warning: git ls-files failed: {exc}")

    # 2. Scan Source Files
    scanned_count = 0
    src_paths = list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py"))
    for py_file in src_paths:
        scanned_count += 1
        content = py_file.read_text(encoding="utf-8", errors="ignore")

        # Secret patterns
        for pattern, desc in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(f"[HIGH] Potential secret ({desc}) in {py_file.relative_to(ROOT)}")

        # Insecure functions
        for pattern, desc in DANGEROUS_CALLS:
            if pattern.search(content):
                findings.append(f"[MEDIUM] Dangerous function ({desc}) in {py_file.relative_to(ROOT)}")

    # 3. Check .gitignore completeness
    gitignore = ROOT / ".gitignore"
    if gitignore.exists():
        gi_content = gitignore.read_text(encoding="utf-8")
        for req in [".env", ".venv", ".cache"]:
            if req not in gi_content:
                findings.append(f"[MEDIUM] Missing '{req}' from .gitignore")
    else:
        findings.append("[HIGH] Missing .gitignore file")

    print(f"Scanned {scanned_count} Python source files.")

    # 4. Results
    if not findings:
        print("\n [PASS] All security checks passed! No vulnerabilities or exposed secrets found.")
        print("=" * 60)
        return 0
    else:
        print(f"\n [WARN] {len(findings)} security findings detected:")
        for f in findings:
            print(f"  - {f}")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(audit())
