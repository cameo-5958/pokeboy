; Breadwinner: Battle Link
; Pokemon Red (US) fixed-bank shim.  The .gbmod manifest guards every entry
; patch and the zero-filled placement range against the supported ROM.

DEF wTileMap                  EQU $c3a0
DEF wEnemySelectedMove        EQU $ccdd
DEF wEnemyMoveListIndex       EQU $cce2
DEF wBuffer                   EQU $cee9
DEF wIsInBattle               EQU $d057
DEF wLinkState                EQU $d12b
DEF hJoyPressed               EQU $ffb3
DEF hAutoBGTransferEnabled    EQU $ffba

DEF Joypad                    EQU $019a
DEF DelayFrame                EQU $20af
DEF LoadScreenTilesFromBuffer1 EQU $3725
DEF DrawHUDsAndHPBars         EQU $4d5a
DEF SelectEnemyMove           EQU $5564
DEF MainInBattleLoop          EQU $4233
DEF Bankswitch                EQU $35d6

DEF AIUseFullRestore          EQU $66a0
DEF AIUsePotion               EQU $66ca
DEF AIUseSuperPotion          EQU $66d0
DEF AIUseHyperPotion          EQU $66d6
DEF SwitchEnemyMon            EQU $674b
DEF AIUseFullHeal             EQU $6786
DEF AIUseGuardSpec            EQU $67b5
DEF AIUseXAttack              EQU $67f2
DEF AIUseXDefend              EQU $67f8
DEF AIUseXSpeed               EQU $67fe
DEF AIUseXSpecial             EQU $6804

DEF B_BUTTON                  EQU %00000010
DEF ACTION_MOVE               EQU 0
DEF ACTION_SWITCH             EQU 1
DEF ACTION_FULL_RESTORE       EQU 2
DEF ACTION_POTION             EQU 3
DEF ACTION_SUPER_POTION       EQU 4
DEF ACTION_HYPER_POTION       EQU 5
DEF ACTION_FULL_HEAL          EQU 6
DEF ACTION_GUARD_SPEC         EQU 7
DEF ACTION_X_ATTACK           EQU 8
DEF ACTION_X_DEFEND           EQU 9
DEF ACTION_X_SPEED            EQU 10
DEF ACTION_X_SPECIAL          EQU 11

SECTION "Battle Link", ROMX[$7e00], BANK[$0f]

BattleLinkStart::
; Called once at StartBattle so the host can give every battle a fresh UUID.
BattleLinkBattleStart::
    ld a, 4
BattleLinkStartHostOpcode::
    db $d3
BattleLinkStartHostSlot::
    dw 0
    xor a
    ld [$d058], a
    ld [$ccf5], a
    ld [$cd6a], a
    ret

; Replaces `call SelectEnemyMove`.  Native selection still establishes
; Struggle/disabled-move behavior and gives forced turns a safe fallback.
BattleLinkSelect::
    call SelectEnemyMove
    ld a, [wIsInBattle]
    cp 2
    ret nz
    ld a, [wLinkState]
    and a
    ret nz
    xor a
    ld [wBuffer + 2], a
    ; The host draws AWAITING into wTileMap, which only reaches VRAM while
    ; auto BG transfer is enabled — force it on for the wait and restore the
    ; caller's value on every exit path.
    ldh a, [hAutoBGTransferEnabled]
    ld [wBuffer + 3], a
    ld a, 1
    ldh [hAutoBGTransferEnabled], a

BattleLinkPoll:
BattleLinkHostOpcode::
    db $d3
BattleLinkHostSlot::
    dw 0
    ; A: 0=pending, 1=ready, 2=cancel, 3=native/forced bypass
    and a
    jr z, BattleLinkPending
    cp 1
    jr z, BattleLinkReady
    cp 2
    jr z, BattleLinkCancel
    ret

BattleLinkPending:
    call BattleLinkDrawWaiting
    call DelayFrame
    ; VBlank only refreshes the raw joypad state; hJoyPressed is derived by
    ; Joypad, which nothing else calls while this loop owns the CPU.
    call Joypad
    ldh a, [hJoyPressed]
    and B_BUTTON
    jr z, BattleLinkPoll
    ; A cancellation poll lets the host abort its AbortController and advance
    ; the attempt number before the battle menu is restored.
    ld a, 1
    ld [wBuffer + 2], a
    jr BattleLinkPoll

BattleLinkCancel:
    xor a
    ld [wBuffer + 2], a
    ld a, [wBuffer + 3]
    ldh [hAutoBGTransferEnabled], a
    call LoadScreenTilesFromBuffer1
    call DrawHUDsAndHPBars
    ; Discard our CALL return address and restart the battle menu.
    pop hl
    jp MainInBattleLoop

BattleLinkReady:
    xor a
    ld [wBuffer + 2], a
    ld a, [wBuffer + 3]
    ldh [hAutoBGTransferEnabled], a
    call LoadScreenTilesFromBuffer1
    call DrawHUDsAndHPBars
    ret

; Replaces both `callfar TrainerAI` sites.  wBuffer[0] is populated by the
; host handler. Move actions simply clear carry; every other action uses the
; original bank-$0e routine so animations, text, HP/status changes, switching,
; and AI item-count accounting stay native.
BattleLinkDispatch::
    ld a, [wBuffer]
    and a
    ret z
    cp ACTION_SWITCH
    jr z, .switch
    cp ACTION_FULL_RESTORE
    jr z, .fullRestore
    cp ACTION_POTION
    jr z, .potion
    cp ACTION_SUPER_POTION
    jr z, .superPotion
    cp ACTION_HYPER_POTION
    jr z, .hyperPotion
    cp ACTION_FULL_HEAL
    jr z, .fullHeal
    cp ACTION_GUARD_SPEC
    jr z, .guardSpec
    cp ACTION_X_ATTACK
    jr z, .xAttack
    cp ACTION_X_DEFEND
    jr z, .xDefend
    cp ACTION_X_SPEED
    jr z, .xSpeed
    cp ACTION_X_SPECIAL
    jr z, .xSpecial
    and a
    ret

.switch
    ld hl, SwitchEnemyMon
    jr .farAction
.fullRestore
    ld hl, AIUseFullRestore
    jr .farAction
.potion
    ld hl, AIUsePotion
    jr .farAction
.superPotion
    ld hl, AIUseSuperPotion
    jr .farAction
.hyperPotion
    ld hl, AIUseHyperPotion
    jr .farAction
.fullHeal
    ld hl, AIUseFullHeal
    jr .farAction
.guardSpec
    ld hl, AIUseGuardSpec
    jr .farAction
.xAttack
    ld hl, AIUseXAttack
    jr .farAction
.xDefend
    ld hl, AIUseXDefend
    jr .farAction
.xSpeed
    ld hl, AIUseXSpeed
    jr .farAction
.xSpecial
    ld hl, AIUseXSpecial
.farAction
    ld b, $0e
    call Bankswitch
    xor a
    ld [wBuffer], a
    scf
    ret

; EnemySendOut normally starts its candidate scan immediately after the
; current slot. A remote switch carries the exact requested slot in wBuffer+1.
BattleLinkChooseSwitch::
    ld a, [wBuffer]
    cp ACTION_SWITCH
    jr nz, .native
    ld a, [wBuffer + 1]
    ld b, a
    ret
.native
    ld b, $ff
.next
    inc b
    ld a, [$cfe8]
    cp b
    jr z, .next
    ret

; The wait UI lives here rather than in the host so dialog changes ship with
; the package instead of an app rebuild. Copies four 12-tile rows into the
; centre of wTileMap; BattleLinkSelect has already forced auto BG transfer on,
; and BattleLinkReady/Cancel restore the scene from the tile buffers.
BattleLinkDrawWaiting::
    ld hl, BattleLinkWaitTiles
    ld de, wTileMap + 7 * 20 + 4
    ld c, 4
.row
    ld b, 12
.tile
    ld a, [hli]
    ld [de], a
    inc de
    dec b
    jr nz, .tile
    ld a, e
    add 20 - 12
    ld e, a
    jr nc, .nextRow
    inc d
.nextRow
    dec c
    jr nz, .row
    ret

BattleLinkWaitTiles:
    db $79, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7b
    db $7c, $7f, $80, $96, $80, $88, $93, $88, $8d, $86, $7f, $7c ; | AWAITING |
    db $7d, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7e
    db $7f, $7f, $ec, $81, $7f, $81, $80, $82, $8a, $7f, $7f, $7f ;   >B BACK

BattleLinkEnd::
