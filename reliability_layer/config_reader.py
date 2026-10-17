"""Strict no-external-dependency parser for whitelisted flat YAML scalars.

This is NOT a full YAML parser. Reject ambiguous constructs rather than guessing.
"""
from pathlib import Path
import re


def check_config(path: Path) -> None:
    # Only the exact known config layout is supported; never print values.
    if not path.is_file():
        raise ValueError("Hermes config missing")
    current = None
    seen = {}
    for raw in path.read_text(encoding="utf8").splitlines():
        line = raw.partition("#")[0].rstrip()
        if not line.strip():
            continue
        m = re.fullmatch(r"( *)([\w.-]+):(?:\s+(.*))?", line)
        if not m:
            continue
        indent, key, val = len(m[1]), m[2], (m[3] or "").strip().strip("'\"")
        if indent == 0:
            current = key
        elif indent > 0 and current in {"model", "compression"}:
            field = current + "." + key
            if field in {"model.context_length", "model.ollama_num_ctx", "model.base_url",
                         "compression.enabled", "compression.threshold_tokens"}:
                if field in seen:
                    raise ValueError("Ambiguous repeated config field: " + field)
                seen[field] = val
    expected = {"model.context_length": "65536", "model.ollama_num_ctx": "65536",
                "model.base_url": "http://127.0.0.1:11434/v1", "compression.enabled": "true",
                "compression.threshold_tokens": "40960"}
    for key, value in expected.items():
        if seen.get(key) != value:
            raise ValueError("Required config value not confirmed: " + key)
