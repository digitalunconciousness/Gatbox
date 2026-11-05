# The library format, v1

What the deck and, later, the archive both store and exchange. It is **Gatbox's own** and not part of contract v1:
the apps never see the writes or the merge. The **read side** (`read/`) is what an app needs to keep a read-only copy,
and each app vendors that directory byte-identical (see `read/README.md`). Both ends run `backend/gatboxlib/library.py`, so the format can't
drift; `library-vectors.json` is what any other implementation must reproduce. The design is
`docs/superpowers/specs/2026-10-28-shared-game-library-design.md`.

## A record

`(kind, id)`, a revision `rev` (1, 2, ...), the `hash` of its data, `deleted`, `at`, `by`, and `data`.
The hash is the SHA-256 of the data's canonical JSON: sorted keys, no whitespace, UTF-8, strings in NFC.

## Writes are compare-and-set

A write names the revision it replaces: `if_rev: 0` creates only if absent, `if_rev: n` replaces only revision `n`.
Each write carries a `uid`, minted once per edit. The result is one of:

| result | meaning |
|---|---|
| `created`, `updated` | written; the record has its new revision |
| `duplicate` | the uid was already applied, or the data is unchanged: nothing written |
| `conflict` | `if_rev` isn't the current revision: nothing written, the current record is returned |
| `rejected` | the store won't take it, with a reason: nothing written |

## The kinds

| kind | id | data |
|---|---|---|
| `game` | the `model_key` | `title, mfr, year, kind (video\|pinball\|other), notes, replaced_by` |
| `document` | `<model_key>/<doc_id>` | `model_key, doc_id, title, kind, source_url (https), source, sha256, pages, bytes` |
| `mame` | the `model_key` | `model_key, set, prefer, status (sure\|check\|null), why` |
| `rom_list` | the `model_key` | `model_key, mame (MAME's version), set, versions[{name, parent, description, year, mfr, roms[{name, size, crc, sha1, region, status, bios}], disks[{name, sha1, region, status}]}]`. Written only by the deck, from `mame -listxml`: hashes only, no ROM data |
| `rom_image` | the image's sha256 | `size, crc32, sha1, label, part, romident, model_key, chip, verdict (good\|bad\|unknown), revision, note` |
| `rail_limit` | 32 hex digits | `model_key, rail, lo, hi, doc_id, page, quote, confirmed (null \| {by, at})` |
| `fact` | 32 hex digits | `model_key, group, what, value, doc_id, page, quote` |

Every field is stored: an optional one that's left out takes its default. Unknown fields are refused.

## Rules the store enforces

- **A game comes before its children.** A document, MAME set, ROM list, limit or fact names its game by
  `model_key`; without that game the write is rejected.
- **Games are never deleted.** A mistaken duplicate retires with `replaced_by: <survivor>`, and readers follow it.
  Children are deleted with a tombstone (`deleted: true`).
- **`model_key`:** `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 60 characters, never `unassigned`. Made from the title the way
  the roster makes slugs; pinball keys start with `pin-`; a title that's taken gets the maker as a suffix, then the
  year. A key is permanent.
- **Volts** are decimal strings with no trailing zeros: `"4.75"`, `"5"`, `"-12"`.
- **A change to `lo`, `hi`, `doc_id` or `page` clears `confirmed`.** Confirming is a write that changes none of them.
- **Limits:** title 120, notes 2000, quote 500, what 60, value 120, why 500, source 200, `source_url` 500 (https),
  `rom_list` 2 MB.

## The feed and the digest

`changes(since, limit)` returns the records changed after a cursor, oldest first, with the next `cursor`, `more` and `head`
(the newest cursor the library has: a reader holding a cursor above it knows the library was rebuilt or restored from an
older copy, and starts again from 0).
A record changed twice shows once, at its latest revision. `digest()` is `{count, digest}` over every record,
tombstones included: two libraries with the same history agree. `content_digest()` is the same over `(kind, id, hash,
deleted)` with the revisions left out: the deck and the archive number a record's revisions independently, so they
compare this one (`GET /api/v1/library/digest?content=1`), and two libraries with the same content agree.
