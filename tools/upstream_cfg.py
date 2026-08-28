#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""upstream-diff.sh / upstream-check.sh 用の設定読み取りヘルパー。
  python tools/upstream_cfg.py root   -> 上流のローカルパス（local.json の source_root）
  python tools/upstream_cfg.py last   -> manifest.json の last_synced_commit
  python tools/upstream_cfg.py paths  -> manifest.json の mappings[].src を1行ずつ
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
what = sys.argv[1] if len(sys.argv) > 1 else "root"

if what == "root":
    p = os.path.join(ROOT, "upstream", "local.json")
    if os.path.exists(p):
        print(json.load(open(p, encoding="utf-8")).get("source_root", ""))
    else:
        print("")
else:
    man = json.load(open(os.path.join(ROOT, "upstream", "manifest.json"), encoding="utf-8"))
    if what == "last":
        print(man.get("last_synced_commit", ""))
    elif what == "paths":
        for m in man["mappings"]:
            print(m["src"])
