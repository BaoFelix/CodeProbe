#!/usr/bin/env python3
"""
run.py — CodeProbe entry point
Usage: python run.py <command> [args]

Quick start:
  python run.py init                  Reset DB (fresh start)
  python run.py analyze test_src/     Full pipeline: scan → design review
  python run.py audit test_src/       Architecture audit (no LLM key needed)
  python run.py report                Generate HTML report
"""
import sys
import os

# Ensure tool package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool.__main__ import main

if __name__ == '__main__':
    main()
