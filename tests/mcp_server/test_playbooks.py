from pathlib import Path


def test_coaching_playbooks_exist_and_contain_rules():
    root = Path(__file__).resolve().parents[2]
    claude_skill = root / ".claude" / "skills" / "athlytics-coach" / "SKILL.md"
    gemini_doc = root / "docs" / "coach" / "gemini-system-instructions.md"
    setup_doc = root / "docs" / "coach" / "client-setup.md"

    assert claude_skill.exists()
    assert gemini_doc.exists()
    assert setup_doc.exists()

    claude_content = claude_skill.read_text()
    assert "athlytics-coach" in claude_content
    assert "10% Rule" in claude_content
    assert "Recovery-Gated" in claude_content
    assert "save_training_plan" in claude_content

    gemini_content = gemini_doc.read_text()
    assert "Athlytics AI Coach" in gemini_content
    assert "Deload" in gemini_content
