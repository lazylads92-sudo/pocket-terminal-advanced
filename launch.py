#!/usr/bin/env python3
"""Launcher for Pocket Terminal Advanced."""
import sys, os

# Add parent directory to path so the package is findable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from pocket_terminal_advanced.main import main
main()
