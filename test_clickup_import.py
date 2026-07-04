"""Unit tests for clickup_import.py — run with: pytest test_clickup_import.py -v
Pure functions only (reference parsing, US formatting, client guards)."""

import pytest

from clickup_import import (
    extract_ref,
    doc_pages_to_us_text,
    task_to_us_text,
    ClickUpClient,
    _retry_wait,
)

TASK = {
    "id": "868c9q3zv",
    "custom_id": "TRESO-142",
    "name": "Dépôt de facture Factur-X par une PA",
    "status": {"status": "in progress"},
    "priority": {"priority": "high"},
    "list": {"name": "Sprint 12"},
    "url": "https://app.clickup.com/t/868c9q3zv",
    "markdown_description": "En tant que PA, je veux déposer une facture...\n\n**AC:**\n- Statut CDAR = Déposée",
    "description": "plain fallback",
    "custom_fields": [
        {"name": "Critères d'acceptation", "value": "Cycle conforme XP Z12-012"},
        {"name": "Estimation", "value": 5},
        {"name": "Bloquant", "value": True},
        {"name": "Vide", "value": ""},
        {"name": "Relation", "value": [{"id": "x"}]},   # complex → skipped
        {"name": "", "value": "orphan"},                 # no name → skipped
    ],
}


class TestExtractTaskRef:
    def test_raw_native_id(self):
        assert extract_ref("868c9q3zv") == {"kind": "task", "task_id": "868c9q3zv", "team_id": None}

    def test_simple_task_url(self):
        ref = extract_ref("https://app.clickup.com/t/868c9q3zv")
        assert ref == {"kind": "task", "task_id": "868c9q3zv", "team_id": None}

    def test_custom_id_url_with_team(self):
        ref = extract_ref("https://app.clickup.com/t/9012/TRESO-142")
        assert ref == {"kind": "task", "task_id": "TRESO-142", "team_id": "9012"}

    def test_bare_custom_id_rejected_with_hint(self):
        with pytest.raises(ValueError, match="task URL"):
            extract_ref("TRESO-142")

    def test_garbage_url_and_empty(self):
        with pytest.raises(ValueError):
            extract_ref("https://app.clickup.com/dashboard")
        with pytest.raises(ValueError):
            extract_ref("   ")

    def test_whitespace_tolerated(self):
        assert extract_ref("  868c9q3zv  ")["task_id"] == "868c9q3zv"


class TestTaskToUsText:
    def test_full_formatting(self):
        txt = task_to_us_text(TASK)
        assert txt.startswith("[ClickUp TRESO-142] Dépôt de facture Factur-X par une PA")
        assert "Status: in progress" in txt and "Priority: high" in txt
        assert "List: Sprint 12" in txt
        assert "En tant que PA" in txt                      # markdown preferred
        assert "plain fallback" not in txt
        assert "- Critères d'acceptation: Cycle conforme XP Z12-012" in txt
        assert "- Estimation: 5" in txt
        assert "- Bloquant: yes" in txt

    def test_complex_and_empty_customs_skipped(self):
        txt = task_to_us_text(TASK)
        assert "Relation" not in txt and "Vide" not in txt and "orphan" not in txt

    def test_missing_description_flagged(self):
        txt = task_to_us_text({"id": "x", "name": "Bare task"})
        assert "enrich before analyzing" in txt

    def test_native_id_fallback_and_minimal_task(self):
        txt = task_to_us_text({"id": "abc123", "name": "T"})
        assert txt.startswith("[ClickUp abc123] T")


class TestClientGuards:
    def test_token_required(self):
        with pytest.raises(ValueError):
            ClickUpClient("   ")

    def test_retry_wait(self):
        assert _retry_wait("5", 0) == 5.0
        assert _retry_wait("Wed, 21 Oct 2026 07:28:00 GMT", 1) == 20.0
        assert _retry_wait(None, 0) == 10.0


class TestDocRefs:
    """The production error: a Doc URL was pasted, not a task URL."""

    def test_doc_url_with_page(self):
        ref = extract_ref("https://app.clickup.com/90121871168/docs/2kxux7u0-512/2kxux7u0-32")
        assert ref == {"kind": "doc", "workspace_id": "90121871168",
                       "doc_id": "2kxux7u0-512", "page_id": "2kxux7u0-32"}

    def test_doc_url_without_page(self):
        ref = extract_ref("https://app.clickup.com/90121871168/docs/2kxux7u0-512")
        assert ref["kind"] == "doc" and ref["page_id"] is None

    def test_doc_url_malformed(self):
        with pytest.raises(ValueError):
            extract_ref("https://app.clickup.com/docs")


class TestDocPagesToUsText:
    def test_single_page(self):
        txt = doc_pages_to_us_text({"name": "Spec dépôt facture", "content": "## AC\n- CDAR = Déposée"},
                                   source_url="https://app.clickup.com/x/docs/y/z")
        assert txt.startswith("[ClickUp Doc] https://app.clickup.com/x/docs/y/z")
        assert "## Spec dépôt facture" in txt and "CDAR = Déposée" in txt

    def test_nested_subpages_flattened(self):
        pages = [{"name": "Parent", "content": "intro",
                  "pages": [{"name": "Child", "content": "détail"}]}]
        txt = doc_pages_to_us_text(pages)
        assert "## Parent" in txt and "## Child" in txt and txt.index("Parent") < txt.index("Child")

    def test_empty_doc_flagged(self):
        assert "empty Doc" in doc_pages_to_us_text([])

    def test_truncation(self):
        txt = doc_pages_to_us_text({"name": "Big", "content": "x" * 30000})
        assert len(txt) < 20000 and "truncated" in txt
