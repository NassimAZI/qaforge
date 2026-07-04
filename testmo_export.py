"""
testmo_export.py — QAForge → Testmo integration (CSV + REST API).

Design notes
------------
- This module is 100% Streamlit-free: every function takes explicit arguments,
  so it is unit-testable with pytest and reusable outside the app.
- Two export paths sharing the same source of truth (structured_test_cases):
    1. build_csv_testmo()  → multi-row CSV for Testmo's universal import wizard
       (one row per step; Testmo detects a new case when the Name column changes).
    2. TestmoClient + tc_to_testmo_cases() → direct push via POST
       /api/v1/projects/{id}/cases (bulk, max 100 cases per request).
- CSV rules (per Testmo docs):
    * Name is repeated on EVERY row of a case (it is the case delimiter).
    * Case-level fields (Priority, Tags, Preconditions, …) are filled on the
      FIRST row only — Testmo MERGES text columns across rows, so repeating
      them would duplicate the content.
    * NO formula-injection escaping here: the leading apostrophe would be
      imported as literal text by Testmo. This CSV targets the import wizard,
      not Excel.
- API rules (per Testmo docs):
    * custom fields use the `custom_` prefix; unknown fields → 422, so we only
      send fields discovered via GET /projects/{id}/templates.
    * steps: [{"text1": "<p>action</p>", "text3": "<p>expected</p>"}] — HTML.
    * priority is a dropdown: the API expects the option ID, resolved
      dynamically from the template (never hardcoded).
"""

from __future__ import annotations

import csv
import io
import re
import time
import unicodedata
from html import escape as _html_escape




def _norm(s: str) -> str:
    """Accent-insensitive, case-insensitive normalisation ('Élevée' → 'elevee').
    Needed because QAForge output and Testmo instances may be French."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()


# EN ↔ FR priority label synonyms → canonical value (both sides are normalised)
PRIORITY_ALIASES = {
    "critical": "critical", "critique": "critical",
    "high": "high", "haute": "high", "elevee": "high",
    "medium": "medium", "moyenne": "medium", "normal": "medium", "normale": "medium",
    "low": "low", "basse": "low", "faible": "low",
}


def _canon_priority(label: str):
    n = _norm(label)
    return PRIORITY_ALIASES.get(n, n)


# ══════════════════════════════════════════════════════════════════════════════
# 1. CSV EXPORT (Testmo universal import wizard — multi-row, separate steps)
# ══════════════════════════════════════════════════════════════════════════════

TESTMO_CSV_FIELDS = [
    "Folder", "Name", "Priority", "Type", "Tags",
    "Preconditions", "Step", "Step Expected",
    "Expected Result", "Failure Signature",
]


def _tc_name(tc: dict) -> str:
    """Case name shown in Testmo: 'TC-12 — Title' (Name field max 255 chars)."""
    return f"{tc.get('id', 'TC-?')} — {tc.get('title', '')}".strip()[:255]


def _tc_tags(tc: dict) -> list[str]:
    """Technique + BR-x coverage as Testmo tags → filterable traceability."""
    tags = []
    if tc.get("technique"):
        tags.append(str(tc["technique"]).strip())
    for c in tc.get("covers") or []:
        c = str(c).strip()
        if c and c not in tags:
            tags.append(c)
    return tags


def _flatten_preconditions(tc: dict) -> str:
    pre = tc.get("preconditions", [])
    if isinstance(pre, list):
        return " | ".join(str(p) for p in pre if str(p).strip())
    return str(pre or "")


def _iter_steps(tc: dict):
    """Yield (action, expected) pairs whatever shape the model returned."""
    for s in tc.get("steps") or []:
        if isinstance(s, dict):
            yield str(s.get("action", "")), str(s.get("expected", "") or "")
        else:
            yield str(s), ""


def build_csv_testmo(data: list[dict], folder_name: str = "QAForge") -> str:
    """Multi-row CSV for the Testmo import wizard.

    Import wizard settings to use in Testmo:
      - "A test case can span across multiple rows" → checked
      - Template: "Case (steps)" (or any template with separate steps)
      - Name column = case delimiter · Step / Step Expected → step sub-fields
    """
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(TESTMO_CSV_FIELDS)

    for tc in data or []:
        name = _tc_name(tc)
        steps = list(_iter_steps(tc)) or [("", "")]  # at least one row per case
        for i, (action, s_exp) in enumerate(steps):
            first = i == 0
            w.writerow([
                folder_name if first else "",
                name,                                   # repeated: case delimiter
                str(tc.get("priority", "")) if first else "",
                str(tc.get("type", "")) if first else "",
                ", ".join(_tc_tags(tc)) if first else "",
                _flatten_preconditions(tc) if first else "",
                action,
                s_exp,
                str(tc.get("expected_result", "")) if first else "",
                str(tc.get("failure_signature", "")) if first else "",
            ])
    return out.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# 2. API PAYLOAD BUILDER (POST /projects/{id}/cases)
# ══════════════════════════════════════════════════════════════════════════════

def _p(txt: str) -> str:
    """Testmo step fields are HTML — escape and wrap in <p>."""
    return "<p>" + _html_escape(str(txt)).replace("\n", "<br>") + "</p>"


def field_api_key(field_name: str) -> str:
    """Template field name → API key ('Priority' → 'custom_priority').
    Accents are ASCII-folded ('Préconditions' → 'custom_preconditions')."""
    return "custom_" + re.sub(r"[^a-z0-9]+", "_", _norm(field_name)).strip("_")


def parse_template(template: dict) -> dict:
    """Extract what we need from a GET /templates entry.

    Returns {template_id, steps_key, priority_key, priority_map, text_keys}
      - priority_map: lowercase option label → option id (API wants the ID)
      - text_keys:    lowercase field name → api key, for text/string fields
                      (used to map preconditions if such a field exists)
    """
    info = {
        "template_id": template.get("id"),
        "template_name": template.get("name", ""),
        "steps_key": None,
        "priority_key": None,
        "priority_map": {},
        "text_keys": {},
    }
    for f in template.get("fields") or []:
        ftype, fname = f.get("type"), f.get("name", "")
        key = field_api_key(fname)
        if ftype == 10 and not info["steps_key"]:          # Steps
            info["steps_key"] = key
        elif ftype == 8 and "priorit" in fname.lower():    # Priority dropdown
            info["priority_key"] = key
            # keyed by CANONICAL priority ('Haute' and 'High' → 'high')
            info["priority_map"] = {
                _canon_priority(o.get("value", "")): o.get("id")
                for o in f.get("options") or []
            }
        elif ftype in (1, 2):                              # String / Text
            info["text_keys"][_norm(fname)] = key           # accent-insensitive
    return info


def _find_text_key(text_keys: dict, *needles: str):
    for name, key in text_keys.items():
        if any(n in name for n in needles):
            return key
    return None


def tc_to_testmo_cases(tcs: list[dict], tpl: dict, folder_id: int | None = None) -> tuple[list[dict], list[str]]:
    """Transform QAForge structured test cases into Testmo API case objects.

    `tpl` is the dict returned by parse_template(). Only fields that exist in
    the template are sent (unknown custom fields → 422 on Testmo's side).

    Mapping strategy:
      - preconditions → dedicated text field if the template has one
        (name contains 'precondition'), else a leading pseudo-step.
      - global expected_result → appended to the last step's expected
        (or a closing verification step if there are no steps).
      - failure_signature → dedicated text field if one matches
        ('failure'/'signature'), else appended to the final expected block.

    Returns (cases, notes) — notes describe fallbacks applied, for UI display.
    """
    notes: set[str] = set()
    pre_key = _find_text_key(tpl["text_keys"], "precondition")
    sig_key = _find_text_key(tpl["text_keys"], "failure", "signature")
    cases = []

    for tc in tcs or []:
        case: dict = {"name": _tc_name(tc), "template_id": tpl["template_id"]}
        if folder_id:
            case["folder_id"] = int(folder_id)

        tags = _tc_tags(tc)
        if tags:
            case["tags"] = tags

        # Priority → dropdown option ID (canonical match: EN/FR labels, accents)
        prio = _canon_priority(tc.get("priority", ""))
        pid = tpl["priority_map"].get(prio)
        if tpl["priority_key"] and pid:
            case[tpl["priority_key"]] = pid
        elif str(tc.get("priority", "")).strip() and not pid:
            notes.add(f"Priority '{tc.get('priority')}' not found in template options — project default used.")

        # Preconditions
        pre = tc.get("preconditions") or []
        pre_items = [str(p) for p in (pre if isinstance(pre, list) else [pre]) if str(p).strip()]
        steps_payload = []
        if pre_items:
            if pre_key:
                case[pre_key] = "".join(_p(p) for p in pre_items)
            else:
                steps_payload.append({
                    "text1": "<p><strong>Preconditions</strong></p>" + "".join(_p(p) for p in pre_items)
                })
                notes.add("No 'Preconditions' field in template — added as a leading step.")

        # Steps (per-step expected in text3)
        for action, s_exp in _iter_steps(tc):
            step = {"text1": _p(action)}
            if s_exp:
                step["text3"] = _p(s_exp)
            steps_payload.append(step)

        # Global expected result + failure signature → final expected block
        tail = ""
        if tc.get("expected_result"):
            tail += _p(str(tc["expected_result"]))
        sig = str(tc.get("failure_signature", "") or "")
        if sig:
            if sig_key:
                case[sig_key] = _p(sig)
            else:
                tail += "<p><em>Failure signature:</em> " + _html_escape(sig) + "</p>"
                notes.add("No 'Failure signature' field in template — appended to final expected result.")
        if tail:
            if steps_payload:
                last = steps_payload[-1]
                last["text3"] = (last.get("text3", "") + tail)
            else:
                steps_payload.append({"text1": _p("Verify final result"), "text3": tail})

        if tpl["steps_key"] and steps_payload:
            case[tpl["steps_key"]] = steps_payload
        elif steps_payload and not tpl["steps_key"]:
            notes.add("Selected template has no Steps field — steps were NOT pushed. Pick a 'Case (steps)' template.")

        cases.append(case)

    return cases, sorted(notes)


# ══════════════════════════════════════════════════════════════════════════════
# 3. API CLIENT (httpx — already in requirements.txt)
# ══════════════════════════════════════════════════════════════════════════════



def retry_wait_seconds(retry_after: str | None, attempt: int) -> float:
    """Safe Retry-After parsing: numeric seconds, or fallback backoff.
    HTTP dates (also legal in Retry-After) fall back to the backoff — parsing
    server dates against local clocks is more fragile than a fixed wait."""
    try:
        return min(float(retry_after), 90.0)
    except (TypeError, ValueError):
        return 15.0 * (attempt + 1)


class TestmoClient:
    """Minimal Testmo REST client. Token is held in memory only — never logged.

    Usage:
        client = TestmoClient("https://acme.testmo.net", token)
        templates = client.get_templates(project_id)
        created   = client.push_cases(project_id, cases)
    """

    __test__ = False  # pytest: not a test class despite the 'Test' prefix

    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self.base = base_url.strip().rstrip("/")
        self._token = token.strip()
        self.timeout = timeout
        if not self.base or not self._token:
            raise ValueError("Testmo instance URL and API token are both required.")

    # -- low level ------------------------------------------------------------
    def _call(self, method: str, path: str, payload: dict | None = None,
              max_retries: int = 3) -> dict:
        import httpx
        url = f"{self.base}/api/v1/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._token}",
                   "Content-Type": "application/json"}
        for attempt in range(max_retries):
            r = httpx.request(method, url, json=payload, headers=headers,
                              timeout=self.timeout, follow_redirects=True)
            if r.status_code == 429 and attempt < max_retries - 1:
                time.sleep(retry_wait_seconds(r.headers.get("Retry-After"), attempt))
                continue
            if r.status_code == 401:
                raise Exception("Testmo API: invalid or expired token (401).")
            if r.status_code == 403:
                raise Exception("Testmo API: token lacks permission for this action (403).")
            if r.status_code == 422:
                raise Exception(f"Testmo API validation error (422): {r.text[:400]}")
            if r.status_code >= 400:
                raise Exception(f"Testmo API {r.status_code} on {path}: {r.text[:400]}")
            return r.json() if r.content else {}
        raise Exception("Testmo API: rate limited (429) — retries exhausted.")

    # -- discovery ------------------------------------------------------------
    def get_projects(self) -> list[dict]:
        """First page (100) of projects — enough for a selectbox."""
        return self._call("GET", "projects").get("result", [])

    def get_templates(self, project_id: int) -> list[dict]:
        return self._call("GET", f"projects/{project_id}/templates").get("result", [])

    # -- push -----------------------------------------------------------------
    def push_cases(self, project_id: int, cases: list[dict],
                   chunk_size: int = 100) -> list[dict]:
        """POST cases in chunks of 100 (API hard limit). Returns created cases."""
        created: list[dict] = []
        for i in range(0, len(cases), chunk_size):
            chunk = cases[i:i + chunk_size]
            resp = self._call("POST", f"projects/{project_id}/cases",
                              {"cases": chunk})
            created.extend(resp.get("result", []))
        return created
