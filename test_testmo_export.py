"""Unit tests for testmo_export.py — run with: pytest test_testmo_export.py -v

Pure functions only: no network, no Streamlit. The TestmoClient HTTP layer is
not tested here (would need httpx mocking); payload builders and the CSV
builder — where the real mapping logic lives — are covered, including the
adversarial shapes the LLM actually produces (string steps, missing fields,
unknown priorities).
"""

import csv
import io

import pytest

from testmo_export import (
    build_csv_testmo,
    field_api_key,
    parse_template,
    tc_to_testmo_cases,
    TestmoClient,
    TESTMO_CSV_FIELDS,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

TC_FULL = {
    "id": "TC-1",
    "title": "Login with valid credentials",
    "technique": "EP",
    "type": "Functional",
    "priority": "High",
    "covers": ["BR-1", "BR-3"],
    "preconditions": ["User account exists", "User is logged out"],
    "steps": [
        {"step_number": 1, "action": "Navigate to login page",
         "expected": "Login page is displayed"},
        {"step_number": 2, "action": "Enter valid credentials & click <Login>"},
    ],
    "expected_result": "User lands on dashboard",
    "failure_signature": "Redirect loop on /login",
}

TC_STRING_STEPS = {  # model sometimes returns bare-string steps
    "id": "TC-2", "title": "Reset password", "priority": "Weird-Prio",
    "steps": ["Open reset page", "Submit email"],
    "expected_result": "Email sent",
}

TC_NO_STEPS = {"id": "TC-3", "title": "Empty case", "expected_result": "Something"}

TEMPLATE = {
    "id": 2,
    "name": "Steps Template",
    "fields": [
        {"id": 1, "name": "Priority", "type": 8, "options": [
            {"id": 1, "value": "Critical"}, {"id": 2, "value": "High"},
            {"id": 3, "value": "Medium"}, {"id": 4, "value": "Low"},
        ]},
        {"id": 2, "name": "Steps", "type": 10},
        {"id": 3, "name": "Preconditions", "type": 2},
    ],
}

TEMPLATE_BARE = {"id": 5, "name": "Bare", "fields": [
    {"id": 1, "name": "Steps", "type": 10},
]}


def _rows(csv_text):
    return list(csv.reader(io.StringIO(csv_text)))


# ── CSV builder ───────────────────────────────────────────────────────────────

class TestCsvTestmo:
    def test_header(self):
        rows = _rows(build_csv_testmo([TC_FULL]))
        assert rows[0] == TESTMO_CSV_FIELDS

    def test_one_row_per_step_name_repeated(self):
        rows = _rows(build_csv_testmo([TC_FULL]))
        body = rows[1:]
        assert len(body) == 2  # 2 steps → 2 rows
        names = {r[1] for r in body}
        assert names == {"TC-1 — Login with valid credentials"}  # case delimiter

    def test_case_fields_on_first_row_only(self):
        rows = _rows(build_csv_testmo([TC_FULL]))
        first, second = rows[1], rows[2]
        prio_idx = TESTMO_CSV_FIELDS.index("Priority")
        tags_idx = TESTMO_CSV_FIELDS.index("Tags")
        assert first[prio_idx] == "High" and second[prio_idx] == ""
        assert "EP" in first[tags_idx] and "BR-1" in first[tags_idx]
        assert second[tags_idx] == ""  # Testmo merges text columns → must be blank

    def test_string_steps_and_no_steps(self):
        rows = _rows(build_csv_testmo([TC_STRING_STEPS, TC_NO_STEPS]))
        body = rows[1:]
        assert len(body) == 3  # 2 string steps + 1 empty row for the stepless case
        assert body[0][TESTMO_CSV_FIELDS.index("Step")] == "Open reset page"

    def test_no_formula_escaping(self):
        tc = dict(TC_FULL, steps=[{"action": "=SUM(A1)", "expected": ""}])
        rows = _rows(build_csv_testmo([tc]))
        # Testmo would import a leading apostrophe literally — must NOT be added
        assert rows[1][TESTMO_CSV_FIELDS.index("Step")] == "=SUM(A1)"

    def test_empty_input(self):
        assert _rows(build_csv_testmo([])) == [TESTMO_CSV_FIELDS]


# ── Template parsing ──────────────────────────────────────────────────────────

class TestParseTemplate:
    def test_discovers_keys_and_priority_map(self):
        tpl = parse_template(TEMPLATE)
        assert tpl["steps_key"] == "custom_steps"
        assert tpl["priority_key"] == "custom_priority"
        assert tpl["priority_map"]["high"] == 2
        assert tpl["text_keys"]["preconditions"] == "custom_preconditions"

    def test_field_api_key_slugging(self):
        assert field_api_key("Failure Signature!") == "custom_failure_signature"


# ── API payload builder ───────────────────────────────────────────────────────

class TestPayloadBuilder:
    def test_full_mapping(self):
        tpl = parse_template(TEMPLATE)
        cases, notes = tc_to_testmo_cases([TC_FULL], tpl, folder_id=10)
        c = cases[0]
        assert c["name"].startswith("TC-1 — ")
        assert c["folder_id"] == 10
        assert c["custom_priority"] == 2                      # 'High' → option id
        assert c["tags"] == ["EP", "BR-1", "BR-3"]
        assert "<p>User account exists</p>" in c["custom_preconditions"]
        steps = c["custom_steps"]
        assert len(steps) == 2                                # no pseudo-step: field exists
        assert steps[0]["text1"] == "<p>Navigate to login page</p>"
        assert steps[0]["text3"] == "<p>Login page is displayed</p>"
        # global expected + failure signature appended to LAST step
        assert "User lands on dashboard" in steps[1]["text3"]
        assert "Failure signature" in steps[1]["text3"]

    def test_html_escaping(self):
        tpl = parse_template(TEMPLATE)
        cases, _ = tc_to_testmo_cases([TC_FULL], tpl)
        assert "&lt;Login&gt;" in cases[0]["custom_steps"][1]["text1"]

    def test_bare_template_fallbacks(self):
        tpl = parse_template(TEMPLATE_BARE)
        cases, notes = tc_to_testmo_cases([TC_FULL], tpl)
        c = cases[0]
        assert "custom_priority" not in c                     # no priority field
        steps = c["custom_steps"]
        assert "Preconditions" in steps[0]["text1"]           # pseudo-step fallback
        assert len(steps) == 3
        assert any("Preconditions" in n for n in notes)
        assert any("Priority" in n for n in notes)            # unknown option flagged

    def test_unknown_priority_omitted(self):
        tpl = parse_template(TEMPLATE)
        cases, notes = tc_to_testmo_cases([TC_STRING_STEPS], tpl)
        assert "custom_priority" not in cases[0]
        assert any("Weird-Prio" in n for n in notes)

    def test_stepless_case_gets_closing_step(self):
        tpl = parse_template(TEMPLATE)
        cases, _ = tc_to_testmo_cases([TC_NO_STEPS], tpl)
        steps = cases[0]["custom_steps"]
        assert len(steps) == 1 and "Something" in steps[0]["text3"]


# ── Client guards (no network) ────────────────────────────────────────────────

class TestClientGuards:
    def test_requires_url_and_token(self):
        with pytest.raises(ValueError):
            TestmoClient("", "tok")
        with pytest.raises(ValueError):
            TestmoClient("https://x.testmo.net", "  ")

    def test_base_url_normalised(self):
        c = TestmoClient("https://acme.testmo.net/", "tok")
        assert c.base == "https://acme.testmo.net"


# ── Review round 2: fixes coverage ───────────────────────────────────────────

from testmo_export import retry_wait_seconds, _canon_priority

FRENCH_TEMPLATE = {"id": 7, "name": "Cas de test (étapes)", "fields": [
    {"id": 1, "name": "Priorité", "type": 8, "options": [
        {"id": 11, "value": "Critique"}, {"id": 12, "value": "Élevée"},
        {"id": 13, "value": "Moyenne"}, {"id": 14, "value": "Basse"},
    ]},
    {"id": 2, "name": "Étapes", "type": 10},
    {"id": 3, "name": "Préconditions", "type": 2},
]}


class TestFrenchInstance:
    """QAForge output in EN pushed to a FR Testmo (and vice versa) must map."""

    def test_en_priority_to_fr_options(self):
        tpl = parse_template(FRENCH_TEMPLATE)
        cases, notes = tc_to_testmo_cases([TC_FULL], tpl)   # priority: 'High'
        assert cases[0]["custom_priorite"] == 12            # → 'Élevée'
        assert not any("Priority" in n for n in notes)

    def test_fr_priority_to_fr_options_with_accents(self):
        tpl = parse_template(FRENCH_TEMPLATE)
        tc = dict(TC_FULL, priority="élevée")
        cases, _ = tc_to_testmo_cases([tc], tpl)
        assert cases[0]["custom_priorite"] == 12

    def test_accented_preconditions_field_discovered(self):
        tpl = parse_template(FRENCH_TEMPLATE)
        assert tpl["text_keys"].get("preconditions") == "custom_preconditions"
        cases, notes = tc_to_testmo_cases([TC_FULL], tpl)
        assert "custom_preconditions" in cases[0]
        assert not any("leading step" in n for n in notes)  # no pseudo-step fallback

    def test_field_api_key_ascii_folds(self):
        assert field_api_key("Préconditions") == "custom_preconditions"
        assert field_api_key("Étapes") == "custom_etapes"

    def test_canon_priority(self):
        assert _canon_priority("Haute") == _canon_priority("HIGH") == "high"
        assert _canon_priority("Critique") == "critical"


class TestRetryWait:
    def test_numeric_seconds(self):
        assert retry_wait_seconds("7.5", 0) == 7.5

    def test_capped_at_90(self):
        assert retry_wait_seconds("600", 0) == 90.0

    def test_http_date_falls_back_to_backoff(self):
        assert retry_wait_seconds("Wed, 21 Oct 2026 07:28:00 GMT", 1) == 30.0

    def test_missing_header(self):
        assert retry_wait_seconds(None, 0) == 15.0


class TestNameTruncation:
    def test_name_capped_255(self):
        tc = dict(TC_FULL, title="x" * 400)
        rows = _rows(build_csv_testmo([tc]))
        assert len(rows[1][1]) <= 255
        tpl = parse_template(TEMPLATE)
        cases, _ = tc_to_testmo_cases([tc], tpl)
        assert len(cases[0]["name"]) <= 255
