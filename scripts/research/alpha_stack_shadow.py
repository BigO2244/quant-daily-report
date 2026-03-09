#!/usr/bin/env python3
"""
Alpha Stack Shadow Runner — CLI Entrypoint
==========================================
Wrapper script for running Alpha Stack in shadow mode.

Usage:
    python scripts/alpha_stack_shadow.py [--date YYYY-MM-DD] [--enable]

For scheduled runs (GitHub Actions):
    python scripts/alpha_stack_shadow.py

For local testing (force-enable flags):
    python scripts/alpha_stack_shadow.py --enable --date 2024-06-01
"""

import sys
import os

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alpha_stack.research.shadow_runner import main

if __name__ == "__main__":
    main()
