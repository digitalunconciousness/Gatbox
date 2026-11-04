"""The archive: the library's home, in its own container (phase D of the integration plan).

It runs the deck's own library code (backend/gatboxlib: the store, the compare-and-set writes, the change feed and the
file store), so the deck and the archive can't disagree about the format. See docs/plans/2026-11-04-phase-d-archive.md and
archive/RUNBOOK.md.
"""
