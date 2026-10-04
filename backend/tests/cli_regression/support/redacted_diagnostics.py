"""Redact disposable-process diagnostics before the CI publication boundary."""
from pathlib import Path
import re


def redact(text: str) -> str:
    text = re.sub(r'([?&](?:t|token|access_token)=)[^\s"\'\\&<>}]+', r'\1<REDACTED>', text)
    text = re.sub(r'(Bearer\s+)[^\s"\'\\<>]+', r'\1<REDACTED>', text, flags=re.I)
    text = re.sub(r'((?:Cookie|Set-Cookie):\s*)[^\r\n"\\]+', r'\1<REDACTED>', text, flags=re.I)
    return re.sub(r'("(?:access_token|refresh_token|accessToken|refreshToken|password)"\s*:\s*")[^"<>]*(")',
                  r'\1<REDACTED>\2', text, flags=re.I)


def archive_text(source: Path, destination: Path) -> None:
    destination.write_text(redact(source.read_text(encoding="utf-8")), encoding="utf-8")
