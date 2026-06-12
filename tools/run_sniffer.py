#!/usr/bin/env python3
"""Daemonize sniff_auth.py so it survives parent shell exit."""
import os, sys, subprocess

if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    sys.exit(0)

os.chdir('/home/mino/tasi-exec')
with open('/tmp/sniff_live.log', 'w', buffering=1) as log:
    subprocess.run(
        ['python3', '-u', '/home/mino/tasi-exec/sniff_auth.py'],
        stdout=log, stderr=log
    )
