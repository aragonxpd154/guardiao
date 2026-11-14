#!/usr/bin/env python3
"""CRC usado nos manifests do firmware Harris NetVX (CRC-32/BZIP2)."""
import sys
def _mk():
    t=[]
    for i in range(256):
        c=i<<24
        for _ in range(8): c=((c<<1)^0x04c11db7)&0xffffffff if c&0x80000000 else (c<<1)&0xffffffff
        t.append(c)
    return t
T=_mk()
def crc(data):
    c=0xffffffff
    for b in data: c=((c<<8)&0xffffffff)^T[((c>>24)^b)&0xff]
    return c^0xffffffff
if __name__=='__main__':
    for f in sys.argv[1:]: print(f'0x{crc(open(f,"rb").read()):08x}  {f}')
