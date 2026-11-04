# local_private_ollama_mcp

[![CI](https://github.com/marc-hanheide/local_private_ollama_mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/marc-hanheide/local_private_ollama_mcp/actions/workflows/ci.yml)

A narrow, privacy-preserving [MCP](https://modelcontextprotocol.io) server that lets Claude delegate questions about your documents to a **local Ollama model**, without the document text passing through Claude.

Claude sends a file name and one clearly defined task ("is there a risk register?", "rate the clarity of the work plan from 1 to 6"). The local model reads the document on your machine and sends back only a fixed-shape answer: yes/no, a label, a count or a score. Anything that needs prose (summaries, answers, justifications) is written to a markdown note on disk for you to read, and Claude only learns where it is.

Built with [FastMCP](https://gofastmcp.com) 4 over stdio, with Pydantic-typed inputs and outputs for every tool.

## How it protects your documents

- Claude only ever sends file names and short instructions, never document content.
- Documents are read from one sandbox folder (`DOC_ROOT`); path traversal is blocked.
- Answers are forced into fixed JSON shapes with Ollama's structured output, so the local model cannot paste document text back.
- Free text goes to markdown notes on disk, never to Claude.
- Every response sent to Claude is appended to an audit log (`AUDIT_LOG`).
- Two deliberate exceptions: `document_overview` returns section titles and character ranges, and `extract_references` returns the reference list.

This is data minimisation, not a guarantee. Enough targeted yes/no questions can reconstruct facts, and your questions and file names do go to Claude, so keep file names neutral. Funder policies such as UKRI's ban generative AI, local models included, from summarising or evaluating grant applications, so don't use this for funder peer review.

## Requirements

- [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) with a model pulled, e.g. `ollama pull qwen3:14b` (about 10 GB at the default 8192-token context)
- Python 3.10 to 3.13 (uv installs it for you if needed)

## Install

Install it once as a uv tool:

```bash
uv tool install git+https://github.com/marc-hanheide/local_private_ollama_mcp
```

This puts a `local-private-ollama-mcp` command on your PATH (usually in `~/.local/bin`). Upgrade later with `uv tool upgrade local-private-ollama-mcp`.

Or run it without installing, always fetching the latest version:

```bash
uvx --from git+https://github.com/marc-hanheide/local_private_ollama_mcp local-private-ollama-mcp
```

Then create the documents folder and put your PDFs, markdown and text files in it:

```bash
mkdir -p ~/local-llm/docs
```

## Use it in Claude Desktop

Add the server to `claude_desktop_config.json` and restart Claude Desktop. The file lives in `~/Library/Application Support/Claude/` on macOS and `%APPDATA%\Claude\` on Windows.

```json
{
  "mcpServers": {
    "local-llm": {
      "command": "/Users/you/.local/bin/local-private-ollama-mcp",
      "env": {
        "DOC_ROOT": "/Users/you/local-llm/docs",
        "OLLAMA_MODEL": "qwen3:14b",
        "OLLAMA_API_KEY": "your-bearer-token-if-needed"
      }
    }
  }
}
```

Use absolute paths: Claude Desktop does not read your shell profile, so `~` and your PATH are not available. Find the command's path with `which local-private-ollama-mcp`.

To run straight from GitHub instead of installing, use `uvx`:

```json
{
  "mcpServers": {
    "local-llm": {
      "command": "/Users/you/.local/bin/uvx",
      "args": ["--from", "git+https://github.com/marc-hanheide/local_private_ollama_mcp", "local-private-ollama-mcp"],
      "env": { "DOC_ROOT": "/Users/you/local-llm/docs" }
    }
  }
}
```

Leave out `OLLAMA_API_KEY` for a plain local Ollama. If you'd rather not keep a token in the config file, point `command` at a small wrapper script that reads it from your keychain, exports it, and then runs `local-private-ollama-mcp`.

## Use it in Claude Code

```bash
claude mcp add local-llm --scope user \
  -e DOC_ROOT="$HOME/local-llm/docs" \
  -e OLLAMA_MODEL=qwen3:14b \
  -- local-private-ollama-mcp
```

Add `-e OLLAMA_API_KEY=...` if your Ollama needs a bearer token. To run from GitHub without installing, replace the last line with `-- uvx --from git+https://github.com/marc-hanheide/local_private_ollama_mcp local-private-ollama-mcp`.

## Working with it in Claude

The server tells Claude on connection that the local model is much weaker than Claude, has no conversation context, and should get one clearly defined, self-contained task per call. Good prompts follow the same pattern:

1. **Check the setup:** "Use local-llm to check the server status." If anything is wrong (Ollama not running, model not pulled, wrong token), the `problems` list says what to fix.
2. **Get the structure:** "Use local-llm to give me an overview of report.pdf." This returns the table of contents with a character range per section.
3. **Ask targeted questions on one section:**
   - "Using local-llm, does the work plan section of report.pdf include a risk register?"
   - "Use local-llm to assess the methodology section of report.pdf for 'rigour of the evaluation design' on 1 to 6, rubric: 1 = absent, 6 = exemplary."
   - "Use local-llm to answer 'Which partners lead which work packages?' for report.pdf and save it to a note."
   - "Use local-llm to extract the references from report.pdf and save them."
4. **Read the notes** that appear in `~/Downloads`; they open directly in Obsidian.

Claude passes the section's range as `char_range`, so the local model reads only that part. That is faster and much more accurate than processing a whole long document.

## Companion skill

[`skills/private-document-review/SKILL.md`](skills/private-document-review/SKILL.md) teaches Claude to use this server whenever you ask it to assess, review, summarise or answer questions about a document that must not go to a cloud provider. It sets hard rules (never open the document or the output notes with other tools, no cloud fallback, a check on funder peer-review rules), the section-by-section workflow above, how to phrase tasks for a small model, and how to report results.

- **Claude Code:** copy the folder into your skills directory:
  ```bash
  mkdir -p ~/.claude/skills
  cp -r skills/private-document-review ~/.claude/skills/
  ```
- **Claude apps:** add it as a custom skill, for example by uploading the `private-document-review` folder as a zip.

A test checks that the skill mentions every tool the server offers and no tools it doesn't, so the two stay in sync.

## Tools

| Tool | Returns to Claude |
|---|---|
| `server_status()` | Ollama reachable, model installed, token set, folders usable, plus a list of problems |
| `list_documents()` | Supported files in `DOC_ROOT` with type and size |
| `document_overview(name)` | Type, size, SHA-256, title, pages, characters, words, whether it fits the context, and the table of contents with `start`/`end` character ranges |
| `count_mentions(name, term, char_range?)` | A count (regex, no LLM) |
| `yes_no(name, question, char_range?)` | `yes`/`no`/`unclear` and a confidence |
| `classify(name, labels, char_range?)` | One label from the 2 to 20 you gave |
| `summarise_to_file(name, instructions, style, char_range?)` | Path and word count; the summary stays on disk |
| `answer_to_file(name, question, max_words, char_range?)` | Path and word count; the answer stays on disk |
| `assess(name, criterion, min_score, max_score, rubric, char_range?)` | The integer score; the justification stays on disk |
| `extract_references(name, char_range?, save_to_file)` | The references with their kind, web links, and what was scanned |

All model-backed tools also return a `processing` block: the character range used, the number of chunks, and whether the text had to be condensed or truncated.

### Table of contents and character ranges

`document_overview` builds the table of contents without the LLM, trying in order:

1. markdown headings (`#`, `##`, ...) in the document or the converted PDF
2. the PDF's bookmark outline, located in the converted text
3. heuristics: numbered headings such as `2.1 Methods` and common section names such as "Abstract", "Work plan" or "References"

`toc_method` says which one was used. Each section's range runs from its heading to the next heading of the same or a higher level, so it includes its subsections. Ranges are character offsets in the text every tool works on, so any `start`/`end` pair can be passed as `char_range`.

### Large documents

The local model's context is small (8192 tokens by default, about 14,000 characters per read). Text that doesn't fit is split into overlapping chunks at paragraph boundaries, each chunk is condensed into notes relevant to the current task, and this repeats until the notes fit; the final step then runs on the notes. Results report this as `condensed: true`. Passing a `char_range` usually avoids it entirely.

### Reference extraction

`extract_references` scans the detected "References", "Bibliography" or similar section(s), or a `char_range` you give, or the whole document if there is no such section.

The text is shown to the local model with numbered lines, and a strongly restrictive prompt asks it only to locate complete reference entries, in any citation style, and return each one's first and last line number and its kind. It never returns citation markers, body text, names or headings, and references don't need a DOI, URL or year. The reference text is then cut from the document itself, so it is verbatim and cannot be invented. Because the model writes a few numbers per reference rather than copying text, extraction is about five times faster than asking for the text.

Out-of-range and overlapping spans are discarded, leading markers such as `[12]` removed and duplicates merged. Each reference carries a `kind` (journal article, conference paper, book, report, web resource and so on). Web links are additionally collected by pattern matching.

## Documents and caching

Only **PDF**, **markdown** and **plain text** files are accepted. PDFs are converted to markdown locally with [markitdown](https://github.com/microsoft/markitdown) (no URL fetching, no plugins). The converted text, page count, title and outline are cached in `CACHE_DIR`, keyed by the SHA-256 of the PDF and the markitdown version, so repeat calls skip conversion until the file's content changes. The cache folder is created with owner-only permissions. Scanned PDFs without a text layer return an error; run OCR on them first (e.g. `ocrmypdf`).

## Notes for Obsidian

Notes are markdown files with YAML front matter (`title`, `created`, `tags`, `type`, `source`, `source_sha256`, `model`, the range used, `chunks`, `condensed`, plus `question`, `criterion`, `score`, `score_min`, `score_max` or `rubric` as relevant), so you can query them with Dataview or Bases. Callouts flag when only part of the document was used or when it was condensed.

## Errors

Failures come back to Claude as clear tool errors rather than crashes:

- Ollama not running or wrong `OLLAMA_URL`, a rejected or missing bearer token, a model that isn't pulled, or a timeout each get a specific message saying what to fix.
- Transient failures (connection refused, HTTP 429 and 5xx) are retried twice with backoff; timeouts are not retried.
- **Long-running work never hangs the client.** Claude gives up on a tool call after about a minute, while a local model can need longer. Every document tool therefore runs as a background job and returns within `TOOL_TIME_BUDGET` seconds (default 50). If the job isn't finished, the tool says it is still running and what it is doing (for example "condensing part 3 of 7"). Calling it again with the same arguments attaches to the same job instead of queuing duplicate work on Ollama, and returns the result once it's ready. Finished results are kept for an hour, and editing the document starts a fresh job.
- Damaged, encrypted or scanned PDFs, empty files, unsupported types and bad ranges are reported as such.
- Malformed model output is reported (and partially salvaged for reference lists).
- Unexpected errors are logged with a traceback to stderr and reported by type only, so no document text leaks into error messages. The server keeps running.

Ollama being down doesn't stop the server from starting: the document tools still work and `server_status` explains the problem.

## Configuration

All settings are environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `DOC_ROOT` | `~/local-llm/docs` | Sandbox folder documents are read from |
| `OUTPUT_DIR` | `~/Downloads` (or `~/Download`) | Where markdown notes are written |
| `CACHE_DIR` | `~/local-llm/cache` | Converted-PDF cache |
| `AUDIT_LOG` | `~/local-llm/audit.jsonl` | Log of everything returned to Claude |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_API_KEY` | unset | Bearer token, sent as `Authorization: Bearer <token>` |
| `OLLAMA_MODEL` | `qwen3:14b` | Model name |
| `NUM_CTX` | `8192` | Context window in tokens; chunk sizes scale with it |
| `CHARS_PER_TOKEN` | `3.2` | Conservative size estimate; lower it if Ollama warns about truncated prompts |
| `OLLAMA_TIMEOUT` | `900` | Seconds per model call |
| `TOOL_TIME_BUDGET` | `50` | Seconds a tool call waits before reporting that its job is still running; keep it below your client's timeout, `0` waits without limit |

Raising `NUM_CTX` (e.g. 32768, about 14 to 16 GB for qwen3:14b) means fewer chunks and better results on long documents.

## Development

```bash
git clone https://github.com/marc-hanheide/local_private_ollama_mcp.git
cd local_private_ollama_mcp
uv sync --group dev
uv run --group dev pytest       # tests use a fake model; no Ollama needed
uv run --group dev ruff check src tests
```

The test suite covers the table of contents, the hash-keyed cache, every tool through an in-memory MCP client, condensing within the context budget, reference verification, the Ollama client's error handling and retries, and an end-to-end run of the real stdio server against a fake Ollama HTTP endpoint. CI runs it on Python 3.10 to 3.13 on Linux and on macOS, and checks that `uv tool install` works.
