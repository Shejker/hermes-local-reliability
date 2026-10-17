#!/usr/bin/env python3
"""Standalone CLI for the version-aware verified-headroom extension."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reliability_layer.installer import cli
raise SystemExit(cli())
