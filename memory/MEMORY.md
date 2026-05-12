# MEMORY.md — Index File (placeholder for project structure)

This file is part of the Hermes Memory Routing project. In a production environment, MEMORY.md serves as the top-level index injected into the system prompt on every turn.

## Structure

```
MEMORY.md (§-delimited index, ~2200 chars max)
│
├── memory/infrastructure.md
├── memory/philosophy.md
├── memory/milestones.md
├── memory/rules.md
├── memory/commitments.md
└── memory/dev-log.md
```

## How It Works

1. The `memory_tool.py` routes new entries to the best-matching sub-document based on keywords/LLM scoring
2. MEMORY.md only contains the index block — core identity and navigation table
3. Sub-documents are read on-demand via `read_file` when needed

## In Production

- Actual MEMORY.md lives in `~/.hermes/profiles/<profile>/memory/`
- This file is a placeholder for demonstrating the project's expected directory structure
- For deployment examples, see `docs/design.md`
