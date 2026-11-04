import json

import pytest
import yaml
from fastmcp import Client
from fastmcp.exceptions import ToolError

from local_private_ollama_mcp.llm import LLMError
from local_private_ollama_mcp.server import REF_SYSTEM, SYSTEM_NOTES

from .conftest import make_pdf

DOC = """# Proposal

## Objectives
We will build GPS-free navigation for strawberry robots.

## Work plan
WP1 covers field trials at three farms. WP2 covers evaluation.

## References
[1] Hanheide, M. et al. (2017). The STRANDS Project: Long-Term Autonomy in Everyday Environments. IEEE Robotics and Automation Magazine.
[2] European Commission. Horizon Europe Work Programme 2025, Cluster 6. Brussels.
[3] ROS 2 Documentation: Navigation2. https://docs.nav2.org/
"""


def front_matter(path: str) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8").read().split("---")[1])


async def test_instructions_and_typed_schemas(client, server):
    assert "LOCAL language model" in client.instructions
    # Classic MCP clients (e.g. Claude Desktop) receive the same text on initialize.
    async with Client(server, mode="legacy") as legacy:
        assert "char_range" in legacy.initialize_result.instructions
    tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {
        "server_status", "list_documents", "document_overview", "count_mentions", "yes_no", "classify",
        "summarise_to_file", "answer_to_file", "assess", "extract_references",
    }
    for t in tools.values():
        assert t.output_schema and t.output_schema.get("type") == "object", t.name
    yes_no_props = tools["yes_no"].input_schema["properties"]
    assert yes_no_props["question"]["maxLength"] == 500
    assert "char_range" in yes_no_props


async def test_overview_returns_toc_with_ranges(client, docs):
    (docs / "p.md").write_text(DOC)
    ov = (await client.call_tool("document_overview", {"name": "p.md"})).structured_content
    assert ov["toc_method"] == "markdown" and ov["file_type"] == "markdown"
    assert ov["title"] == "Proposal" and ov["characters"] == len(DOC)
    titles = [s["title"] for s in ov["sections"]]
    assert titles == ["Proposal", "Objectives", "Work plan", "References"]
    wp = next(s for s in ov["sections"] if s["title"] == "Work plan")
    assert DOC[wp["start"]:wp["end"]].startswith("## Work plan")
    assert "References" not in DOC[wp["start"]:wp["end"]]


async def test_overview_of_pdf_uses_outline_and_cache(client, docs):
    make_pdf(docs / "p.pdf", [("Introduction", "Robots in fields."), ("Methods", "We navigate without GPS.")])
    ov1 = (await client.call_tool("document_overview", {"name": "p.pdf"})).structured_content
    assert ov1["file_type"] == "pdf" and ov1["pages"] == 2 and not ov1["conversion_cached"]
    assert ov1["toc_method"] in ("pdf_outline", "heuristic")
    assert [s["title"] for s in ov1["sections"]] == ["Introduction", "Methods"]
    ov2 = (await client.call_tool("document_overview", {"name": "p.pdf"})).structured_content
    assert ov2["conversion_cached"]


async def test_char_range_restricts_what_the_model_reads(client, docs, llm):
    (docs / "p.md").write_text(DOC)
    ov = (await client.call_tool("document_overview", {"name": "p.md"})).structured_content
    obj = next(s for s in ov["sections"] if s["title"] == "Objectives")
    r = (await client.call_tool("yes_no", {"name": "p.md", "question": "Is GPS used?",
                                           "char_range": {"start": obj["start"], "end": obj["end"]}})).structured_content
    assert r["answer"] == "yes" and r["processing"]["range_start"] == obj["start"]
    prompt = llm.final_calls()[-1].user
    assert "GPS-free" in prompt and "WP1" not in prompt


async def test_invalid_ranges_and_inputs(client, docs):
    (docs / "p.md").write_text(DOC)
    with pytest.raises(ToolError):
        await client.call_tool("yes_no", {"name": "p.md", "question": "q?", "char_range": {"start": 10, "end": 5}})
    with pytest.raises(ToolError, match="only"):
        await client.call_tool("yes_no", {"name": "p.md", "question": "Valid?", "char_range": {"start": 99999, "end": 100000}})
    with pytest.raises(ToolError):
        await client.call_tool("classify", {"name": "p.md", "labels": ["only-one"]})
    with pytest.raises(ToolError, match="Unsupported"):
        (docs / "x.docx").write_bytes(b"PK")
        await client.call_tool("document_overview", {"name": "x.docx"})


async def test_large_document_is_condensed_within_context(client, docs, llm, settings):
    big = "\n\n".join(f"Paragraph {i}: field trials of robot navigation at farm {i}." * 5 for i in range(400))
    (docs / "big.txt").write_text(big)
    r = (await client.call_tool("assess", {"name": "big.txt", "criterion": "feasibility of the trials",
                                           "min_score": 1, "max_score": 5})).structured_content
    proc = r["processing"]
    assert proc["condensed"] and proc["chunks"] > 1 and not proc["truncated"]
    budget = settings.doc_budget_chars
    # No call ever sends more document text than the budget allows.
    for c in llm.calls:
        doc_part = c.user.split("<document>\n", 1)[1].split("\n</document>", 1)[0]
        assert len(doc_part) <= budget
    fm = front_matter(r["written_to"])
    assert fm["condensed"] is True and fm["score"] == 1 and fm["score_min"] == 1
    assert fm["tags"] == ["local-llm", "assessment"] and fm["source"] == "big.txt"


async def test_assess_clamps_and_writes_justification(client, docs, llm):
    (docs / "p.md").write_text(DOC)
    llm.handler = lambda c: json.dumps({"justification": "Clear but thin.", "score": 42})
    r = (await client.call_tool("assess", {"name": "p.md", "criterion": "clarity", "min_score": 1,
                                           "max_score": 6, "rubric": "1 poor, 6 excellent"})).structured_content
    assert r["score"] == 6
    text = open(r["written_to"], encoding="utf-8").read()
    assert "Clear but thin." in text and "clamped" in text
    with pytest.raises(ToolError, match="lower than"):
        await client.call_tool("assess", {"name": "p.md", "criterion": "clarity", "min_score": 5, "max_score": 5})


async def test_answer_and_summary_notes(client, docs, settings):
    (docs / "p.md").write_text(DOC)
    a = (await client.call_tool("answer_to_file", {"name": "p.md", "question": "What is WP1?"})).structured_content
    s = (await client.call_tool("summarise_to_file", {"name": "p.md"})).structured_content
    for res, kind in ((a, "answer"), (s, "summary")):
        assert res["written_to"].startswith(str(settings.output_dir))
        fm = front_matter(res["written_to"])
        assert fm["type"] == kind and fm["model"] == settings.model and len(fm["source_sha256"]) == 64
    assert "Model answer." not in json.dumps(a)  # text stays on disk


async def test_extract_references_scopes_to_reference_section(client, docs, llm):
    (docs / "p.md").write_text(DOC)
    refs = [
        {"text": "[1] Hanheide, M. et al. (2017). The STRANDS Project: Long-Term Autonomy in Everyday "
                 "Environments. IEEE Robotics and Automation Magazine.", "kind": "journal_article"},
        {"text": "European Commission. Horizon Europe Work Programme 2025, Cluster 6. Brussels.", "kind": "report"},
        {"text": "ROS 2 Documentation: Navigation2. https://docs.nav2.org/", "kind": "web_resource"},
        {"text": "Doe, J. (2019). Quantum harvesting with drones. Nature.", "kind": "journal_article"},
        {"text": "European Commission. Horizon Europe Work Programme 2025, Cluster 6. Brussels.", "kind": "report"},
    ]
    llm.handler = lambda c: json.dumps({"references": refs}) if c.system == REF_SYSTEM else "x"
    r = (await client.call_tool("extract_references", {"name": "p.md", "save_to_file": True})).structured_content
    assert r["scope"] == ["section 'References'"]
    texts = [x["text"] for x in r["references"]]
    # Leading marker stripped, a reference without DOI or year kept, the invented one dropped, duplicate removed.
    assert texts[0].startswith("Hanheide, M.")
    assert any(t.startswith("European Commission") for t in texts)
    assert not any("Quantum" in t for t in texts)
    assert len(texts) == 3 and r["dropped_unverified"] == 1
    assert r["web_links"] == ["https://docs.nav2.org/"]
    # Only the reference section was sent to the model.
    sent = [c.user for c in llm.calls if c.system == REF_SYSTEM]
    assert all("GPS-free" not in u for u in sent)
    assert front_matter(r["written_to"])["reference_count"] == 3


async def test_extract_references_salvages_truncated_json(client, docs, llm):
    (docs / "p.md").write_text(DOC)
    llm.handler = lambda c: ('{"references":[{"text":"European Commission. Horizon Europe Work Programme 2025, '
                             'Cluster 6. Brussels.","kind":"report"},{"text":"Cut off mid')
    r = (await client.call_tool("extract_references", {"name": "p.md"})).structured_content
    assert len(r["references"]) == 1


async def test_ollama_failure_becomes_clean_tool_error(client, docs, llm):
    (docs / "p.md").write_text(DOC)

    def down(c):
        raise LLMError("Cannot connect to Ollama at http://127.0.0.1:9.")

    llm.handler = down
    with pytest.raises(ToolError, match="local model unavailable.*Cannot connect"):
        await client.call_tool("yes_no", {"name": "p.md", "question": "Is it?"})


async def test_unexpected_error_does_not_leak_or_crash(client, docs, llm):
    (docs / "p.md").write_text(DOC)

    def bug(c):
        raise KeyError("secret document text")

    llm.handler = bug
    with pytest.raises(ToolError) as e:
        await client.call_tool("yes_no", {"name": "p.md", "question": "Is it?"})
    assert "KeyError" in str(e.value) and "secret document text" not in str(e.value)
    # The server keeps working afterwards.
    llm.handler = None
    assert (await client.call_tool("list_documents", {})).structured_content["documents"]


async def test_malformed_model_output(client, docs, llm):
    (docs / "p.md").write_text(DOC)
    llm.handler = lambda c: "not json"
    with pytest.raises(ToolError, match="malformed"):
        await client.call_tool("yes_no", {"name": "p.md", "question": "Is it?"})
    llm.handler = lambda c: json.dumps({"label": "invented"})
    with pytest.raises(ToolError, match="did not return one of the given labels"):
        await client.call_tool("classify", {"name": "p.md", "labels": ["a", "b"]})


async def test_unwritable_output_dir(client, docs, settings, llm):
    (docs / "p.md").write_text(DOC)
    settings.output_dir.chmod(0o500)
    try:
        if (settings.output_dir / "probe").exists() or _can_write(settings.output_dir):
            pytest.skip("running as root; permissions not enforced")
        with pytest.raises(ToolError, match="Could not write the note"):
            await client.call_tool("answer_to_file", {"name": "p.md", "question": "What?"})
    finally:
        settings.output_dir.chmod(0o700)


def _can_write(d):
    try:
        (d / ".t").write_text("")
        (d / ".t").unlink()
        return True
    except OSError:
        return False


async def test_server_status_with_fake_backend(client, docs):
    (docs / "p.md").write_text(DOC)
    st = (await client.call_tool("server_status", {})).structured_content
    assert st["ok"] and st["documents"] == 1 and st["problems"] == []


async def test_notes_use_condensed_marker_only_when_needed(client, docs, llm):
    (docs / "p.md").write_text(DOC)
    await client.call_tool("yes_no", {"name": "p.md", "question": "Is it?"})
    assert not [c for c in llm.calls if c.system == SYSTEM_NOTES]
