# Project conventions

## Language: English only

**All code, comments, docstrings, log messages, documentation and commit messages
must be written in English.** This applies to new files and to any line you touch
in an existing one — no exceptions, in any Claude session.

The migration is done: the live code, the shell scripts, the tests and the documentation
are English. What remains in Romanian is a short, deliberate list — ntfy alert bodies
that reach the operator's phone, the detection vocabulary inside
`verify_tools/translation_audit.py`, and a few identifiers such as `prag`. Anything else
in Romanian is a regression; **do not imitate it**, translate it.

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
