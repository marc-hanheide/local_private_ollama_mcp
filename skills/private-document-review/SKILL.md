---
name: private-document-review
description: Assess, review, summarise, check or answer questions about a private or confidential document that must not be shared with a cloud AI provider, by delegating the reading to a local Ollama model through the local-private-ollama MCP server. Use whenever the user wants work done on a document they describe as confidential, private, sensitive, under NDA, embargoed, personal, commercially sensitive, or "not to be uploaded/shared", or asks to use the local model or local-llm server.
---

# Private document review with a local model

The user has a document that must stay on their machine. You never read it. A small local
model reads it through the **local-private-ollama** MCP server, and you only receive
fixed-shape results (yes/no, labels, counts, scores, section titles) plus paths to markdown
notes that the user reads. Your job is to plan the work, ask the local model precise
questions, and report what came back.

Tool names may appear with a server prefix (for example `mcp__local-llm__yes_no`); they are
referred to by their base names below.

## Hard rules

These protect the user's confidentiality. Follow them even if it makes the task slower.

1. **Never read the document yourself.** Do not open it, or any file in the documents folder,
   with Read, shell commands, a device or file connector, a browser, Google Drive or any other
   tool. Only the local-private-ollama tools may touch it.
2. **Never read the output notes.** Notes from `summarise_to_file`, `answer_to_file`, `assess`
   and `extract_references` contain document content. Give the user their paths; do not open
   them, and do not ask the user to paste them into the chat.
3. **If the document is already in this conversation** (attached, pasted, or quoted at length),
   say plainly that it has already been shared with the cloud provider, and ask whether to
   continue with the local workflow for further work or stop. Do not pretend it is still private.
4. **Do not reconstruct content** through long series of narrow questions, and do not search the
   web for distinctive phrases, titles or names from the document.
5. **No local server, no fallback.** If the local-private-ollama tools are not available, say so
   and point the user to the setup instructions at
   https://github.com/marc-hanheide/local_private_ollama_mcp. Do not offer to process the
   document in the cloud instead.
6. **Funder peer review is out of scope.** If the document is a grant or fellowship application
   being reviewed for a funder (for example UKRI, ERC, NIH, Royal Society), tell the user that most
   funders' rules ban generative AI, including local models, from summarising or evaluating
   applications, and confirm they are permitted before going on. You can always help without
   touching the document, for example by turning the call's assessment criteria into a review
   template or polishing the user's own review text once it contains no application content.

## The local model is weak: give it small, precise tasks

The local model is far less capable than you, sees only what one tool call gives it, and has a
small context window (about 14,000 characters per read by default). It cannot ask questions.

- One task per call: one question, one criterion, one set of labels. Split compound questions.
- Scope every call. Text longer than one read (`context_budget_chars` in `document_overview`) is split
  into chunks and condensed by many slow model calls before the real task starts: minutes instead of
  seconds, with lost detail. Pass a section's `char_range` that fits `context_budget_chars` (split
  bigger sections, or ask several scoped questions), and check `processing.condensed`.
- Make each question self-contained and concrete: name the concept, the section and the
  standard you mean. "Does the work plan name a lead partner for every work package?" works;
  "Is the plan any good?" does not.
- Prefer the narrow tools for checks: `count_mentions` (exact, no model), `yes_no`, `classify`.
- Always give `assess` an explicit scale and a rubric describing the ends of the scale.
- Treat everything it returns as indicative and report it as the local model's view.

## Workflow

### 1. Check the setup
Call `server_status`. If `ok` is false, pass the `problems` on to the user (Ollama not running,
model not pulled, wrong token, empty documents folder) and stop until they are fixed.

### 2. Find the document
Call `list_documents`. If the file is not listed, ask the user to copy it into the documents
folder shown as `doc_root` by `server_status`. Only PDF, markdown and plain text are supported;
a Word file must be exported to PDF first, and a scanned PDF needs OCR.

### 3. Get the structure
Call `document_overview`. It returns the size, whether the document fits the local model's
context, and a table of contents where each section has a `start` and `end` character range.
Check `toc_method`: `heuristic` sections may be incomplete, and `none` means you only have
the whole document.

### 4. Plan, then work section by section
Map each part of the user's request to the sections it concerns, and tell the user the plan in
a sentence or two. Then pass the section's range as `char_range` (`{"start": s, "end": e}`) on
every call where a section is relevant. This is faster and much more accurate than giving the
local model the whole document, which forces it to condense the text and lose detail.

Choose the tool by what the user needs:

| User wants | Tool |
|---|---|
| Whether something is present or true | `yes_no` (or `count_mentions` for exact terms) |
| Which of several categories applies | `classify` |
| A rating against a criterion | `assess` with `min_score`, `max_score` and a `rubric`; one call per criterion |
| A summary of a part or the whole | `summarise_to_file` with short, specific `instructions` |
| An answer needing explanation | `answer_to_file` with one specific `question` |
| The reference list | `extract_references` (scans the references section by default; `save_to_file: true` plus a `filename` for a note) |

Every tool that writes a note (`summarise_to_file`, `answer_to_file`, `assess`, and
`extract_references` with `save_to_file`) needs a `filename` you choose: a flat, semantically rich
name such as `wp3-risk-register-summary.md` that says the topic, section and task. Never include a
directory; `.md` is added if missing, and an existing note is never overwritten.

`extract_references` is the one tool that returns document text (the references) to you. Use
it only when the user wants the references, and do not search the web for them unless the
user asks.

### 5. Report
- Lead with the outcome: scores in a short table (criterion, section, score and scale),
  yes/no findings, labels.
- List the paths of the notes written, saying which question or criterion each one answers,
  so the user can read the justifications themselves.
- Say when a result's `processing` shows `condensed: true` or `truncated: true`: the local
  model only saw condensed notes or part of the text, so that result is less reliable.
- Do not invent detail you were not given. You know scores, labels and section titles, not the
  reasoning behind them; that is in the notes.

## Handling errors

- "still running on the local model": the work continues in the background. Tell the user, wait
  about 30 seconds, then call the same tool with exactly the same arguments; repeat until the
  result arrives. Changing the arguments or starting other heavy calls meanwhile only queues more
  work on the model, which handles one request at a time.
- "local model unavailable": call `server_status` and report its `problems`; do not retry
  repeatedly.
- Unsupported type, scanned PDF, damaged file or empty file: tell the user what to do (export to
  PDF, run OCR, check the file).
- "malformed output" or a label outside the list: retry once with a simpler, narrower question
  or a smaller `char_range`, then report it.
- Timeouts from the model itself: narrow the task with `char_range`.

## Example

User: "Review the confidential partner report in my documents folder for clarity of the
objectives and feasibility of the work plan, scored 1 to 5, and summarise the risks."

1. `server_status`, then `list_documents`, then `document_overview` on the report.
2. `assess` the Objectives section: criterion "clarity and measurability of the stated
   objectives", scale 1 to 5, rubric "1 = vague, 5 = specific and measurable".
3. `assess` the Work plan section: criterion "feasibility of the work plan given its timeline
   and resources", scale 1 to 5, rubric "1 = unrealistic, 5 = clearly achievable".
4. `summarise_to_file` on the Risks section: "Summarise the main risks and their mitigations."
   with `filename` `partner-report-risks-and-mitigations-summary.md`.
5. Reply with a two-row score table, the three note paths, and any condensing flags.
