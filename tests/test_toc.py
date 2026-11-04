from local_private_ollama_mcp.toc import build_toc

MD = """# Proposal
Intro text.

## 1 Objectives
Aims here.

### 1.1 Detail
More.

```python
# not a heading
```

## 2 Work plan
Plan text.
"""


def test_markdown_toc_ranges_and_nesting():
    method, toc = build_toc(MD)
    assert method == "markdown"
    assert [t.title for t in toc] == ["Proposal", "1 Objectives", "1.1 Detail", "2 Work plan"]
    by = {t.title: t for t in toc}
    # Each section starts at its heading line.
    for t in toc:
        assert MD[t.start:].startswith("#")
    # A level-2 section includes its level-3 subsection and ends at the next level-2 heading.
    obj = by["1 Objectives"]
    assert "1.1 Detail" in MD[obj.start:obj.end]
    assert obj.end == by["2 Work plan"].start
    assert by["1.1 Detail"].end == by["2 Work plan"].start
    # The top-level section spans the whole document; the last section runs to the end.
    assert by["Proposal"].end == len(MD)
    assert by["2 Work plan"].end == len(MD)


def test_code_fence_is_ignored():
    _, toc = build_toc(MD)
    assert all("not a heading" not in t.title for t in toc)


def test_heuristic_numbered_and_known_headings():
    text = (
        "Abstract\nWe study robots.\n1 Introduction\nRobots are useful. 2. This is a sentence.\n"
        "2.1 Related Work\nPrior art.\n1. We will do this in a list item.\nReferences\n[1] A. B. (2020). Title.\n"
    )
    method, toc = build_toc(text)
    assert method == "heuristic"
    titles = [t.title for t in toc]
    assert titles == ["Abstract", "1 Introduction", "2.1 Related Work", "References"]
    assert {t.title: t.level for t in toc}["2.1 Related Work"] == 2
    refs = toc[-1]
    assert text[refs.start:refs.end].startswith("References")


def test_pdf_outline_is_located_in_text():
    text = "Cover page\nSection One Title\nbody\nSection  Two\nTitle more body\n"
    method, toc = build_toc(text, [(1, "Section One Title"), (1, "Section Two Title"), (1, "Missing")])
    assert method == "pdf_outline"
    assert [t.title for t in toc] == ["Section One Title", "Section Two Title"]
    assert text[toc[0].start:].startswith("Section One Title")
    assert toc[0].end == toc[1].start


def test_no_structure():
    assert build_toc("just some plain text without headings.") == ("none", [])
