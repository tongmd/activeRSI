#!/usr/bin/env python3
"""Synthetic contract checks for the offline tutorial, not a model benchmark."""
import json
import sys

request = json.load(sys.stdin)
harness = request["harness"]
required = request["case"]["input"]["required_text"]
result = all(fragment in harness for fragment in required)
json.dump({"passed": result, "safety_violation": False, "questions": 0, "cost": 0.0,
           "measurement": "synthetic text-contract fixture; no model was run"}, sys.stdout)
