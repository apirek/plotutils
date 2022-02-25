#!/usr/bin/env -S python3 -B -OO

# replay - Replay time-series CSV data
# Copyright (C) 2022  Axel Pirek
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import argparse
import signal
import sys
import time
from argparse import ArgumentParser
from datetime import datetime


signal.signal(signal.SIGINT, lambda signum, frame: sys.exit())

argparser = ArgumentParser(description="Replay time-series CSV data")
argparser.add_argument("-d", "--delimiter", default="\t",
        help="field delimiter")
argparser.add_argument("-f", "--timefmt", default="%Y-%m-%d %H:%M:%S.%f")
argparser.add_argument("files", nargs="+")

args = argparser.parse_args()
delimiter = args.delimiter
timefmt = args.timefmt
prev_timestamp = None

_enter = time.monotonic()
for file in args.files:
    with open(file) as f:
        for line in f:
            fields = line.removesuffix("\n").split(delimiter)
            try:
                timestamp = datetime.strptime(fields[0], timefmt)
            except ValueError as e:
                print(f"Error: {e}, line: {line}, file: {file}", file=sys.stderr)
                continue
            now = datetime.now()
            line = delimiter.join([now.strftime(timefmt)] + fields[1:])
            if prev_timestamp is not None:
                delay = (timestamp - prev_timestamp).total_seconds()
                _exit = time.monotonic()
                time.sleep(max(delay - (_exit - _enter), 0))
            prev_timestamp = timestamp
            _enter = time.monotonic()
            # https://docs.python.org/3/library/signal.html#note-on-sigpipe
            try:
                print(line, flush=True)
            except BrokenPipeError:
                sys.stderr.close()
                sys.exit(32) # EPIPE
