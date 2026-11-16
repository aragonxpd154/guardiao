# Histórico de versões

<p align="center">
  <img src="https://raw.githubusercontent.com/aragonxpd154/guardiao/main/assets/linha-do-tempo.png" alt="Linha do tempo: diagnóstico do firmware, 1.0.0 Guardião NetVX, 2.0.0 assistente e patch obel1, 2.0.1 failover com switch" width="100%">
</p>

Legenda: ![novo](https://img.shields.io/badge/-novo-10B981) recurso novo · ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) mudança em algo que já existia · ![correção](https://img.shields.io/badge/-corre%C3%A7%C3%A3o-F59E0B) defeito corrigido

## 2.0.1 - 2026-11-16

**Failover na bancada com switch.** Correções depois do teste com as duas SYS e o notebook no mesmo switch.

- ![correção](https://img.shields.io/badge/-corre%C3%A7%C3%A3o-F59E0B) O teste de failover não para mais com "Tempo esgotado: timed out". Durante a troca, a SYS fica um tempo sem responder, a sessão antiga fica muda e o FTP volta depois do telnet. O assistente espera tudo isso, sem limite fixo, e pergunta ao operador antes de desistir.
- ![correção](https://img.shields.io/badge/-corre%C3%A7%C3%A3o-F59E0B) Com o slot 2 no comando, o assistente não diz mais "Sem proteção ativa": a proteção aparece como **em uso**, e o teste de failover faz só o failback.
- ![correção](https://img.shields.io/badge/-corre%C3%A7%C3%A3o-F59E0B) A tela de estado não fecha mais o assistente quando a proteção está num estado novo.
- ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) O diagnóstico oferece o failback quando encontra o slot 2 no comando, em vez de parar.
- ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) Antes do failback, espera o slot 1 voltar como reserva. Se a SYS recusar, tenta de novo em 30 s, até três vezes. No fim, confere se a proteção voltou a ficar disponível.
- ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) Quando a SYS recusa o failover ou o failback, o assistente mostra a resposta dela (por exemplo, "No backup available").
- ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) O simulador ganhou o modo switch, com sessão muda, IP fora do ar, FTP atrasado e slot 1 reiniciando, e testes para retomar o failback.

## 2.0.0 - 2026-11-14

**Assistente.** O caminho que corrigiu o chassi do Morro do Céu virou um assistente gráfico passo a passo.

<img src="https://raw.githubusercontent.com/aragonxpd154/guardiao/main/assets/tela-assistente.png" alt="Assistente com as dez etapas concluídas" width="100%">

- ![novo](https://img.shields.io/badge/-novo-10B981) **Assistente GTK4/libadwaita** com dez etapas: diagnóstico, backup, preparar, enviar, vincular, ativar, proteção, `current.bak`, teste de failover e vigiar. Confirmação antes de tudo que pode afetar o ar.
- ![novo](https://img.shields.io/badge/-novo-10B981) **Imagem corrigida montada a partir do firmware do próprio cartão**, com conferência de CRC de cada arquivo, envio por FTP e leitura de volta byte a byte.
- ![novo](https://img.shields.io/badge/-novo-10B981) **Ativação sem queda** (reset da reserva, failover, reset do slot 1, failback) ou por reset do sistema.
- ![novo](https://img.shields.io/badge/-novo-10B981) **Proteção da reserva**: detecta a reserva encaixada mas não configurada e completa a configuração.
- ![novo](https://img.shields.io/badge/-novo-10B981) **Teste de failover e failback** conferindo que a configuração sobrevive à troca. Pede para mudar o cabo do notebook quando o IP passa para a outra SYS.
- ![novo](https://img.shields.io/badge/-novo-10B981) **Voltar à versão anterior** pelo menu.
- ![novo](https://img.shields.io/badge/-novo-10B981) **Patch obel1 para 8.4.7, 8.4.8 e 8.6.2-02**, com endereços gerados pelos símbolos da imagem (`patch-obel1/gen_versao.py`). Comando offline `guardiao patch`.
- ![novo](https://img.shields.io/badge/-novo-10B981) Pacote **`.deb`**, logo no menu e atalho na Área de Trabalho, metadados para a loja do Zorin e atualização automática pelo GitHub.
- ![novo](https://img.shields.io/badge/-novo-10B981) **Vigilância a cada 10 minutos** (timer do systemd) com aviso na área de trabalho.
- ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) Validação da config também exige término em `\n`, o que pega arquivo truncado. Mesmo critério dentro da SYS.
- ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) `util save` mantém o rótulo da config ativa. O rótulo passado ao `util save` substitui o da config em uso.
- ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) CRC do NetVX cerca de 100 vezes mais rápido (zlib com bits espelhados).
- ![correção](https://img.shields.io/badge/-corre%C3%A7%C3%A3o-F59E0B) O failback é `util failback slot.1` (o slot primário), e não `slot.2` como diz o manual de 2011.
- ![melhoria](https://img.shields.io/badge/-melhoria-0EA5E9) Simulador de NetVX (telnet + FTP) e testes ponta a ponta, inclusive com o firmware real. GitHub Actions roda os testes a cada push e cria a Release a cada tag.

## 1.0.0 - 2026-11-14

**Guardião NetVX.** Script único de vigilância.

- ![novo](https://img.shields.io/badge/-novo-10B981) Coleta por FTP de `etc/cfg`, `umconfig.txt`, `snmpd.cnf` e `brand`, com validação e histórico.
- ![novo](https://img.shields.io/badge/-novo-10B981) Alerta quando a config encolhe ou fica ilegível; restauração por slot (`util load`).
