# Contributing to hermes-memory-routing

## Before You Commit — Three Rules

### Rule 1: Imports at the Top

All imports must be at the top of the file, sorted alphabetically (isort).

**Exception:** Cross-platform compatibility (e.g., `try/except` for `fcntl`/`msvcrt`) and lazy-loading for optional dependencies (e.g., `urllib` in a try/except block) are allowed inside functions.

```python
# ✅ Correct — all imports at top
import json
import logging
import math
import os

def my_func():
    return math.sqrt(42)

# ❌ Wrong — import inside function
def my_func():
    import math
    return math.sqrt(42)
```

### Rule 2: No Dead Constants

Every constant must be referenced at least once. If you define `KEYWORD_FALLBACK = 0` but no code uses it, remove it.

**How to check:** `grep -rn "CONSTANT_NAME" src/` — if only the definition line appears, delete it.

### Rule 3: No Duplicate Branch Logic

If two `if/elif` branches return identical values, merge them:

```python
# ❌ Wrong
if score >= 0.6:
    return doc, raw
elif score >= 0.3:
    return doc, raw  # Same return as above
else:
    return None, 0

# ✅ Correct
if score >= 0.3:
    return doc, raw
return None, 0
```

## Automated Checks

### Pre-commit Hooks (Local)

Install once:
```bash
pre-commit install
```

After that, every `git commit` automatically runs isort, black, flake8, and pyupgrade. If any check fails, the commit is rejected.

Run manually on all files:
```bash
pre-commit run --all-files
```

### CI Checks (GitHub)

Every push to `main` and every PR triggers the "Code Quality" workflow:
- **isort**: Checks import ordering
- **black**: Checks code formatting
- **flake8**: Catches unused imports (F401), unused variables (F841), duplicate keys (E401)
- **pyupgrade**: Flags outdated Python syntax

If CI fails, fix the issues and push again. No merge without green CI.

## File Structure Convention

```
src/                    # Core library code
scripts/                # Standalone scripts (cron jobs, maintenance)
.github/workflows/      # CI pipelines
.pre-commit-config.yaml # Pre-commit hook configuration
```

## Questions?

If you're unsure whether something is allowed, ask in a PR or check with the team. It's better to ask than to create technical debt.
