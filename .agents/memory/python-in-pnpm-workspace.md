---
name: Python in the pnpm workspace
description: Environment notes for adding Python scripts and dependencies to this Node/pnpm workspace.
---

Use a Replit-managed Python Tools module that includes pip for Python scripts and dependencies. The base system interpreter may have no pip and reject installation because it is externally managed.

**Why:** The default interpreter could compile scripts but could not install `requests`; selecting a Python Tools module made package installation available without modifying the immutable system Python.

**How to apply:** Before installing Python packages in this workspace, check that the active interpreter comes from a Replit-managed Python module, then use the package-management flow and keep dependencies declared in `requirements.txt`.