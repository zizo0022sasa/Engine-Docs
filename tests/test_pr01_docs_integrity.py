"""PR-01 Acceptance Tests: T20 (Doc JSON blocks), T25 (Epistemic & Contradiction Hygiene), T28 (Governance)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from osrs_ee.security.secret_scan import scan_path

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_NORMATIVE_FILES = [
    "README.md",
    "PROGRESS.md",
    "TASKS.md",
    "docs/RESUME_PROTOCOL.md",
    "docs/PROJECT_CONTEXT_AND_REQUIREMENTS.md",
    "docs/AUDIT_REGISTER.md",
    "docs/DECISIONS.md",
    "docs/ARCHITECTURE_AND_DESIGN.md",
    "docs/DATA_CONTRACTS_AND_STATE.md",
    "docs/SECURITY_AND_RECOVERY.md",
    "docs/ACCEPTANCE_TESTS.md",
    "docs/EXECUTION_AND_MIGRATION_PLAN.md",
    "وثائق_المحركات/00_المرجع_المعماري_الموحد.md",
    "وثائق_المحركات/01_محرك_عامل_المهمة_Task_Worker_Adapter.md",
    "وثائق_المحركات/02_محرك_إدارة_الحسابات_Accounts_Manager.md",
    "وثائق_المحركات/03_محرك_حارس_التليجرام_والسجلات_Telegram_Logs_Guardian.md",
    "وثائق_المحركات/04_محرك_المايسترو_ومشرف_التشغيل_Master_Orchestrator.md",
    "وثائق_المحركات/05_محرك_الذكاء_الاصطناعي_AI_Triage_Solver.md",
    "وثائق_المحركات/مصفوفة_السكربتات_المفتوحة_وخريطة_المراحل.md",
]


def test_t28_all_required_normative_files_exist_and_non_empty() -> None:
    """T28: Verify full consolidated documentation baseline exists."""
    for rel_path in REQUIRED_NORMATIVE_FILES:
        fp = REPO_ROOT / rel_path
        assert fp.is_file(), f"Missing required normative document: {rel_path}"
        content = fp.read_text(encoding="utf-8").strip()
        assert len(content) > 200, f"Document unexpectedly short/empty: {rel_path}"


def test_t20_all_json_code_blocks_in_normative_docs_are_valid_rfc8259() -> None:
    """T20: Every ```json block in docs/ and وثائق_المحركات/ must parse cleanly with json.loads."""
    json_fence = re.compile(r"```json\s*(.*?)```", re.DOTALL)
    total_blocks = 0

    for rel_path in REQUIRED_NORMATIVE_FILES:
        fp = REPO_ROOT / rel_path
        text = fp.read_text(encoding="utf-8")
        blocks = json_fence.findall(text)
        for idx, raw_block in enumerate(blocks, start=1):
            total_blocks += 1
            try:
                parsed = json.loads(raw_block)
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"Invalid JSON in {rel_path} [block #{idx}]: {exc}\n{raw_block}"
                ) from exc
            assert isinstance(parsed, dict), f"Expected JSON object in {rel_path} [block #{idx}]"

    assert total_blocks >= 9, f"Expected at least 9 normative JSON examples, found {total_blocks}"


def test_t25_engine_docs_free_of_superseded_contradictions_and_false_guarantees() -> None:
    """T25: Ensure وثائق_المحركات/ no longer contains plaintext password cards, 100% ban guarantees, or stale rules."""
    forbidden_phrases = [
        "نحصن المزرعة ضده بنسبة 100%",
        "بنسبة دقة 100%",
        "يضمن مستحيلاً توقف",
        "كلمة المرور: Pass",
        '"password": "Password123"',
        "QP < 7",
        "7 نقاط كويست ينتهي قيد التجارة",
        "حد أقصى حسابان لكل بروكسي",
    ]
    engine_docs_dir = REPO_ROOT / "وثائق_المحركات"
    for fp in sorted(engine_docs_dir.glob("*.md")):
        text = fp.read_text(encoding="utf-8")
        for phrase in forbidden_phrases:
            assert phrase not in text, f"Stale/contradictory phrase '{phrase}' still present in {fp.name}"


def test_t28_adr_and_audit_register_cross_references_are_complete() -> None:
    """T28: Verify ADR-001..ADR-012 and AUD-01..AUD-39 are properly registered and secret-free."""
    decisions = (REPO_ROOT / "docs/DECISIONS.md").read_text(encoding="utf-8")
    for i in range(1, 13):
        adr_id = f"ADR-{i:03d}"
        assert adr_id in decisions, f"Missing {adr_id} in docs/DECISIONS.md"

    audit_reg = (REPO_ROOT / "docs/AUDIT_REGISTER.md").read_text(encoding="utf-8")
    for i in range(1, 40):
        aud_id = f"AUD-{i:02d}"
        assert aud_id in audit_reg, f"Missing {aud_id} in docs/AUDIT_REGISTER.md"

    assert scan_path(REPO_ROOT) == []
