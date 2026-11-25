#!/usr/bin/env python3
"""video/problems.src.json -> video/problems.walk.json for the walkthrough renderer.

Code is never copied by hand: `code_from` / `before_from` name an @learn anchor, and this script cuts
the lines between the markers out of the source file. It refuses to write the output if a segment's
line numbers fall outside the extracted code, or if narration contains code symbols, and it prints
every highlighted line next to its narration so a person can check the pairing.

    python scripts/build_problem_videos.py          # writes video/problems.walk.json
    python scripts/build_problem_videos.py --show q07-oversell   # print one problem's code with line numbers
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "video" / "problems.src.json"
OUT = ROOT / "video" / "problems.walk.json"
SYMBOLS = re.compile(r"[{}\[\]<>=_/\\|`*#$;]|->|==|\(\)")


def cut(path: str, anchor: str) -> str:
    lines = (ROOT / path).read_text().split("\n")
    start = next(i for i, l in enumerate(lines) if re.search(rf"@learn:{re.escape(anchor)}\s*$", l))
    end = next(i for i, l in enumerate(lines) if re.search(rf"@learn:end {re.escape(anchor)}\s*$", l))
    body = lines[start + 1:end]
    indent = min((len(l) - len(l.lstrip()) for l in body if l.strip()), default=0)
    return "\n".join(l[indent:] for l in body)


def check(pid: str, kind: str, code: str, segments: list[dict], problems: list[str], verbose: bool) -> None:
    n = len(code.split("\n"))
    for k, seg in enumerate(segments):
        for ln in seg["lines"]:
            if not 1 <= ln <= n:
                problems.append(f"{pid} {kind} segment {k}: line {ln} outside 1..{n}")
        bad = SYMBOLS.findall(seg["text"])
        if bad:
            problems.append(f"{pid} {kind} segment {k}: code symbols in narration {sorted(set(bad))}")
        if re.search(r"\d", seg["text"]):
            problems.append(f"{pid} {kind} segment {k}: write numbers in Chinese, not digits")
        if verbose:
            shown = " | ".join(code.split("\n")[ln - 1].strip() for ln in seg["lines"] if 1 <= ln <= n)
            print(f"  [{kind} {k:02d}] {seg['lines'] or '-'}  {seg['text'][:38]}…\n           {shown[:150]}")


def main() -> None:
    items = json.loads(SRC.read_text())
    if len(sys.argv) == 3 and sys.argv[1] == "--show":
        it = next(i for i in items if i["id"] == sys.argv[2])
        for key in ("code_from", "before_from"):
            print(f"--- {key}")
            for i, l in enumerate(cut(**it[key]).split("\n"), 1):
                print(f"{i:3} {l}")
        return
    problems, out = [], []
    for it in items:
        code = cut(**it["code_from"])
        before = cut(**it["before_from"]) if it.get("before_from") else ""
        print(f"{it['id']}  ({len(code.splitlines())} lines after, {len(before.splitlines())} before)")
        check(it["id"], "after", code, it["segments"], problems, True)
        check(it["id"], "before", before, it.get("before_segments", []), problems, True)
        n = len(it["segments"]) + len(it.get("before_segments", []))
        if not 6 <= n <= 14:
            problems.append(f"{it['id']}: {n} segments, want 6..14")
        out.append({"id": it["id"], "title": it["title"], "subtitle": it["subtitle"], "lang": it["lang"],
                    "code": code, "segments": it["segments"],
                    "example_code": before, "example_segments": it.get("before_segments", [])})
    if problems:
        print("\nbuild_problem_videos FAILED\n  " + "\n  ".join(problems))
        sys.exit(1)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"\nwrote {OUT.relative_to(ROOT)}: {len(out)} problems")


if __name__ == "__main__":
    main()
