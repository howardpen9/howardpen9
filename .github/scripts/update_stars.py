#!/usr/bin/env python3
"""Refresh GitHub star counts embedded in README.md.

Looks for markers:

  <!--stars:owner/repo-->123<!--/stars-->
  <!--stars:owner/repo format=k-->12.3k<!--/stars-->
  <!--stars-sum:group_id-->456+<!--/stars-sum-->

Sum groups are defined in .github/stars.yml.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
CONFIG = ROOT / ".github" / "stars.yml"

STAR_RE = re.compile(
    r"<!--stars:([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:\s+format=(k|\+))?-->(.*?)<!--/stars-->",
    re.DOTALL,
)
SUM_RE = re.compile(
    r"<!--stars-sum:([A-Za-z0-9_.-]+)(?:\s+format=(k|\+))?-->(.*?)<!--/stars-sum-->",
    re.DOTALL,
)


def load_sums() -> dict[str, dict]:
    if not CONFIG.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        # Minimal YAML subset for our config (no PyYAML in default GH runner env
        # beyond what's installed). Prefer stdlib-only parse of our simple file.
        return _parse_simple_sums(CONFIG.read_text(encoding="utf-8"))

    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    return data.get("sums") or {}


def _parse_simple_sums(text: str) -> dict[str, dict]:
    """Parse the tiny stars.yml without PyYAML."""
    sums: dict[str, dict] = {}
    current: str | None = None
    in_repos = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("sums:"):
            continue
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if m:
            current = m.group(1)
            sums[current] = {"repos": [], "format": None}
            in_repos = False
            continue
        if current is None:
            continue
        if re.match(r"^    repos:\s*$", line):
            in_repos = True
            continue
        m = re.match(r'^    format:\s*["\']?([^"\']+)["\']?\s*$', line)
        if m:
            sums[current]["format"] = m.group(1)
            in_repos = False
            continue
        m = re.match(r'^      -\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*$', line)
        if m and in_repos:
            sums[current]["repos"].append(m.group(1))
    return sums


def format_stars(n: int, style: str | None) -> str:
    if style == "k":
        if n >= 1000:
            value = n / 1000
            # One decimal, strip trailing .0
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text}k"
        return str(n)
    if style == "+":
        return f"{n}+"
    return str(n)


class GitHub:
    def __init__(self, token: str | None) -> None:
        self.token = token
        self.cache: dict[str, int] = {}

    def stars(self, full_name: str) -> int:
        if full_name in self.cache:
            return self.cache[full_name]
        url = f"https://api.github.com/repos/{full_name}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "howardpen9-readme-stars",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {e.code} for {full_name}: {body}") from e
        count = int(data["stargazers_count"])
        self.cache[full_name] = count
        return count


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    gh = GitHub(token)
    text = README.read_text(encoding="utf-8")
    original = text
    sums = load_sums()

    # Collect every repo referenced so we can fail fast with a clear list.
    repos: set[str] = set(m.group(1) for m in STAR_RE.finditer(text))
    for group in sums.values():
        repos.update(group.get("repos") or [])

    if not repos and not SUM_RE.search(text):
        print("No star markers found in README.md", file=sys.stderr)
        return 1

    print(f"Fetching stars for {len(repos)} repos…")
    for full_name in sorted(repos):
        n = gh.stars(full_name)
        print(f"  {full_name}: {n}")

    def repl_star(m: re.Match[str]) -> str:
        full_name, style, _old = m.group(1), m.group(2), m.group(3)
        count = gh.stars(full_name)
        return f"<!--stars:{full_name}{f' format={style}' if style else ''}-->{format_stars(count, style)}<!--/stars-->"

    def repl_sum(m: re.Match[str]) -> str:
        group_id, style, _old = m.group(1), m.group(2), m.group(3)
        if group_id not in sums:
            raise RuntimeError(
                f"Unknown stars-sum group '{group_id}'. "
                f"Define it in .github/stars.yml (known: {sorted(sums)})"
            )
        group = sums[group_id]
        total = sum(gh.stars(r) for r in group["repos"])
        fmt = style or group.get("format") or None
        # YAML may store format as "+" already
        if fmt == '"+"' or fmt == "'+'":
            fmt = "+"
        return f"<!--stars-sum:{group_id}{f' format={style}' if style else ''}-->{format_stars(total, fmt)}<!--/stars-sum-->"

    text = STAR_RE.sub(repl_star, text)
    text = SUM_RE.sub(repl_sum, text)

    if text == original:
        print("README.md already up to date.")
        return 0

    README.write_text(text, encoding="utf-8")
    print("README.md updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
