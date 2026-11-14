# Guardião NetVX 1.0.0

Vigia a configuração das SYS (system controller) do Harris NetVX. Ele puxa por
FTP o que está gravado no cartão CF, confere se está íntegro, guarda um
histórico e avisa quando a config some ou é zerada. Também restaura uma versão boa.

Usa só o Python 3 que já vem no Zorin OS, sem dependências.

## Instalação

```bash
git clone https://github.com/aragonxpd154/guardiao.git
mkdir -p ~/guardiao-netvx && cp guardiao/guardiao/guardiao.py guardiao/guardiao/guardiao.ini.exemplo ~/guardiao-netvx/
cd ~/guardiao-netvx && cp guardiao.ini.exemplo guardiao.ini && chmod 600 guardiao.ini
nano guardiao.ini            # IPs das duas SYS, usuário/senha do FTP
python3 guardiao.py coletar  # primeira coleta
```

Rodar sozinho a cada 10 min (timer do systemd do usuário):

```bash
mkdir -p ~/.config/systemd/user && cp guardiao/guardiao/systemd/* ~/.config/systemd/user/   # a partir da pasta do clone
systemctl --user daemon-reload && systemctl --user enable --now guardiao-netvx.timer
loginctl enable-linger $USER     # continua rodando sem sessão aberta
```

## Comandos

| comando | o que faz |
|---|---|
| `guardiao.py coletar` | baixa `etc/cfg/*`, `umconfig.txt`, `snmpd.cnf`, `brand`, `profile/*` de cada SYS, valida e guarda snapshot (só quando muda). Também mostra a versão do firmware da SYS (original ou com o patch obel1). |
| `guardiao.py listar sys-slot1` | histórico com status de cada snapshot |
| `guardiao.py logs sys-slot1` | baixa os últimos logs de `/d/log/sys` e filtra os eventos de config, redundância e disco |
| `guardiao.py restaurar sys-slot1 [--snapshot ID] [--slot 9] [--carregar]` | envia o último `current` íntegro, ou o do snapshot indicado, para o slot de configuração N da SYS e confere lendo de volta. Com `--carregar`, dispara `utilCmdLoad` por SNMP. |

Códigos de saída: `0` tudo ok, `2` alerta (config ruim ou zerada), `1` erro de coleta.

## O que gera alerta

- `current` ausente, vazio, sem o cabeçalho `#cfg=`, com bytes nulos (sinal de cluster
  perdido no CF), menor que 64 bytes ou ilegível. São os mesmos critérios do patch obel1.
- Config que encolheu mais que `queda_linhas_pct` (padrão 30%) em relação ao último
  snapshot bom. É o sintoma de "zerou as configs".
- Aviso separado quando o `current` da master e o da reserva diferem.

O alerta chama o comando definido em `alerta_cmd` com a mensagem em `$GUARDIAO_MSG`.
Pode ser `notify-send`, um trap SNMP para o painel de monitoramento, e-mail etc.

## Restauração: por que vai para um slot, e não direto no `current`

Com a SYS rodando, sobrescrever o `current` por FTP não adianta. A SYS regrava esse
arquivo a partir do banco em memória, e a reserva sincroniza da master. O caminho
nativo e seguro é este:

1. O Guardião envia a config boa para um slot numerado (padrão 09) e confere.
2. Você carrega o slot na master: `util load 9` (console/telnet), pela tela de carga de
   configuração na web, ou com `--carregar` (SNMP `utilCmdLoad`).
3. A master grava o novo `current` e a reserva sincroniza dela.

O conteúdo que já existia no slot é salvo localmente antes de ser sobrescrito.

## Observações

- O caminho do disco no FTP da SYS é detectado sozinho (`/d/...` ou relativo).
- Se o FTP da SYS não aceitar modo passivo, use `passivo = nao` no `guardiao.ini`.
