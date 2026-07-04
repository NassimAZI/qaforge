"""
clickup_import.py — QAForge input satellite: fetch a ClickUp task (User Story)
and format it as editable text for the Phase 1 input area.

Design notes
------------
- Streamlit-free, same pattern as testmo_export.py: pure functions + a thin
  HTTP client, unit-testable with pytest.
- INPUT ONLY: this module pre-fills the User Story field. It never triggers
  generation — reviewing/enriching the ticket before analysis is the point
  (ticket quality is the #1 risk factor of the pipeline).
- Auth quirk: the ClickUp API expects the personal token RAW in the
  Authorization header ('pk_…'), WITHOUT a 'Bearer ' prefix.
- Accepted references: raw task id ('868c9q3zv'), task URL
  ('https://app.clickup.com/t/868c9q3zv'), or custom-id URL
  ('https://app.clickup.com/t/{team_id}/PROJ-123' — needs the team id, which
  the URL provides; a bare custom id like 'PROJ-123' is rejected with a hint
  to paste the URL instead).
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

CLICKUP_API = "https://api.clickup.com/api/v2"
CLICKUP_API_V3 = "https://api.clickup.com/api/v3"

# Custom ids look like 'PROJ-123' (uppercase prefix + dash + number);
# native ids are lowercase alphanumeric ('868c9q3zv').
_CUSTOM_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")


def _retry_wait(retry_after: str | None, attempt: int) -> float:
    """Safe Retry-After parsing (numeric seconds or fallback backoff).
    Local copy — satellites stay independently deletable."""
    try:
        return min(float(retry_after), 90.0)
    except (TypeError, ValueError):
        return 10.0 * (attempt + 1)


def extract_ref(url_or_id: str) -> dict:
    """Parse whatever the user pastes — TASK or DOC reference.

    Tasks → {'kind': 'task', 'task_id': …, 'team_id': …|None}
      '868c9q3zv'                                   → native id
      'https://app.clickup.com/t/868c9q3zv'         → native id from URL
      'https://app.clickup.com/t/9012/PROJ-123'     → custom id + team id
    Docs  → {'kind': 'doc', 'workspace_id': …, 'doc_id': …, 'page_id': …|None}
      'https://app.clickup.com/{ws}/docs/{doc_id}'            → whole doc
      'https://app.clickup.com/{ws}/docs/{doc_id}/{page_id}'  → single page
    Rejected with a helpful message:
      'PROJ-123' alone (custom ids need the team id — paste the task URL).
    """
    ref = (url_or_id or "").strip()
    if not ref:
        raise ValueError("Empty reference.")

    if "clickup.com" in ref:
        path = [p for p in urlsplit(ref).path.split("/") if p]
        # Doc URL: /{workspace_id}/docs/{doc_id}[/{page_id}]
        if "docs" in path:
            i = path.index("docs")
            if i >= 1 and len(path) > i + 1:
                return {"kind": "doc", "workspace_id": path[i - 1],
                        "doc_id": path[i + 1],
                        "page_id": path[i + 2] if len(path) > i + 2 else None}
            raise ValueError(f"Unrecognised ClickUp Doc URL: {ref}")
        # Task URL: /t/{task_id}  or  /t/{team_id}/{custom_id}
        if "t" in path:
            after = path[path.index("t") + 1:]
            if len(after) == 1:
                return {"kind": "task", "task_id": after[0], "team_id": None}
            if len(after) >= 2:
                return {"kind": "task", "task_id": after[1], "team_id": after[0]}
        raise ValueError(f"Unrecognised ClickUp URL (expected …/t/<task_id> "
                         f"or …/docs/<doc_id>): {ref}")

    if _CUSTOM_ID_RE.match(ref):
        raise ValueError(
            f"'{ref}' looks like a custom task id — the ClickUp API needs the "
            "team id to resolve it. Paste the full task URL instead."
        )
    return {"kind": "task", "task_id": ref, "team_id": None}


def _fmt_custom_field(field: dict):
    """Return 'Name: value' for simple scalar custom fields, else None.
    Complex types (dropdown indexes, relations, users…) are skipped in v1 —
    better to omit than to inject cryptic ids into the User Story."""
    name = str(field.get("name", "")).strip()
    value = field.get("value")
    if not name or value in (None, "", [], {}):
        return None
    if isinstance(value, bool):
        return f"{name}: {'yes' if value else 'no'}"
    if isinstance(value, (str, int, float)):
        v = str(value).strip()
        return f"{name}: {v}" if v else None
    return None


def task_to_us_text(task: dict) -> str:
    """Format a ClickUp task as a User Story block for the QAForge input area.

    Layout: reference header (id, status, list, url) → description (markdown
    when available) → non-empty simple custom fields (often where acceptance
    criteria live). Plain text, fully editable by the QA before generation.
    """
    tid = task.get("custom_id") or task.get("id", "?")
    lines = [f"[ClickUp {tid}] {task.get('name', '').strip()}"]

    meta = []
    status = (task.get("status") or {}).get("status")
    if status:
        meta.append(f"Status: {status}")
    prio = (task.get("priority") or {}).get("priority") if isinstance(task.get("priority"), dict) else None
    if prio:
        meta.append(f"Priority: {prio}")
    lst = (task.get("list") or {}).get("name")
    if lst:
        meta.append(f"List: {lst}")
    if task.get("url"):
        meta.append(f"URL: {task['url']}")
    if meta:
        lines.append(" · ".join(meta))

    desc = (task.get("markdown_description") or task.get("description") or "").strip()
    lines.append("")
    lines.append(desc if desc else "(no description — enrich before analyzing!)")

    fmts = [_fmt_custom_field(f) for f in (task.get("custom_fields") or [])]
    fmts = [f for f in fmts if f]
    if fmts:
        lines += ["", "Custom fields:"] + [f"- {f}" for f in fmts]

    return "\n".join(lines).strip()




def _flatten_pages(pages) -> list[dict]:
    """Doc pages can nest sub-pages under a 'pages' key — flatten depth-first."""
    flat = []
    for p in pages or []:
        if isinstance(p, dict):
            flat.append(p)
            flat.extend(_flatten_pages(p.get("pages")))
    return flat


def doc_pages_to_us_text(pages, source_url: str = "", max_chars: int = 19000) -> str:
    """Format Doc page(s) as an editable spec block for the QAForge input.
    Page name becomes a heading, markdown content follows. Capped: the input
    area accepts 20k chars — a huge Doc should be trimmed by the QA anyway."""
    flat = _flatten_pages(pages if isinstance(pages, list) else [pages])
    blocks = []
    for p in flat:
        name = str(p.get("name", "")).strip()
        content = str(p.get("content", "") or "").strip()
        if name:
            blocks.append(f"## {name}")
        if content:
            blocks.append(content)
    if not blocks:
        return "(empty Doc — enrich before analyzing!)"
    header = f"[ClickUp Doc] {source_url}".strip()
    text = header + "\n\n" + "\n\n".join(blocks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[… truncated — Doc longer than the input limit]"
    return text


class ClickUpClient:
    """Minimal ClickUp REST client. Token held in memory only — never logged."""

    __test__ = False

    def __init__(self, token: str, timeout: float = 30.0):
        self._token = (token or "").strip()
        self.timeout = timeout
        if not self._token:
            raise ValueError("ClickUp API token is required (ClickUp → Settings → Apps).")

    def _get(self, url: str, params: dict | None = None,
             what: str = "resource", max_retries: int = 3):
        import httpx
        # NOTE: raw token, no 'Bearer ' prefix — ClickUp API quirk.
        headers = {"Authorization": self._token}
        for attempt in range(max_retries):
            r = httpx.get(url, params=params, headers=headers,
                          timeout=self.timeout, follow_redirects=True)
            if r.status_code == 429 and attempt < max_retries - 1:
                time.sleep(_retry_wait(r.headers.get("Retry-After"), attempt))
                continue
            if r.status_code == 401:
                raise Exception("ClickUp API: invalid token (401). Use a personal "
                                "token from ClickUp → Settings → Apps (starts with 'pk_').")
            if r.status_code == 403:
                raise Exception(f"ClickUp API: token lacks access to this {what} (403).")
            if r.status_code == 404:
                raise Exception(f"ClickUp API: {what} not found (404) — check the reference.")
            if r.status_code >= 400:
                raise Exception(f"ClickUp API {r.status_code}: {r.text[:300]}")
            try:
                return r.json()
            except ValueError:
                raise Exception("ClickUp returned non-JSON — unexpected; check the reference.")
        raise Exception("ClickUp API: rate limited (429) — retries exhausted.")

    # -- tasks (API v2) ---------------------------------------------------------
    def get_task(self, task_id: str, team_id: str | None = None) -> dict:
        params = {"include_markdown_description": "true"}
        if team_id:
            params.update({"custom_task_ids": "true", "team_id": team_id})
        return self._get(f"{CLICKUP_API}/task/{task_id}", params,
                         what=f"task '{task_id}'")

    # -- docs (API v3) ----------------------------------------------------------
    def get_doc_page(self, workspace_id: str, doc_id: str, page_id: str) -> dict:
        """Single Doc page with markdown content."""
        url = (f"{CLICKUP_API_V3}/workspaces/{workspace_id}"
               f"/docs/{doc_id}/pages/{page_id}")
        return self._get(url, {"content_format": "text/md"},
                         what=f"Doc page '{page_id}'")

    def get_doc_pages(self, workspace_id: str, doc_id: str) -> list:
        """All pages (and sub-pages) of a Doc with markdown content."""
        url = f"{CLICKUP_API_V3}/workspaces/{workspace_id}/docs/{doc_id}/pages"
        res = self._get(url, {"content_format": "text/md"},
                        what=f"Doc '{doc_id}'")
        return res if isinstance(res, list) else res.get("pages", res.get("result", []))
