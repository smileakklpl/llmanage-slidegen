# Claude instructions — LLManage SlideGen Local Private Edition

Read and follow `AGENTS.md` and `.claude/skills/local-private-deck/SKILL.md` for deck jobs.

Claude running in an IDE or terminal is not automatically a local model. Do not open sensitive Excel, prompts, `request.json`, or `revision.json` unless the active Claude deployment is explicitly approved for that data. The application generation runtime remains local-only Ollama/vLLM, but that does not change Claude's own data boundary.

For chat-driven deck changes, use the filesystem contract only: read fresh `status/current.json`, resolve “this page” from `selection`, edit `request.json` for the initial run or `revision.json` for a follow-up, then call `scripts/local_private.ps1`. Never directly edit PPTX, XLSX, DeckSpec, MetricStore, stage output, embedded workbook, or chart XML. All revisions and Refresh operations must regenerate through `GenerationRequest → generate_deck() → renderer → T1`.
