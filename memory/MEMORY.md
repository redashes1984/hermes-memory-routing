# MEMORY.md — Index File (placeholder for project structure)

This file is part of the Hermes Memory Routing project. In a production environment, MEMORY.md serves as the top-level index injected into the system prompt on every turn.

## Directory Structure

```
~/.hermes/profiles/<profile>/
├── memories/                  # Hermes official directory
│   ├── MEMORY.md             # Index file (injected into system prompt)
│   └── USER.md               # User profile (injected into system prompt)
│
└── memory/                    # Memory-routing sub-documents
    ├── infrastructure.md     — infrastructure, deployment, hardware
    ├── philosophy.md         — values, principles, relationships
    ├── milestones.md         — milestones, version history
    ├── rules.md              — conventions, standards, workflows
    ├── commitments.md        — commitments, long-term promises
    └── dev-log.md            — changelog, iteration notes
```

## How It Works

1. The `memory_tool.py` routes new entries to the best-matching sub-document based on keywords/LLM scoring
2. MEMORY.md only contains the index block — core identity and navigation table
3. Sub-documents are read on-demand via `read_file` when needed

## In Production

- `~/.hermes/profiles/<profile>/memories/` — Official Hermes directory for MEMORY.md and USER.md (index files injected into system prompt)
- `~/.hermes/profiles/<profile>/memory/` — Memory-routing sub-documents (read on-demand via `read_file`)
- This file is a placeholder for demonstrating the project's expected directory structure
- For deployment examples, see `docs/design.md`
