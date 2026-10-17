#!/usr/bin/env python3
"""Read-only health check for Hermes Desktop, Ollama, compression, Guard and source."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reliability_layer.healthcheck import cli
raise SystemExit(cli())
