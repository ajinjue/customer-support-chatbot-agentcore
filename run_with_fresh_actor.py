#!/usr/bin/env python3
"""Run a starter script with per-session memory isolation (see fresh_actor.py).

    python run_with_fresh_actor.py chat.py
    python run_with_fresh_actor.py generate-eval-dataset.py --tests-json harness-tests.json
"""

import runpy
import sys

import fresh_actor  # noqa: F401  (registers the boto3 hook)

if len(sys.argv) < 2:
    sys.exit(__doc__)
script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(script, run_name="__main__")
