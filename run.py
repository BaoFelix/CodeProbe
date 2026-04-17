#!/usr/bin/env python3
"""
run.py — CodeProbe entry point
Usage: python run.py <command> [args]

Quick start:
  python run.py init                  Reset DB (fresh start)
  python run.py analyze test_src/     Full pipeline: scan → arch → resp → design
  python run.py report                Generate HTML report
"""
import sys
import os

# Ensure tool package is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tool.__main__ import main

if __name__ == '__main__':
    main()
