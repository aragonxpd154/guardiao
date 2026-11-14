/*
 * NetVX SYS (sysa12) - patch "obel1" (8.4.7, 8.4.8, 8.6.2)
 * Codigo novo colocado no lugar de ada_demangle (764 bytes), funcao do
 * demangler C++ que nao e chamada por nenhum ponto do firmware.
 */
        .set FIOSYNC,         21
        .set MIN_CFG_SIZE,    64

        /* funcoes e strings do firmware (STR_DISK="/d", STR_CURRENT="/d/etc/cfg/current"):
           enderecos de cada versao vem de versoes/<versao>.json, passados ao ld via --defsym */
        .text
        .globl _start
_start:

/* ------------------------------------------------------------------
 * obel_sync(): open("/d"), ioctl(FIOSYNC) -> grava FAT + cache de disco
 * no cartao CF, close.  Nao altera nada se o disco nao abrir.
 * ------------------------------------------------------------------ */
obel_sync:
        stwu    r1,-16(r1)
        mflr    r0
        stw     r31,12(r1)
        stw     r0,20(r1)
        lis     r3,STR_DISK@ha
        addi    r3,r3,STR_DISK@l
        li      r4,0
        li      r5,0
        bl      open
        mr.     r31,r3
        blt     1f
        li      r4,FIOSYNC
        li      r5,0
        bl      ioctl
        mr      r3,r31
        bl      close
1:      lwz     r0,20(r1)
        mtlr    r0
        lwz     r31,12(r1)
        addi    r1,r1,16
        blr

/* ------------------------------------------------------------------
 * obel_sync_delay(ticks): substitui o taskDelay(10) em Db_Sc_App_Reset,
 * logo antes de Sc_CibResetSys.  Descarrega o disco e depois espera.
 * ------------------------------------------------------------------ */
obel_sync_delay:
        stwu    r1,-16(r1)
        mflr    r0
        stw     r31,12(r1)
        stw     r0,20(r1)
        mr      r31,r3
        bl      obel_sync
        mr      r3,r31
        bl      taskDelay
        lwz     r0,20(r1)
        mtlr    r0
        lwz     r31,12(r1)
        addi    r1,r1,16
        blr

/* ------------------------------------------------------------------
 * obel_cfg_valid(path) -> 1 se o arquivo parece integro, 0 se nao.
 * Criterios (conservadores): abre e le ate o fim sem erro, comeca
 * com "#cfg=", nao contem bytes NUL, tem pelo menos 64 bytes e termina
 * em '\n' (arquivo cortado no meio de uma linha = truncado).
 * ------------------------------------------------------------------ */
obel_cfg_valid:
        stwu    r1,-304(r1)
        mflr    r0
        stw     r27,284(r1)
        stw     r28,288(r1)
        stw     r29,292(r1)
        stw     r30,296(r1)
        stw     r31,300(r1)
        stw     r0,308(r1)
        li      r30,0                   /* resultado = invalido */
        li      r4,0
        li      r5,0
        bl      open
        mr.     r31,r3
        blt     9f
        mr      r3,r31
        addi    r4,r1,16
        li      r5,5
        bl      read
        cmpwi   r3,5
        bne     8f
        lwz     r9,16(r1)               /* "#cfg" */
        lis     r0,0x2363
        ori     r0,r0,0x6667
        cmpw    r9,r0
        bne     8f
        lbz     r9,20(r1)               /* "="    */
        cmpwi   r9,0x3d
        bne     8f
        li      r29,5                   /* total lido */
2:      mr      r3,r31
        addi    r4,r1,16
        li      r5,256
        bl      read
        mr.     r28,r3
        blt     8f                      /* erro de leitura (setor ruim) */
        beq     4f                      /* fim do arquivo */
        add     r29,r29,r28
        mtctr   r28
        addi    r9,r1,15
3:      lbzu    r0,1(r9)
        cmpwi   r0,0
        beq     8f                      /* byte NUL: cluster perdido */
        bdnz    3b
        lbz     r27,0(r9)               /* ultimo byte lido ate agora */
        b       2b
4:      cmpwi   r29,MIN_CFG_SIZE
        blt     8f
        cmpwi   r27,10                  /* termina em '\n'? */
        bne     8f
        li      r30,1
8:      mr      r3,r31
        bl      close
9:      mr      r3,r30
        lwz     r0,308(r1)
        mtlr    r0
        lwz     r27,284(r1)
        lwz     r28,288(r1)
        lwz     r29,292(r1)
        lwz     r30,296(r1)
        lwz     r31,300(r1)
        addi    r1,r1,304
        blr

/* ------------------------------------------------------------------
 * obel_cfg_commit(tmp, dest): substitui OS_Rename(tmp,dest) no final
 * de Db_Sc_Cfg_Save.
 *   - se dest == "/d/etc/cfg/current" e o current atual e valido,
 *     renomeia current -> current.bak (guarda a ultima config boa);
 *   - renomeia tmp -> dest (comportamento original);
 *   - FIOSYNC no volume, para a gravacao chegar ao cartao.
 * ------------------------------------------------------------------ */
obel_cfg_commit:
        stwu    r1,-32(r1)
        mflr    r0
        stw     r29,20(r1)
        stw     r30,24(r1)
        stw     r31,28(r1)
        stw     r0,36(r1)
        mr      r30,r3
        mr      r31,r4
        mr      r3,r31
        lis     r4,STR_CURRENT@ha
        addi    r4,r4,STR_CURRENT@l
        bl      strcmp
        cmpwi   r3,0
        bne     1f
        mr      r3,r31
        bl      obel_cfg_valid
        cmpwi   r3,0
        beq     1f
        mr      r3,r31
        lis     r4,str_bak@ha
        addi    r4,r4,str_bak@l
        bl      OS_Rename
1:      mr      r3,r30
        mr      r4,r31
        bl      OS_Rename
        mr      r29,r3
        bl      obel_sync
        mr      r3,r29
        lwz     r0,36(r1)
        mtlr    r0
        lwz     r29,20(r1)
        lwz     r30,24(r1)
        lwz     r31,28(r1)
        addi    r1,r1,32
        blr

/* ------------------------------------------------------------------
 * obel_cfg_load(user, path): substitui Db_Sc_Cfg_Load(user,path) em
 * Db_Sc_Cfg_Load_Current.
 *   - path valido           -> carrega path (original);
 *   - path invalido, .bak ok -> carrega current.bak;
 *   - os dois invalidos      -> carrega path (igual ao original).
 * Nunca fica pior que o firmware original.
 * ------------------------------------------------------------------ */
obel_cfg_load:
        stwu    r1,-32(r1)
        mflr    r0
        stw     r30,24(r1)
        stw     r31,28(r1)
        stw     r0,36(r1)
        mr      r30,r3
        mr      r31,r4
        mr      r3,r31
        bl      obel_cfg_valid
        cmpwi   r3,0
        bne     5f
        lis     r3,msg_bad@ha
        addi    r3,r3,msg_bad@l
        mr      r4,r31
        bl      loggerMsg
        lis     r3,str_bak@ha
        addi    r3,r3,str_bak@l
        bl      obel_cfg_valid
        cmpwi   r3,0
        beq     5f
        lis     r3,msg_bak@ha
        addi    r3,r3,msg_bak@l
        bl      loggerMsg
        mr      r3,r30
        lis     r4,str_bak@ha
        addi    r4,r4,str_bak@l
        li      r5,0                    /* 8.6.x: 0 = carga normal (1 = merge) */
        bl      Db_Sc_Cfg_Load
        b       6f
5:      mr      r3,r30
        mr      r4,r31
        li      r5,0
        bl      Db_Sc_Cfg_Load
6:      lwz     r0,36(r1)
        mtlr    r0
        lwz     r30,24(r1)
        lwz     r31,28(r1)
        addi    r1,r1,32
        blr

        .balign 4
str_bak:
        .asciz  "/d/etc/cfg/current.bak"
msg_bad:
        .asciz  "OBEL: %s ruim, usando .bak\n"
msg_bak:
        .asciz  "OBEL: .bak carregado\n"
        .balign 4
_end_cave:
