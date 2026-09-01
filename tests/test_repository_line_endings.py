from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".conf", ".csv", ".env", ".html", ".ini", ".json", ".md", ".ps1",
    ".py", ".service", ".sh", ".toml", ".txt", ".yaml", ".yml",
}


def _tracked_text_files():
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT,
    ).decode("utf-8")
    for relative in output.split("\0"):
        if not relative:
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {
            ".gitattributes", ".gitignore", "submit",
        }:
            yield path


def test_tracked_text_files_use_unix_line_endings():
    offenders = [
        str(path.relative_to(ROOT))
        for path in _tracked_text_files()
        if b"\r" in path.read_bytes()
    ]
    assert offenders == [], f"tracked text files contain CR/CRLF: {offenders}"
