# Project conventions

## Language: English only

**All code, comments, docstrings, log messages, documentation and commit messages
must be written in English.** This applies to new files and to any line you touch
in an existing one — no exceptions, in any Claude session.

Parts of this repository still contain Romanian comments: they are legacy, and an
active migration is translating them (`verify_tools/translation_audit.py`,
`docs/COMMENT_TRANSLATION_REMAINING.md`). **Do not imitate them.** Matching the
surrounding style is the usual rule, but here it works against the migration and
adds new debt to it.

Useful commands:

```bash
verify_tools/translation_audit.py scan            # what still looks Romanian
verify_tools/translation_audit.py verify <file>   # prove an edit touched prose only
```

Romanian text is expected in exactly two places, and must stay there:

- **user-facing runtime output** the operator reads on the phone or in a terminal
  (ntfy alert bodies, banners printed by the start scripts) — translating those
  changes what the operator sees, which is a behaviour change, not a cleanup;
- **argparse help strings reused as CLI text** — `translation_audit.py scan` flags
  these explicitly so they are not translated as if they were ordinary prose.

## Line endings

LF everywhere, enforced by `.gitattributes` (`* text=auto eol=lf`). The repo runs
on Linux but is often edited from Windows, where `core.autocrlf=true` silently
introduces CRLF. A single `\r` in a `.sh` file makes it unrunnable on the server
(`bad interpreter`), and in `.conf`/`.env` it corrupts values read with `cut`/`grep`.

Binary files (`.h5`, `.mp3`, `.wav`, images, archives) are marked `binary` because
they contain legitimate `0x0D` bytes that line-ending normalisation would corrupt.

## Deployment

Edit locally, commit, push to `main`, then `git pull --ff-only` on the server. No
restart mechanism ever pulls code, so a single pull is enough: every supervisor
(systemd, healthcheck) relaunches from what is already on disk.

See `systemd/PIA.md` for the VPN, and `systemd/README.md` for rebuilding PROD.
