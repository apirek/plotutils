#!/usr/bin/env -S python3 -B -OO

# iir - Apply Infinite Impulse Response to CSV data
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
from argparse import ArgumentParser


signal.signal(signal.SIGINT, lambda signum, frame: sys.exit())

argparser = ArgumentParser(description="Apply Infinite Impulse Response to CSV data")
argparser.add_argument("-d", "--delimiter", default="\t",
        help="field delimiter")
argparser.add_argument("-f", "--field", action="extend", nargs="+", dest="fields")
argparser.add_argument("-n", "--num", required=True, type=int)
args = argparser.parse_args()
delimiter = args.delimiter
#to_slice = lambda field: slice(*[int(arg) if arg else None for arg in field.split(":")])
def to_slice(field):
    slice_ = slice(*[int(arg) if arg else None for arg in field.split(":")])
    if slice_.start is None and slice_.stop is not None:
        slice_ = slice(slice_.stop, slice_.stop + 1)
    return slice_
slices = [to_slice(field) for field in (args.fields if args.fields else [":"])]
n = args.num
assert 1 <= num

avgs = None
for line in sys.stdin:
    fields = line.split(delimiter)
    fields = [field for slice in slices for field in fields[slice]]
    values = list(map(float, fields))
    if avgs is None:
        avgs = list(values)
    for i, field in enumerate(fields):
        avgs[i] = ((avgs[i] * n) - avgs[i] + values[i]) / n
    line = delimiter.join(map(str, avgs))
    # https://docs.python.org/3/library/signal.html#note-on-sigpipe
    try:
        print(line, flush=True)
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(32) # EPIPE
