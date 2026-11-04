"""Keep the companion skill consistent with the server it documents."""

import re
from pathlib import Path

import yaml

SKILL = Path(__file__).parent.parent / "skills" / "private-document-review" / "SKILL.md"


def _parts() -> tuple[dict, str]:
    text = SKILL.read_text(encoding="utf-8")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_frontmatter_is_valid():
    fm, body = _parts()
    assert fm["name"] == SKILL.parent.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fm["name"])
    desc = fm["description"]
    assert 20 < len(desc) <= 1024 and "<" not in desc and ">" not in desc
    assert body.strip()


async def test_skill_covers_every_tool_and_no_stale_ones(client):
    _, body = _parts()
    tools = {t.name for t in await client.list_tools()}
    mentioned = set(re.findall(r"`([a-z_]+)`", body))
    missing = tools - mentioned
    assert not missing, f"tools not covered by the skill: {missing}"
    # Names the server used to have, or plausible typos, must not appear.
    stale = {"document_stats", "summarise", "answer", "references"} & mentioned
    assert not stale, f"stale tool names in the skill: {stale}"


async def test_parameters_named_in_skill_exist(client):
    _, body = _parts()
    tools = {t.name: t for t in await client.list_tools()}
    for param in ("char_range", "min_score", "max_score", "rubric", "instructions", "question", "save_to_file"):
        assert f"`{param}" in body
        assert any(param in t.input_schema["properties"] for t in tools.values()), param
