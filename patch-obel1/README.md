# patch-obel1 — fonte do patch da SYS

O Guardião aplica o patch sozinho (assistente ou `guardiao patch`). Esta pasta é
para quem quer **auditar ou reconstruir** o código que vai dentro da SYS.

| arquivo | função |
|---|---|
| `cave.S` | código novo em assembly PowerPC, comentado |
| `gen_versao.py` | lê uma imagem `sysa12/vxworks` e gera o JSON de endereços da versão (por símbolos e padrões, tudo conferido) |
| `build_cave.sh` | monta `cave.S` para cada versão e grava em `../guardiao/dados/obel1/cave/` |
| `netvx_sys_patch.py` | atalho para `guardiao patch` (modo offline) |
| `teste/emu_test.py` | executa o código PowerPC real da imagem corrigida num emulador (Unicorn), com um sistema de arquivos simulado |

## Onde o patch mexe

| | função original | mudança |
|---|---|---|
| P1 | `Db_Sc_App_Reset` | `bl taskDelay` → `obel_sync_delay`: FIOSYNC no disco antes do reset pela CIB |
| P2 | `Db_Sc_Cfg_Save` | `bl OS_Rename` → `obel_cfg_commit`: `current` → `current.bak` (se íntegro), tmp → `current`, FIOSYNC |
| P3 | `Db_Sc_Cfg_Load_Current` | `bl Db_Sc_Cfg_Load` → `obel_cfg_load`: valida o `current`; se estiver ruim, carrega o `.bak` (com os dois ruins, faz o mesmo que o original) |
| P4 | `mountDisk` | `bl dosFsVolFormat` → `li r3,-1`: não formata o CF sozinho |
| P5 (opcional) | `usrAtaConfig` | `li r6,-1` → `li r6,2`: chkdsk com reparo ao montar |

Critérios de "config íntegra" (iguais no Guardião): começa com `#cfg=`, nenhum byte
nulo, ≥ 64 bytes, termina em `\n`, leitura sem erro.

O código novo (756 bytes) ocupa `ada_demangle`, função do demangler C++ (764 bytes) que
nenhum ponto do firmware chama — `gen_versao.py` confere que nenhum desvio aponta para lá.

| versão | vxworks original | com obel1 | obel1 + chkdsk |
|---|---|---|---|
| 8.4.7    | `0x3016f665` | `0xc00f6bec` | `0x06c408f9` |
| 8.4.8    | `0x5f22cb81` | `0x193c7811` | `0x44384ab6` |
| 8.6.2-02 | `0xb5d1a962` | `0xd99fef0d` | `0x8f20c3f2` |

## Reconstruir e testar

```bash
sudo apt install binutils-powerpc-linux-gnu binutils-multiarch && pip install unicorn
python3 gen_versao.py <cartão>/boot/v8-6-2.02/sysa12/vxworks > ../guardiao/dados/obel1/versoes/v8-6-2.02.json
./build_cave.sh                                   # reproduz byte a byte os .bin do pacote
python3 netvx_sys_patch.py <cartão> /tmp/corrigido
python3 teste/emu_test.py /tmp/corrigido/boot/v8-6-2.02/sysa12/vxworks    # 24/24
python3 teste/emu_test.py <cartão>/boot/v8-6-2.02/sysa12/vxworks          # original: falha em 11
```
