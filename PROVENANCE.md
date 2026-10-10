---
title: "Provenance and Clean-Room Statement: talon"
updated: 2026-09-29
weaver_hash: f0da8b4c38290144e5c34d9c6158f01483a7f43a32eab023307cab6072c91b09
weaver_imported: 2026-10-09T22:22:08+00:00
---
# Provenance and Clean-Room Statement: talon

**Component:** talon 0.1.0  
**Licensor:** Juan Carlos De Santiago  
**Licence:** PolyForm Small Business 1.0.0 **or** PolyForm Noncommercial 1.0.0 (`LICENSING.md`).  
**Disclosure:** Published as defensive prior art; no patents filed (`TECHNICAL-DISCLOSURE.md`).

---

## 1. Clean-Room Provenance

`talon` was designed and authored by Juan Carlos De Santiago and the Sovereign Raptor fleet agents. It grew from the terminal-launcher project (`projects/terminal-launcher`) to furnish a clean, non-bloated, local-first agent runner that decouples agent execution from heavy external cloud frameworks.

No proprietary code was referenced or incorporated. All model interaction loops, session stores, and role configurations were implemented cleanly using Python standard libraries and open interface conventions.

---

## 2. Dependencies

| Component | Role | Licence | Shipped? |
|---|---|---|---|
| Python 3 (3.10+) | Core execution runtime | PSF-2.0 | No (runtime) |
| SQLite3 | Session database storage | Public Domain | No (runtime) |
| `pytest` | Test suite execution | MIT | No (development only) |
| `omnitool` | Batch tool execution hands | PolyForm SB / NC | No (independent release) |

---

## 3. Excluded Fleet Artifacts

| Artifact | Reason for Exclusion |
|---|---|
| `roles/*/bin/*` symlinks | Absolute symlinks pointing into the fleet's local `bin` |
| `src/role_shell.py.backup` | Scratch development backup |
| `~/.config/talon/keys.json` | Local user secret API keys |
| `talon.db` | Operational local session histories |
| Fleet-only telemetry tools (`bin/talon-telemetry*`) | Internal cluster metrics reporters |
