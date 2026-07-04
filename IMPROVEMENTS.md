# QAForge — Improvement Ideas

All ideas collected across sessions. Each item notes why it's useful and roughly how hard it is.

---

## UX / Navigation

| Idea | Why | Effort |
|------|-----|--------|
| **Back navigation between phases** | User can't correct their user story after seeing the plan without starting a new session | Medium |
| **Phase 2 inline edit of scenario titles** | Currently requires typing in chat — direct edit would be faster | Medium |
| **Keyboard shortcut to submit (Ctrl+Enter)** | Power users expect it on textarea inputs | Low |
| **Collapse all / expand all TC cards** | Useful when reviewing 15+ TCs at once | Low |
| **Filter TC cards by priority / technique (interactive)** | Filter bar is currently display-only — clicking should actually filter | Medium |
| **Drag-and-drop reordering of scenarios in Phase 2** | Priority ordering by drag is faster than typing chat commands | High |
| **Session persistence across page reloads** | `localStorage` is disabled in Streamlit Cloud iframes — lose everything on refresh | High (needs backend or URL state) |

---

## Input / Import

| Idea | Why | Effort |
|------|-----|--------|
| **Jira import** | Same pattern as ClickUp — fetch a Jira issue by URL or key and pre-fill the user story field | Medium |
| **Notion page import** | Fetch a Notion page as spec input (Notion API v2) | Medium |
| **GitHub issue import** | Fetch a GitHub issue by URL — useful for open-source projects | Low |
| **URL scraping** | Paste a Confluence/Notion/Linear URL and extract the text automatically | Medium |
| **ClickUp Folder ID discovery** | Currently requires copy-pasting a numeric folder ID from Testmo — could be discovered via API dropdown | Medium |
| **ClickUp token persistence** | Session-only today — allow saving in `st.secrets` / `.env` for local runs with a contextual tip | Low |
| **Template user stories** | A dropdown of pre-filled examples (login, checkout, search…) to help new users understand the expected input format | Low |

---

## Generation

| Idea | Why | Effort |
|------|-----|--------|
| **Configurable batch size** | Advanced users on paid API tiers could increase batch size above 6 to speed up Phase 3 | Low |
| **Pre-generation review in Phase 2** | ✅ Already done — AI checks for uncovered BRs, priority gaps, duplicates before generating | Done |
| **Streaming output for Phase 1** | Display questions as they stream instead of waiting for the full JSON | High (Streamlit streaming + partial JSON parsing) |
| **Alternative scenario suggestions** | After Phase 2, offer 3 alternative angles the user might not have considered | Medium |
| **Scenario deduplication assistant** | AI proposes which overlapping scenarios to merge, user approves | Medium |
| **Multi-session comparison** | Compare two test suites generated from different user stories for the same feature | High |

---

## Export / Integrations

| Idea | Why | Effort |
|------|-----|--------|
| **Xray (Jira plugin) export** | Most widely used test management tool in enterprise Jira environments | Medium |
| **TestRail export** | CSV format + API push (same pattern as Testmo) | Medium |
| **Squash TM export** | Common in French enterprises | Medium |
| **Azure DevOps Test Plans export** | Required for Microsoft-stack teams | Medium |
| **Allure TestOps export** | Growing adoption in CI-first teams | Medium |
| **Google Sheets direct export** | One-click push via Google Sheets API — no CSV download/import | High |
| **Confluence page publish** | Push the test suite as a formatted Confluence page | High |
| **qa-update Claude skill** | A `/qa-update` skill to apply targeted modifications to an existing test suite without regenerating everything | Low |

---

## Quality / Reliability

| Idea | Why | Effort |
|------|-----|--------|
| **Retry UI for rate-limited batches** | Free-tier 429s can fail a whole batch — expose a per-batch retry button | Medium |
| **Token budget display** | Show estimated input tokens before each phase transition so users can anticipate rate limits | Low |
| **Prompt versioning** | Track which prompt version generated a given test suite for reproducibility | Medium |
| **Automated end-to-end tests** | Playwright test that runs a full 3-phase cycle with a mock LLM | High |
| **Prompt A/B mode** | Let advanced users switch between two prompt variants and compare outputs | High |

---

## Design / Accessibility

| Idea | Why | Effort |
|------|-----|--------|
| **Light mode toggle** | Some users prefer light mode — currently forced dark | Medium |
| **Font size control** | Accessibility — JetBrains Mono at 11px can be small on low-DPI screens | Low |
| **Keyboard-navigable TC cards** | Cards currently require mouse click — add Enter/Space support | Low |
| **ARIA labels on icon-only buttons** | Yes/No buttons in Phase 1 and ✅/❌ in Phase 2 are not screen-reader friendly | Low |
| **Mobile layout** | Phase 2's 4-column row collapses badly below 900px | Medium |

---

## Claude Code Skills

| Idea | Why | Effort |
|------|-----|--------|
| **`/qa-update` skill** | Apply targeted modifications (priority, add/remove) to an existing test suite in any format | Low |
| **`/qa-export` skill** | Format an existing test suite for a specific tool (Jira Xray, TestRail, Squash) | Low |
| **`/qa-estimate` skill** | From a test suite, estimate execution time, automation ROI, and sprint capacity | Low |
| **`/qa-coverage` skill** | Given a user story + existing TCs, identify coverage gaps without regenerating | Low |
| **`/qa-translate` skill** | Translate a test suite from one language to another, preserving structure | Low |

---

## Infrastructure

| Idea | Why | Effort |
|------|-----|--------|
| **Docker image** | One-command local deploy for teams who can't use Streamlit Cloud | Low |
| **Multi-user mode** | Each user gets an isolated session — today all users on a shared deploy see the same session state | High |
| **Usage analytics** | Track which providers/models are used, where users drop off, average TC count | High |
| **LLM cost estimator** | Show estimated API cost per session based on token counts and provider pricing | Medium |
