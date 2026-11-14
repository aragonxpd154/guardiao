# Patch obel1: diagnóstico e procedimento de teste

## 1. O que foi encontrado no firmware da SYS

Os defeitos abaixo existem **igualmente nas versões 8.4.7, 8.4.8 e 8.6.2-02**. As funções
de gravação, carga, reset e montagem do disco não mudaram entre elas. Na 8.6.2 só foi
acrescentado o "merge" de configuração. Os endereços da tabela são os da 8.4.7.

| # | onde (sysa12/vxworks) | defeito | efeito |
|---|---|---|---|
| 1 | `Db_Sc_Cfg_Load` 0x15192c | Volta a árvore `cfg` inteira para o padrão de fábrica e só depois lê `/d/etc/cfg/current`. Não confere cabeçalho, tamanho nem integridade. Em `Db_Sc_Init`, o erro da leitura é ignorado. | Um `current` truncado ou corrompido faz a SYS subir zerada. |
| 2 | `Db_Sc_Cfg_Save` 0x151728 | Grava `cfgNNN.tmp`, fecha e renomeia para `current`, sem flush. O disco usa um cache de escrita de 128 KB em RAM (`dcacheDevCreate`), e o FAT é montado sem verificação automática. | Durante alguns segundos após cada gravação, a config ainda está só na RAM. |
| 3 | `Db_Sc_App_Reset` 0x14dbc0 | Pede o reset do chassi à CIB (`Sc_CibResetSys`) **antes** de `OS_Reboot`, e só `OS_Reboot` desmonta (e assim descarrega) o disco. | Num reset por comando ou numa troca de master, o cache pode se perder e o FAT fica inconsistente. |
| 4 | `mountDisk` 0x1e38c | Se `/d` não abre no boot, chama `dosFsVolFormat`. | Um CF com problema é **apagado** sozinho ("Disk erased"). |
| 5 | sincronização master→slave | A master empurra os arquivos de `/d/etc` para a slave (`slave_cfg_sync`), e a slave apaga o que não existe na master. | Uma SYS que sobe zerada e vira master sobrescreve a cópia boa da reserva. Por isso o problema aparece com a reserva. |

Cartões CF gastos aumentam muito a chance dos itens 1, 2 e 4. Por isso a troca por CF
industrial SLC faz parte da solução.

## 2. O que o patch obel1 muda

- **P1:** FIOSYNC (FAT + cache) antes de pedir o reset à CIB.
- **P2:** ao salvar o `current`, a versão anterior vira `current.bak` (só se estiver
  íntegra), seguida de FIOSYNC. Os saves em slot numerado (`util save N`) não mudam.
- **P3:** ao carregar o `current` (no boot e na troca slave→master), a SYS confere o
  arquivo: cabeçalho `#cfg=`, nenhum byte nulo, pelo menos 64 bytes, termina em `\n`,
  leitura sem erro.
  - Arquivo ruim e `.bak` bom: carrega o `.bak`.
  - Os dois ruins: faz o mesmo que o firmware original, então nunca fica pior.
- **P4:** não formata mais o CF sozinho. A SYS para com erro no console para diagnóstico.

Não muda bootrom, módulos, FPGAs nem a interface. A recuperação continua pelo
bootrom + `Upgrade.exe`, como hoje.

### Verificação já feita (sem hardware)

- O código PowerPC real da imagem corrigida rodou num emulador (Unicorn) com um
  sistema de arquivos simulado: **24 de 24 cenários passaram** nas três versões. Os
  originais da Harris, nos mesmos testes, falham em 11, o que reproduz os defeitos.
- O `current` real de uma SYS em 8.6.2 (6000 linhas) passa na validação. Truncado
  pela metade ou com 4 KB zerados, ele é rejeitado.
- Só os bytes esperados mudaram: 4 instruções e o bloco de código novo.
- Os CRCs de todos os arquivos dos dois manifests conferem.

**O que o emulador não testa:** o comportamento real do dosFs/ATA com o seu CF, a CIB
e a sincronização entre as duas SYS. Isso só se confirma na bancada.

## 3. Antes de tudo: validar o critério com os arquivos reais

1. Instale o **Guardião** ([`guardiao`](../guardiao)) e faça `coletar` nas SYS de produção.
2. Os `current` que vocês sabem que estão bons precisam aparecer como `[OK] … íntegro`.
3. Se algum arquivo bom aparecer como **RUIM**, **não instale o patch**. Nesse caso o
   critério não bate com o formato real, e o arquivo precisa ser analisado antes.

## 4. Bancada

Pré-requisitos:
- uma SYS fora de produção, de preferência num chassi de bancada **sem a segunda SYS**
  (com reserva, a master tende a sincronizar o firmware dela na outra);
- acesso ao console/craft;
- o pacote original `NetVx_Firmware_V8_4_7` à mão;
- **um clone do CF dessa SYS** (feito com o Arca), para voltar trocando o cartão.

1. Use o **assistente do Guardião**: ele gera a imagem a partir do próprio cartão, envia,
   confere e faz o bind. Também dá para gerar offline com `guardiao patch` e instalar com o
   `Upgrade.exe`.
2. Acompanhe o boot no console. No Guardião deve aparecer
   `firmware SYS: <versão> + patch obel1`.
3. **Gravação:** altere um parâmetro qualquer e espere a SYS salvar. O Guardião deve
   mostrar `.bak: sim`.
4. **Reset:** execute `util reset`. A config deve voltar intacta.
5. **Corrupção simulada:** por FTP, troque `/d/etc/cfg/current` por um arquivo com
   uma linha só, por exemplo `#cfg=teste`. Logo em seguida execute `util reset`.
   - Esperado: a SYS sobe com a config do `.bak`.
   - Esperado no log (`guardiao.py logs <sys>`): `OBEL: /d/etc/cfg/current invalid, trying .bak`.
6. **Corte de energia:** desligue a fonte logo depois de uma alteração de config, repita
   algumas vezes e confira a config em cada boot.
7. **Estabilidade:** deixe rodando 48 h. Os logs não podem ter `system CRC error`,
   `Exception` ou `FATAL`.

## 5. Produção

1. Troque os CFs das duas SYS por CF industrial (SLC), clonando com o Arca.
2. Com o Guardião já rodando e com snapshots bons:
   - carregue a imagem nas **duas** controladoras: pelo `Upgrade.exe`, uma por IP,
     fazendo o bind nas duas e o reset pelo slot 1 (procedimento das release notes 8.6.2);
   - ou, numa janela de manutenção, grave direto nos dois cartões antes de religar.
   Nunca deixe master e reserva com imagens diferentes: a reserva tende a copiar o
   firmware da master.
3. Confirme no Guardião que as duas mostram `<versão> + patch obel1` e o mesmo `current`.

## 6. Rollback

Escolha um dos caminhos:
- reinstale o pacote original com o `Upgrade.exe` (o patch não mexe no bootrom, que continua aceitando a reinstalação);
- recoloque o CF clonado.

O `current.bak` que fica no cartão é ignorado pelo firmware original.

**Nunca copie `etc/` de um backup para o cartão para "instalar" o patch.** A config
vigente é a que está no cartão.

## 7. Validação em campo: Morro do Céu (14/11/2026)

Chassi `#11 CEU`, duas SYS-A22 na 8.6.2-02 original, notebook Zorin ligado direto na
porta da master. Sequência feita à mão, a mesma que o assistente executa:

1. **Backup:** `get /d/etc/cfg/current` (271 KB, 8348 linhas) e `util save 90`.
2. **Envio:** `mirror -R` de `boot/v8-6-2.ob1` (91 arquivos, 34.503.429 bytes) e de `v8-6-2.ob1`.
   O `vxworks` lido de volta saiu idêntico. Os avisos de `SITE CHMOD` do lftp são
   inofensivos.
3. **Bind:** `util firmware v8-6-2.ob1`. A `V8-6-2.02` virou a anterior e a `V8-4-8` saiu do
   cartão.
4. **Reserva:** ao ser encaixada, sincronizou da master, reiniciou e subiu verde com
   `version = v8-6-2.ob1`. Essa foi a primeira prova de boot da imagem corrigida.
5. **Failover recusado:** `No backup available for slot 1`. A config tinha
   `slot.1 protMode=protectedBy-2`, mas `slot.2 type=none`: a proteção estava pela
   metade, o tipo de estado deixado por uma perda parcial de config.
6. **Ativação:** `util reset sys`. As duas SYS voltaram na `v8-6-2.ob1`, sem falha.
7. **Proteção:** `cfg slot.2/gen type=sys-a22-std`. O `protMode` do slot 2 é só leitura.
   Depois disso, o slot 1 mostrou `protAvail = available`.
8. **`.bak` em campo:** apareceu `current.bak` (271.375 bytes, a config anterior) ao lado
   do novo `current` (311.712 bytes).
9. **Failover:** `util failover slot.1`. No slot 2, a config ficou inteira (9701 linhas) e
   o slot 2 também gerou o seu `.bak`.
10. **Failback:** **`util failback slot.1`**. O manual de 2011 diz `slot.2`, mas a SYS
    responde `slot 2 is not configured for protection`.

Duas lições que o assistente já aplica:
- **Rótulo da config:** o rótulo passado no `util save N rótulo` vira o rótulo da config
  ativa. O assistente usa o rótulo atual.
- **IP do sistema:** o IP acompanha a SYS no comando. Com o notebook ligado direto numa
  porta, é preciso trocar o cabo de porta no failover e no failback.

## 8. Opcional: chkdsk ao montar (P5)

`netvx_sys_patch.py --chkdsk` gera uma variante que verifica e repara o FAT ao montar
o CF. Ela não vem neste pacote. Avalie só depois de validar o obel1, porque deixa o
boot mais lento e o reparo pode descartar cadeias de clusters corrompidas.
