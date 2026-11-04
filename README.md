# local-llm-mcp

A narrow MCP server that lets Claude Desktop ask your local Ollama model (qwen3:14b) about documents, without the document text passing through Claude.

## Tools

| Tool | Returns to Claude |
|---|---|
| `list_documents()` | File names in the documents folder |
| `document_stats(name)` | Word/character counts, whether it fits the context |
| `count_mentions(name, term)` | A count (plain regex, no LLM) |
| `yes_no(name, question)` | `yes` / `no` / `unclear` + confidence |
| `classify(name, labels)` | One label from the list you gave |
| `summarise_to_file(name, instructions)` | Only the path and word count; the summary stays on disk |
| `answer_to_file(name, question, max_words)` | Only the path and word count; the answer stays on disk |
| `assess(name, criterion, min_score, max_score, rubric)` | The integer score; the justification stays on disk |
| `extract_references(name, save_to_file)` | Literature references, web links and DOIs (see below) |

Answers are forced into fixed JSON shapes with Ollama's structured output, so the model can't paste document text back. Every response sent to Claude is logged to `audit.jsonl`.

### What Claude is told

The server sends MCP instructions that Claude reads on connection: the local model is much weaker than Claude and has no conversation context, so Claude should give it one clearly defined, self-contained task per call, split compound questions, treat answers and scores as indicative, flag when a document was condensed, and not try to reconstruct document content through many narrow questions. Tool descriptions repeat the "one specific question / one criterion" rule.

### Large documents

The default context is 8192 tokens, and documents of any length work:

1. If a document doesn't fit (about 14,000 characters at 8192 tokens), it is split into overlapping chunks at paragraph boundaries.
2. The local model reads each chunk and writes short notes relevant to the current task only (or `NONE`).
3. If the notes are still too long, they are condensed again, up to four rounds.
4. The final step (answer, score, label) runs on the notes.

Every result includes `chunks`, `condensed` and `truncated`, and markdown notes show an info callout when condensing happened. `truncated` is only true if condensing failed to shrink the text. `extract_references` never condenses: it reads every chunk in full, with smaller chunks to leave room for the output. Expect long documents to take a while, roughly one model call per chunk. `document_stats` reports `chunks_needed` up front.

### extract_references

This is the one tool that deliberately returns document text (the references) to Claude.

- **Web links and DOIs** are found by pattern matching only, never by the model, so they can't be invented.
- **Literature references** are extracted by the model with a restrictive prompt (full references to journal articles, conference papers, books, chapters, theses, reports, preprints, standards and patents only; no bare citation markers, names, organisations, software, datasets or body text). The whole document is processed in chunks, so reference lists at the end aren't lost to truncation.
- Each candidate is then **verified**: it must contain a year, DOI or URL, be under 600 characters, and at least 85% of its words must occur in the document. Anything failing is dropped and counted in `dropped_unverified`.
- `save_to_file=true` also writes an Obsidian note with numbered references, links and clickable DOIs.

### Output notes

All outputs are markdown files with Obsidian-friendly YAML front matter, saved to `~/Downloads` by default (or `~/Download` if that is the folder that exists). Properties include `title`, `created`, `tags` (`local-llm` plus the note type), `type`, `source`, `model`, `truncated`, and tool-specific fields such as `question`, `criterion`, `score`, `score_min`, `score_max` and `rubric`, so you can query them with Dataview or Bases.

### Example prompts in Claude

- "Use local-llm to answer 'What are the main risks and how are they mitigated?' for report.pdf."
- "Use local-llm to assess report.pdf for 'clarity of the work plan in relation to the stated objectives' on 1 to 6, rubric: 1 = unclear, 6 = exemplary."
- "Use local-llm to extract the references from report.pdf and save them to a file."

For `assess`, the model writes its justification before the score, which tends to give better-grounded numbers. Scores outside the range are clamped and flagged in the file.

## Setup

```bash
cd local-llm-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ollama pull qwen3:14b
mkdir -p ~/local-llm/docs     # put documents here
```

## Claude Desktop config

Add to `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`), using absolute paths, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "local-llm": {
      "command": "/ABSOLUTE/PATH/local-llm-mcp/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/local-llm-mcp/server.py"],
      "env": {
        "DOC_ROOT": "/Users/you/local-llm/docs",
        "OLLAMA_URL": "http://127.0.0.1:11434",
        "OLLAMA_API_KEY": "your-bearer-token",
        "OLLAMA_MODEL": "qwen3:14b",
        "NUM_CTX": "8192"
      }
    }
  }
}
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DOC_ROOT` | `~/local-llm/docs` | Sandbox folder documents are read from |
| `OUTPUT_DIR` | `~/Downloads` | Where markdown notes are written |
| `AUDIT_LOG` | `~/local-llm/audit.jsonl` | Log of everything returned to Claude |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_API_KEY` | unset | Bearer token, sent as `Authorization: Bearer <token>`; leave unset for unauthenticated Ollama |
| `OLLAMA_MODEL` | `qwen3:14b` | Model name |
| `NUM_CTX` | `8192` | Context window; chunk sizes scale with it |
| `CHARS_PER_TOKEN` | `3.2` | Conservative size estimate; lower it if you see Ollama truncation warnings |

The token lives in `claude_desktop_config.json` in plain text. If you'd rather not, point `command` at a small wrapper script that reads it from your keychain and exports it before starting the server.

## Notes

- **Context vs speed:** qwen3:14b at 8192 tokens runs on about 10 GB. Raising `NUM_CTX` (e.g. 32768, about 14 to 16 GB) means fewer chunks, less condensing and better results on long documents.
- **Residual leakage:** narrow tools still leak a little. Enough targeted yes/no questions can reconstruct facts, and Claude's question text and the file names go to Anthropic. Keep file names neutral, and treat this as data minimisation, not a guarantee.
- **Not for funder peer review:** UKRI and similar policies ban generative AI from summarising or evaluating applications, local models included.
- Requires the MCP Python SDK 1.x (`mcp<2`); 2.x renamed `FastMCP`.
