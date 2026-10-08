#!/usr/bin/env python3
"""Build only the native engine used by C05 (Linux GCC/G++ and OpenMP)."""
import argparse
import json
from build_v05 import build

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sanitize", action="store_true", help="Enable undefined-behavior diagnostics")
    args = parser.parse_args()
    print(json.dumps(build(sanitize=args.sanitize), indent=2))
