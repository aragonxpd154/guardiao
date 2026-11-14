#!/bin/sh
# Monta cave.S para cada versão em ../guardiao/dados/obel1/versoes/*.json
# -> ../guardiao/dados/obel1/cave/<versão>.bin e .syms (usados pelo Guardião)
set -e
cd "$(dirname "$0")"
DADOS=../guardiao/dados/obel1
mkdir -p "$DADOS/cave" cave
for j in "$DADOS"/versoes/*.json; do
  v=$(basename "$j" .json)
  eval "$(python3 -c "
import json; d=json.load(open('$j'))
syms=dict(d['funcs']); syms['STR_DISK']=d['STR_DISK']; syms['STR_CURRENT']=d['STR_CURRENT']
print('DEFS=\"'+' '.join(f'--defsym={k}={v}' for k,v in syms.items())+'\"')
print('ADDR='+d['cave']); print('LIM='+str(d['cave_size']))")"
  powerpc-linux-gnu-as -mregnames -a32 -mbig -o cave/$v.o cave.S
  powerpc-linux-gnu-ld -m elf32ppc -Ttext=$ADDR -e _start $DEFS -o cave/$v.elf cave/$v.o
  powerpc-linux-gnu-objcopy -O binary -j .text cave/$v.elf "$DADOS/cave/$v.bin"
  powerpc-linux-gnu-nm -n cave/$v.elf | awk '$2=="t"{print $1, $3}' > "$DADOS/cave/$v.syms"
  rm -f cave/$v.o cave/$v.elf
  echo "$v: cave $(wc -c < "$DADOS/cave/$v.bin") bytes em $ADDR (limite $LIM)"
done
rmdir cave 2>/dev/null || true
