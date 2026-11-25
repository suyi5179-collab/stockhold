"""Render docs/interview.md and docs/problems.md for the learn page: one collapsible item per question.

`anchor:<id>` links in the markdown become GitHub links pinned to the current commit, built from the
generated anchors file and REPO_URL - the docs never spell out the repository URL themselves.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import markdown

from .config import git_sha, settings

ROOT = Path(__file__).resolve().parent.parent
ANCHORS = Path(__file__).resolve().parent / "static" / "generated" / "anchors.json"


def anchors() -> dict:
    return json.loads(ANCHORS.read_text()) if ANCHORS.exists() else {}


def source_url(anchor_id: str) -> str | None:
    a = anchors().get(anchor_id)
    if not a:
        return None
    return f"{settings().repo_url}/blob/{git_sha()}/{a['path']}#L{a['start']}-L{a['end']}"


def _html(md_text: str) -> str:
    def link(m):
        label, aid = m.group(1), m.group(2)
        url = source_url(aid)
        return f'<a class="src-link" data-anchor="{aid}" href="{url}" target="_blank" rel="noopener">{label}</a>' if url else label
    md_text = re.sub(r"\[([^\]]+)\]\(anchor:([a-z0-9-]+)\)", link, md_text)

    def named(m):   # "锚点 `a`、`b`" -> each name becomes a pinned source link
        return "锚点" + re.sub(r"`([a-z0-9-]+)`", lambda k: (
            f'<a class="src-link" data-anchor="{k.group(1)}" href="{source_url(k.group(1))}" target="_blank" '
            f'rel="noopener"><code>{k.group(1)}</code></a>' if source_url(k.group(1)) else k.group(0)), m.group(1))
    md_text = re.sub(r"锚点((?:\s*[、和，,]?\s*`[a-z0-9-]+`)+)", named, md_text)
    # Relative links inside docs/ point at the repository on GitHub, at this commit.
    md_text = re.sub(r"\]\((\.\./|\./)?([\w./-]+\.(?:md|py|mjs|sql|json|yml|sh))(#[\w-]+)?\)",
                     lambda m: f"]({settings().repo_url}/blob/{git_sha()}/{_resolve(m.group(1), m.group(2))}{m.group(3) or ''})", md_text)
    return markdown.markdown(md_text, extensions=["fenced_code", "tables"])


def _resolve(prefix: str | None, path: str) -> str:
    if path.startswith(("stockhold/", "tests/", "scripts/", "sql/", "infra/", "lambdas/", "video/", "e2e/", "deploy/", "docs/")):
        return path
    return ("" if prefix == "../" else "docs/") + path


@lru_cache(maxsize=8)
def _sections(name: str, mtime: float) -> dict:
    text = (ROOT / "docs" / name).read_text()
    intro, groups, cur_group, cur = [], [], None, None
    for line in text.split("\n"):
        if line.startswith("## "):
            cur_group = {"title": line[3:].strip(), "items": []}
            groups.append(cur_group)
            cur = None
        elif line.startswith("### ") and cur_group is not None:
            cur = {"title": line[4:].strip(), "body": []}
            cur_group["items"].append(cur)
        elif cur is not None:
            cur["body"].append(line)
        elif cur_group is None and not line.startswith("# "):
            intro.append(line)
    for g in groups:
        for it in g["items"]:
            it["html"] = _html("\n".join(it.pop("body")))
    return {"intro": _html("\n".join(intro)), "groups": groups}


def interview() -> dict:
    p = ROOT / "docs" / "interview.md"
    return _sections("interview.md", p.stat().st_mtime)


@lru_cache(maxsize=4)
def _problems(mtime: float) -> dict:
    text = (ROOT / "docs" / "problems.md").read_text()
    head, items, cur = [], [], None
    for line in text.split("\n"):
        m = re.match(r"^## (q\d\d)[ ](.+)$", line)
        if m:
            cur = {"num": m.group(1), "title": m.group(2).strip(), "body": []}
            items.append(cur)
        elif cur is not None:
            cur["body"].append(line)
        elif not line.startswith("# "):
            head.append(line)
    src = json.loads((ROOT / "video" / "problems.src.json").read_text()) if (ROOT / "video" / "problems.src.json").exists() else []
    ids = {p["id"].split("-")[0]: p for p in src}
    for it in items:
        body = "\n".join(it.pop("body"))
        it["anchors"] = sorted(set(re.findall(r"anchor:([a-z0-9-]+)", body)) | set(re.findall(r"`([a-z0-9-]+)`", " ".join(
            re.findall(r"锚点[^）)\n]*", body)))))
        it["html"] = _html(body)
        p = ids.get(it["num"])
        it["id"] = p["id"] if p else it["num"]
    return {"intro": _html("\n".join(head)), "items": items}


def problems() -> dict:
    return _problems((ROOT / "docs" / "problems.md").stat().st_mtime)
