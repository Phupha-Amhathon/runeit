#!/usr/bin/env python3
"""Regenerates the binary mock data used by TESTING.md (you never need to run this
to follow the test guide -- the .bin files are committed). Layout mirrors
pwd_table_t: 31 entries x (char name[16] + char password[32]) = 1488 bytes."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
N_ENTRIES, NAME_LEN, PW_LEN = 31, 16, 32
FILLER = "!@#$%^&*()-_=+[]{};:,.<>/?abcXYZ"


def entry(name: bytes, password: bytes) -> bytes:
    assert len(name) <= NAME_LEN and len(password) <= PW_LEN
    return name.ljust(NAME_LEN, b"\0") + password.ljust(PW_LEN, b"\0")


def full_entry(i: int) -> bytes:
    name = ("S%02d" % i + "abcdefghijkl").encode()          # 15 chars = max
    password = ("pw%02d" % i + FILLER[:27]).encode()        # 31 chars = max
    assert len(name) == 15 and len(password) == 31
    return entry(name, password)


def table(entries: dict) -> bytes:
    blank = b"\0" * (NAME_LEN + PW_LEN)
    return b"".join(entries.get(i, blank) for i in range(N_ENTRIES))


def write(fname: str, data: bytes) -> None:
    with open(os.path.join(HERE, fname), "wb") as f:
        f.write(data)


write("table_full.bin", table({i: full_entry(i) for i in range(N_ENTRIES)}))
write("table_sparse.bin", table({i: full_entry(i) for i in (0, 15, 30)}))
write("table_empty.bin", table({}))
write("table_holes.bin", table({
    3: entry(b"", b"orphan-password"),        # password but no name -> "unused"
    4: entry(b"nopass", b""),                 # name but empty password -> "used"
    5: entry(b"normal", b"pass5"),
}))
write("table_unterm.bin", table({
    0: b"X" * NAME_LEN + b"secretpw".ljust(PW_LEN, b"\0"),   # name has no NUL
}))
write("seq64.bin", bytes(range(64)))
