"""Stand-in for the compiled system-audio helper: speaks the same stdout protocol.

usage: fake_tap.py RATE MODE
  loud    header + 0.5 s of a loud signal, then wait until stdin closes
  silent  header + 0.5 s of zeros, then wait until stdin closes
  split   like loud, but writes in chunks that cut samples in half
  crash   header + a little audio, then dies with an error on stderr
  fail    error on stderr and exit 1 without a header
  hang    never sends a header
"""

import struct
import sys
import time

rate, mode = int(sys.argv[1]), sys.argv[2]
out = sys.stdout.buffer


def write(data):
    out.write(data)
    out.flush()


if mode == "fail":
    sys.stderr.write("error: cannot create the audio tap (CoreAudio error 1852797029)\n")
    sys.exit(1)
if mode == "hang":
    time.sleep(30)

write(f"RATE {rate}\n".encode())
amplitude = 0 if mode == "silent" else 16384
audio = struct.pack("<h", amplitude) * (rate // 2)

if mode == "split":
    for start in range(0, len(audio), 4097):  # odd size: cuts int16 samples in half
        write(audio[start : start + 4097])
else:
    write(audio if mode != "crash" else audio[: rate // 2])

if mode == "crash":
    sys.stderr.write("error: the audio device disappeared\n")
    sys.exit(2)

sys.stdin.buffer.read()  # a real helper stops when the parent closes stdin
