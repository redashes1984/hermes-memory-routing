# Hermes Memory Routing System

A robust, intelligent memory routing and management system for Hermes AI agents.

## Overview

This project implements a comprehensive memory routing architecture that enables AI agents to efficiently manage, categorize, and retrieve information. The system features automatic routing, auditing, and maintenance capabilities with advanced semantic search support.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Memory System                          │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐    ┌──────────────────┐                   │
│  │  MEMORY.md  │    │  USER PROFILE    │                   │
│  │  (Personal) │    │  (User Info)     │                   │
│  └──────┬──────┘    └──────────────────┘                   │
│         │                                                  │
│         ▼                                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Routing Layer                           │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐            │  │
│  │  │ Fast     │ │ LLM      │ │ Fallback │            │  │
│  │  │ Path     │ │ Review   │ │ Path     │            │  │
│  │  │ Score≥3  │ │ Score 1-2│ │ Score 0  │            │  │
│  │  └──────────┘ └──────────┘ └──────────┘            │  │
│  └──────────────────────────────────────────────────────┘  │
│         │                                                  │
│         ▼                                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                   Sub-docs Layer                      │  │
│  │  • infrastructure.md  • philosophy.md               │  │
│  │  • dev-log.md        • rules.md                     │  │
│  │  • milestones.md    • commitments.md                │  │
│  │  • hell-contract.md                                 │  │
│  └──────────────────────────────────────────────────────┘  │
│         │                                                  │
│         ▼                                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Audit & Monitoring                      │  │
│  │  • .audit.jsonl (Audit trail)                        │  │
│  │  • .fact_cache.json (Fact cache)                     │  │
│  └──────────────────────────────────────────────────────┘  │
│         │                                                  │
│         ▼                                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              Maintenance Tasks                       │  │
│  │  • Keyword auto-tuning (every 30min)                 │  │
│  │  • Memory idle replay (every 2h)                     │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## Features

### Core Functionality
- **Intelligent Routing**: Automatic classification and routing of memories based on keywords and context
- **Multi-path Processing**: Fast path, LLM review path, and fallback mechanisms
- **Comprehensive Auditing**: Full audit trail with scoring and tracking
- **Automated Maintenance**: Cron-based system maintenance and optimization

### Memory Management
- **Sub-document Organization**: 7 specialized sub-documents for different memory types
- **Fact Caching**: Efficient fact retrieval and storage system
- **Keyword Optimization**: Automatic keyword tuning for better routing accuracy
- **Security**: Base64 encryption and strict file permissions (600)

### Monitoring
- **Performance Metrics**: Real-time performance tracking (fast path rate, success rate)
- **Health Checks**: Comprehensive system health monitoring
- **Error Tracking**: Detailed error analysis and reporting

## Installation

### Prerequisites
- Hermes Agent environment
- Python 3.8+
- Git for version control

### Quick Setup
```bash
# Clone the repository
git clone https://github.com/redashes1984/hermes-memory-routing.git

# Navigate to the project
cd hermes-memory-routing

# Install dependencies (if any)
pip install -r requirements.txt

# Run initial setup
python setup.py
```

## Usage

### Basic Usage

```python
from memory_routing import MemorySystem

# Initialize the system
memory_system = MemorySystem()

# Save a new memory
memory_system.save_memory(
    target="memory",
    content="Important technical information",
    category="infrastructure"
)

# Search memories
results = memory_system.search_memories("infrastructure configuration")

# Get routing statistics
stats = memory_system.get_routing_stats()
```

### Configuration

#### Keyword Configuration
Configure routing keywords in `config/keywords.json`:
```json
{
  "infrastructure": ["server", "docker", "container", "network"],
  "philosophy": ["thoughts", "values", "beliefs"],
  "dev_log": ["code", "programming", "debugging"],
  "rules": ["rules", "guidelines", "standards"],
  "milestones": ["milestone", "achievement", "deployment"],
  "commitments": ["commitment", "promise", "goal"]
}
```

#### Cron Configuration
Configure maintenance tasks in `config/maintenance.yaml`:
```yaml
maintenance:
  keyword_tuning:
    frequency: "30 minutes"
    enabled: true
  memory_replay:
    frequency: "2 hours"
    enabled: true
```

## System Status

### Health Indicators
| Component | Status | Details |
|-----------|--------|---------|
| Storage | ✅ Good | 8% memory / 34% user |
| Auditing | ✅ Active | Real-time tracking |
| Sub-documents | ✅ Available | All 7 documents |
| Fact Cache | ✅ Active | 3 cached facts |
| Security | 🔐 Secure | 600 permissions |
| Cron Tasks | ⚠️ Partial | 1 task with errors |

### Performance Metrics
- Fast Path Rate: 80%
- LLM Review Rate: 20%
- Success Rate: 100%
- Error Rate: 0%

## Directory Structure

```
hermes-memory-routing/
├── docs/
│   ├── architecture.md
│   ├── configuration.md
│   └── monitoring.md
├── scripts/
│   ├── health_check.py
│   ├── audit_analyzer.py
│   └── maintenance.py
├── config/
│   ├── keywords.json
│   ├── maintenance.yaml
│   └── permissions.json
├── templates/
│   ├── sub-document-templates.md
│   └── audit-templates.json
├── tests/
│   └── test_routing.py
├── README.md
├── CHANGELOG.md
└── LICENSE
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contact

- Project: [GitHub Repository](https://github.com/redashes1984/hermes-memory-routing)
- Issues: [Report Issues](https://github.com/redashes1984/hermes-memory-routing/issues)

## Version

Current Version: v1.0.0
Last Updated: 2026-05-10
