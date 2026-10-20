#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snapshot.py [tên-màn] — chụp màn hiện tại, in ra và lưu vào dumps/<tên>.txt (+ .json)

Chạy tool này mỗi khi sang 1 màn mới để tích luỹ dữ liệu build auto.
Nếu không truyền tên → lưu theo timestamp.
"""
import sys, os, json, time
import driver

HERE = os.path.dirname(os.path.abspath(__file__))
DUMPS = HERE + "/dumps"


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else time.strftime("screen-%Y%m%d-%H%M%S")
    dev, session, script, msgs = driver.connect()
    snap = script.exports_sync.snapshot()
    text = driver.pretty(snap)
    print(text)
    os.makedirs(DUMPS, exist_ok=True)
    with open("%s/%s.txt" % (DUMPS, name), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open("%s/%s.json" % (DUMPS, name), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print("\n>> đã lưu dumps/%s.txt và .json" % name)
    session.detach()


if __name__ == "__main__":
    main()
