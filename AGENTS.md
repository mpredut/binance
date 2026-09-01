# Repository language policy

- Write every Git commit subject and commit body in English.
- Write all new or edited source-code comments and docstrings in English.
- Write all new or edited repository documentation in English.
- Do not introduce Romanian prose into tracked files. Preserve non-English runtime
  strings only when the task explicitly requires them or changing them would alter
  a user-facing compatibility contract.
- When editing an existing file, translate any Romanian comments or docstrings in
  the touched section into accurate English without changing executable behavior.
- Before committing, inspect the staged diff for Romanian comments, docstrings,
  documentation, and commit text. Correct every such occurrence before the commit.
- Use English for branch names, release notes, change summaries stored in the
  repository, and all other Git-authored metadata.
- Keep unrelated user files and uncommitted changes out of commits.
- Push completed, verified commits to the configured upstream branch unless the
  user explicitly asks to keep the work local.
