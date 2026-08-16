import pytest
from mcp import Client
from mcp_server.server import mcp


@pytest.mark.anyio
async def test_workflow_prompts_contract():
    async with Client(mcp) as client:
        # Prompt 1: readiness_check
        res_readiness = await client.get_prompt("readiness_check", {})
        assert res_readiness.messages is not None
        assert "readiness check" in res_readiness.messages[0].content.text.lower()

        # Prompt 2: weekly_review
        res_review = await client.get_prompt("weekly_review", {})
        assert res_review.messages is not None
        assert "weekly review" in res_review.messages[0].content.text.lower()

        # Prompt 3: build_training_plan
        res_plan = await client.get_prompt(
            "build_training_plan",
            {"goal": "Sub-4 Marathon", "target_date": "2026-10-15", "current_weekly_volume": "35.0"},
        )
        assert res_plan.messages is not None
        prompt_text = res_plan.messages[0].content.text
        assert "Sub-4 Marathon" in prompt_text
        assert "2026-10-15" in prompt_text
        assert "35.0" in prompt_text
