; Breadwinner: Battle Link
; Pokemon Red (US) fixed-bank shim.  The .gbmod manifest guards every entry
; patch and the zero-filled placement range against the supported ROM.

DEF wTileMap                  EQU $c3a0
DEF wEnemySelectedMove        EQU $ccdd
DEF wEnemyMoveListIndex       EQU $cce2
DEF wBuffer                   EQU $cee9
DEF wActionResultOrTookBattleTurn EQU $cd6a
DEF wLowHealthAlarm           EQU $d083
DEF wIsInBattle               EQU $d057
DEF wLinkState                EQU $d12b
; Tail of wLinkBattleRandomNumberList ($d148-$d151): the whole $d141-$d151
; union holds only link-serial exchange data, Game Corner prize prices, and
; the link-battle RNG list, all idle during the non-link battles this mod
; runs in. wBuffer aliases wHPBarMaxHP, so a resolved decision parked there
; is destroyed by any HP-bar animation; it is copied here the moment the
; host reports ready. +0 = action kind, +1 = payload, +2 = resolved flag.
DEF wBattleLinkStash          EQU $d14f
DEF hJoyPressed               EQU $ffb3
DEF hAutoBGTransferEnabled    EQU $ffba

DEF wCurItem                  EQU $cf91
DEF wEnemyMonPartyPos         EQU $cfe8
DEF wEnemyPartyCount          EQU $d89c
DEF wEnemyMons                EQU $d8a4
DEF PARTY_STRUCT_LENGTH       EQU 44

DEF Joypad                    EQU $019a
DEF RedrawPartyMenu           EQU $14d9
DEF DelayFrame                EQU $20af
DEF UseItem                   EQU $30bc
DEF SaveScreenTilesToBuffer1  EQU $3719
DEF AddNTimes                 EQU $3a87
DEF GBPalNormal               EQU $3ddc
; ItemUseMedicine.canceledItemUse minus its two `pop af`s. All three
; medicine gates run after those entry pushes have already been consumed.
DEF ItemUseMedicineDone       EQU $5de7
DEF ItemUsePPRestoreDone      EQU $6451
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

SECTION "Battle Link", ROMX[$7c00], BANK[$0f]

BattleLinkStart::
; Called once at StartBattle so the host can give every battle a fresh UUID.
; The stash bytes alias Game Corner prize prices between battles, and a
; decision resolved on a turn whose dispatch never ran (the enemy fainted
; before it) survives the battle — clear the whole stash so neither can leak
; into the first send-out or dispatch of this battle.
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
    ld [wBattleLinkStash], a
    ld [wBattleLinkStash + 1], a
    ld [wBattleLinkStash + 2], a
    ret

; Replaces `call SelectEnemyMove`.  Native selection still establishes
; Struggle/disabled-move behavior and gives forced turns a safe fallback.
BattleLinkSelect::
    ; A pre-commit hook may have already resolved this turn. The stashed
    ; action feeds BattleLinkDispatch, and the host wrote the enemy move
    ; registers when it resolved, so native selection must not run.
    ld a, [wBattleLinkStash + 2]
    and a
    jr z, .fresh
    xor a
    ld [wBattleLinkStash + 2], a
    ret
.fresh
    call SelectEnemyMove
    ld a, [wIsInBattle]
    cp 2
    ret nz
    ld a, [wLinkState]
    and a
    ret nz
    xor a
    ld [wBuffer + 2], a
    ld [wBuffer + 4], a
    ld [wBuffer + 5], a
    ; The AWAITING box goes into wTileMap, which only reaches VRAM while
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
    ; A: 0=pending, 1=remote decision, 2=cancel, 3=native/forced bypass,
    ; 4=random fallback (invalid endpoint response or deadline)
    and a
    jr z, BattleLinkPending
    cp 2
    jr z, BattleLinkCancel
    cp 3
    ret z
    cp 1
    jr z, BattleLinkReady
    cp 4
    jr z, BattleLinkReady
    jr BattleLinkPending

BattleLinkPending:
    call BattleLinkDrawWaiting
    call DelayFrame
    ; A switch or item spends the turn before SelectEnemyMove runs, and the
    ; cancel path restarts the battle menu without undoing it — a free
    ; take-back. Only poll B while the turn is still open.
    ld a, [wActionResultOrTookBattleTurn]
    and a
    jr nz, BattleLinkPoll
    ; VBlank only refreshes the raw joypad state; hJoyPressed is derived by
    ; Joypad, which nothing else calls while this loop owns the CPU.
    call Joypad
    ldh a, [hJoyPressed]
    and B_BUTTON
    jr z, BattleLinkPoll
    ; A cancellation poll lets the host abort its request and advance the
    ; attempt number before the battle menu is restored.
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
    ; A still holds the host status: flash RECEIVED (1) or REJECTED (4)
    ; over the box before the scene is repaired.
    call BattleLinkFlashResult
    ; Move the decision out of wBuffer before any animation can clobber the
    ; HP-bar scratch it aliases; the dispatch hooks read the stash.
    ld a, [wBuffer]
    ld [wBattleLinkStash], a
    ld a, [wBuffer + 1]
    ld [wBattleLinkStash + 1], a
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
    ; Native TrainerAI bails for wild battles; without this gate a stale or
    ; garbage stash byte would fire an AI item on a wild mon. cp leaves carry
    ; set when a < 2, and carry means "AI took the turn" to the caller.
    ld a, [wIsInBattle]
    cp 2
    jr nz, .noAction
    ld a, [wBattleLinkStash]
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
.noAction
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
    ld [wBattleLinkStash], a
    scf
    ret

; Replaces EnemySendOut's candidate scan. The ROM's own fainted-mon check
; right after the patch loops BACK INTO the patched bytes without advancing
; the candidate, so every slot returned from here must already be alive or
; the game spins on the same slot forever. A remote switch carries the exact
; requested slot in the stash (consumed here, not just in dispatch, so it
; can't be re-applied on a later turn); the send-out after a faint asks the
; host for a fresh forced-switch decision. The whole stash dies here either
; way: a send-out is a turn boundary, and a leftover pre-commit move or item
; from a turn whose dispatch never ran must not leak into the next one.
BattleLinkChooseSwitch::
    ld a, [wBattleLinkStash]
    cp ACTION_SWITCH
    jr nz, BattleLinkChooseFresh
    xor a
    ld [wBattleLinkStash], a
    ld [wBattleLinkStash + 2], a
    ld a, [wBattleLinkStash + 1]
    ld b, a
    call BattleLinkSwitchSlotOk
    ret z
    jr BattleLinkChooseNative
BattleLinkChooseFresh:
    xor a
    ld [wBattleLinkStash], a
    ld [wBattleLinkStash + 2], a
    ; Forced switch-in: poll the host with switch-only legal actions. The
    ; host bypasses (3) for the battle-opening send-out and anything that
    ; isn't an eligible trainer battle. No B here — a faint replacement
    ; cannot be taken back, and the ROM saved the screen to buffer 1 two
    ; calls before the patch, so the ready path restores it exactly.
    ld [wBuffer + 2], a
    ld [wBuffer + 4], a
    ldh a, [hAutoBGTransferEnabled]
    ld [wBuffer + 3], a
    ld a, 1
    ld [wBuffer + 5], a        ; a forced switch offers no B: keep the hint blank
    ldh [hAutoBGTransferEnabled], a
BattleLinkFaintPoll:
    ld a, 6
    db $d3
BattleLinkFaintHostSlot::
    dw 0
    and a
    jr z, BattleLinkFaintPending
    cp 3
    jr z, BattleLinkFaintBypass
    ; 1 = remote decision, 4 = random fallback: flash the verdict, repair
    ; the scene, then trust the slot only after validating it.
    call BattleLinkFlashResult
    ld a, [wBuffer + 3]
    ldh [hAutoBGTransferEnabled], a
    call LoadScreenTilesFromBuffer1
    call DrawHUDsAndHPBars
    ld a, [wBuffer + 1]
    ld b, a
    call BattleLinkSwitchSlotOk
    ret z
    jr BattleLinkChooseNative
BattleLinkFaintPending:
    call BattleLinkDrawWaiting
    call DelayFrame
    jr BattleLinkFaintPoll
BattleLinkFaintBypass:
    ld a, [wBuffer + 3]
    ldh [hAutoBGTransferEnabled], a
BattleLinkChooseNative:
    ; Original scan order (ascending, skipping the on-field slot) plus the
    ; HP and party-count checks the displaced code left to the caller.
    ld a, [wEnemyPartyCount]
    ld c, a
    ld b, $ff
.scan
    inc b
    ld a, b
    cp c
    jr nc, .anyAlive
    ld a, [wEnemyMonPartyPos]
    cp b
    jr z, .scan
    call BattleLinkSlotAlive
    jr z, .scan
    ret
.anyAlive
    ; No live benched mon found (stale on-field marker): first alive slot.
    ld b, $ff
.anyScan
    inc b
    ld a, b
    cp c
    jr nc, .fallback
    call BattleLinkSlotAlive
    jr z, .anyScan
    ret
.fallback
    ld b, 0
    ret

; b = candidate slot. Returns Z when the slot is inside the party, not the
; on-field mon, and still alive; NZ otherwise. Preserves b.
BattleLinkSwitchSlotOk:
    ld a, [wEnemyPartyCount]
    ld c, a
    ld a, b
    cp c
    jr nc, .bad
    ld a, [wEnemyMonPartyPos]
    cp b
    jr z, .bad
    call BattleLinkSlotAlive
    jr z, .bad
    xor a
    ret
.bad
    or 1
    ret

; b = roster slot. NZ when the mon still has HP. The roster is current at
; send-out time: the faint handler zeroes the fainted slot's HP before the
; scan runs. Preserves bc.
BattleLinkSlotAlive:
    push bc
    ld hl, wEnemyMons + 1
    ld a, b
    ld bc, PARTY_STRUCT_LENGTH
    call AddNTimes
    pop bc
    ld a, [hli]
    or [hl]
    ret

; Patched over MainInBattleLoop's `call SaveScreenTilesToBuffer1`, reached
; once per turn after both faint checks pass: opens this turn's decision
; request the moment the turn starts instead of waiting for the player to
; commit an action. The host only starts the request (never resolves one
; here); the reply is discarded.
BattleLinkPrime::
    call SaveScreenTilesToBuffer1
    ld a, 5
    db $d3
BattleLinkPrimeHostSlot::
    dw 0
    ret

; Patched over `call GBPalNormal` just before the fall-through into
; SwitchPlayerMon: the mon must not leave the field until the decision is in.
; The turn was committed two instructions earlier; reopen it for the wait so
; B still cancels, and only re-commit once the host answers.
BattleLinkAwaitSwitch::
    call GBPalNormal
    xor a
    ld [wActionResultOrTookBattleTurn], a
    call BattleLinkAwait
    jr c, .cancelled
    ld a, 1
    ld [wActionResultOrTookBattleTurn], a
    ret
.cancelled
    ; PartyMenuOrRockOrRun is jumped to from DisplayBattleMenu, so the stack
    ; holds our patch-call frame plus DisplayBattleMenu's return address.
    pop hl
    pop hl
    jp MainInBattleLoop

; Patched over `call UseItem` in UseBagItem. Only items that spend the turn
; with no further menu pre-wait here: balls (which commit even when the
; trainer blocks them), the X items, X Accuracy, Guard Spec, Dire Hit and
; the battle Poké Flute.
; Medicine waits after target selection and native effect validation via
; one of three bank-3 gates instead.
; Ether/Elixir waits after target/move selection via a bank-3 effect gate.
; Everything else — fossils, key items and "not the time" cases — runs
; native with no wait.
; A cancelled wait returns with the turn still open, so UseBagItem's own
; "was the item used?" check reopens the bag.
BattleLinkAwaitItem::
    ld a, [wCurItem]
    dec a
    cp 4                     ; balls $01-$04
    jr c, .wait
    ld a, [wCurItem]
    cp $2e                   ; X ACCURACY
    jr z, .wait
    cp $37                   ; GUARD SPEC
    jr z, .wait
    cp $3a                   ; DIRE HIT
    jr z, .wait
    cp $49                   ; POKé FLUTE
    jr z, .wait
    sub $41                  ; X ATTACK..X SPECIAL ($41-$44)
    cp 4
    jr nc, .native
.wait
    call BattleLinkAwait
    ret c
.native
    jp UseItem

; Core wait: polls the host for this turn's decision. Returns carry set if
; the player backed out with B; otherwise carry clear with a = 1 (decision
; arrived and was stashed) or a = 3 (host chose native handling, first poll,
; no box drawn). The transfer flag is restored on every exit; the caller
; owns any screen repair.
BattleLinkAwaitCore::
    ld a, [wBattleLinkStash + 2]
    and a
    jr z, .fresh
    ld a, 1                    ; already resolved this turn (menu re-entry)
    ret
.fresh
    xor a
    ld [wBuffer + 2], a
    ld [wBuffer + 4], a
    ld [wBuffer + 5], a
    ldh a, [hAutoBGTransferEnabled]
    ld [wBuffer + 3], a
    ld a, 1
    ldh [hAutoBGTransferEnabled], a
BattleLinkAwaitPoll:
    db $d3
BattleLinkAwaitHostSlot::
    dw 0
    and a
    jr z, BattleLinkAwaitPending
    cp 2
    jr z, BattleLinkAwaitCancelled
    cp 3
    jr z, BattleLinkAwaitDone  ; native bypass, first poll, no box drawn
    cp 1
    jr z, .ready
    cp 4
    jr nz, BattleLinkAwaitPending
.ready
    ; A still holds the host status for the RECEIVED/REJECTED flash; then
    ; stash the decision before any animation can clobber the HP-bar
    ; scratch that wBuffer aliases.
    call BattleLinkFlashResult
    ld a, [wBuffer]
    ld [wBattleLinkStash], a
    ld a, [wBuffer + 1]
    ld [wBattleLinkStash + 1], a
    ld a, 1
    ld [wBattleLinkStash + 2], a
BattleLinkAwaitDone:
    push af
    xor a
    ld [wBuffer + 2], a
    ld a, [wBuffer + 3]
    ldh [hAutoBGTransferEnabled], a
    pop af
    and a
    ret
BattleLinkAwaitPending:
    call BattleLinkDrawWaiting
    call DelayFrame
    call Joypad
    ldh a, [hJoyPressed]
    and B_BUTTON
    jr z, BattleLinkAwaitPoll
    ld a, 1
    ld [wBuffer + 2], a
    jr BattleLinkAwaitPoll
BattleLinkAwaitCancelled:
    xor a
    ld [wBuffer + 2], a
    ld a, [wBuffer + 3]
    ldh [hAutoBGTransferEnabled], a
    scf
    ret

; Battle-scene flavour of the wait, used by the switch and bag hooks: the
; battlefield is restored over the box on ready and cancel exits.
BattleLinkAwait::
    call BattleLinkAwaitCore
    jr c, .cancelled
    cp 3
    ret z                      ; bypass: no box was drawn
    call BattleLinkAwaitRestoreScene
    and a
    ret
.cancelled
    call BattleLinkAwaitRestoreScene
    scf
    ret
BattleLinkAwaitRestoreScene:
    call LoadScreenTilesFromBuffer1
    jp DrawHUDsAndHPBars

; Runs under Bankswitch from a bank-3 medicine gate after the player picked
; a target and native code proved the effect is valid. The verdict goes
; through RAM because Bankswitch's return path restores AF. On ready the
; party menu is redrawn to clear the box; on cancel the native failure path
; repaints the screen itself.
BattleLinkAwaitMedicine::
    call BattleLinkAwaitCore
    jr c, .cancelled
    cp 3
    jr z, .proceed
    call RedrawPartyMenu
.proceed
    xor a
    jr .store
.cancelled
    ld a, 1
.store
    ld [wBattleLinkStash - 1], a
    ret

; The wait UI lives here rather than in the host so dialog changes ship with
; the package instead of an app rebuild. A three-row bordered box perfectly
; sized for the nine-tile message over the textbox area of wTileMap, with
; the ▶B BACK hint on its own row OUTSIDE the box; the wait hooks force auto
; BG transfer on and the ready/cancel exits restore the scene from the tile
; buffers.
BattleLinkDrawWaiting::
    ; Remember that the box reached the screen so a decision that resolves
    ; on the very first poll doesn't flash over an untouched scene.
    ld a, 1
    ld [wBuffer + 4], a
    ld hl, BattleLinkAwaitingText
    ; fall through into BattleLinkDrawBox

; Draws the box with the 9-tile message row at hl. The ▶B BACK row below the
; box is blanked while the turn is committed (BattleLinkPending ignores B
; then) and during result flashes (wBuffer+5).
BattleLinkDrawBox:
    push hl
    ld de, wTileMap + 13 * 20 + 4
    ld a, $79
    ld c, $7b
    ld hl, BattleLinkBorderRow
    call BattleLinkBoxRow      ; top border
    pop hl
    ld a, $7c
    ld c, $7c
    call BattleLinkBoxRow      ; message
    ld hl, BattleLinkBorderRow
    ld a, $7d
    ld c, $7e
    call BattleLinkBoxRow      ; bottom border
    ld hl, BattleLinkHintRow
    ld a, [wBuffer + 5]
    and a
    jr nz, .blankHint
    ld a, [wActionResultOrTookBattleTurn]
    and a
    jr z, .hintChosen
.blankHint
    ld hl, BattleLinkBlankRow
.hintChosen
    ld a, $7f
    ld c, $7f
    jr BattleLinkBoxRow        ; ▶B BACK outside the box (tail call)

; One box row at de: a = left tile, c = right tile, hl = 9 inner tiles.
; Advances de to the next tilemap row.
BattleLinkBoxRow:
    ld [de], a
    inc de
    ld b, 9
.inner
    ld a, [hli]
    ld [de], a
    inc de
    dec b
    jr nz, .inner
    ld a, c
    ld [de], a
    ld a, e
    add 20 - 10
    ld e, a
    ret nc
    inc d
    ret

; A holds the host status: 1 flashes RECEIVED, anything else REJECTED (the
; random fallback, status 4). Skipped when no box was ever drawn this wait.
; The caller repairs the screen afterwards; DelayFrame keeps music running.
BattleLinkFlashResult:
    ld hl, BattleLinkReceivedText
    cp 1
    jr z, .checkDrawn
    ld hl, BattleLinkRejectedText
.checkDrawn
    ld a, [wBuffer + 4]
    and a
    ret z
    ld a, 1
    ld [wBuffer + 5], a        ; a resolved turn offers no B
    call BattleLinkDrawBox
    xor a
    ld [wBuffer + 5], a
    ld b, 24
.hold
    call DelayFrame
    dec b
    jr nz, .hold
    ret

BattleLinkBorderRow:
    db $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7a, $7a
BattleLinkBlankRow:
    db $7f, $7f, $7f, $7f, $7f, $7f, $7f, $7f, $7f
BattleLinkAwaitingText:
    db $80, $96, $80, $88, $93, $88, $8d, $86, $75 ; AWAITING…
BattleLinkReceivedText:
    db $7f, $91, $84, $82, $84, $88, $95, $84, $83 ;  RECEIVED
BattleLinkRejectedText:
    db $7f, $91, $84, $89, $84, $82, $93, $84, $83 ;  REJECTED
BattleLinkHintRow:
    db $ec, $81, $7f, $81, $80, $82, $8a, $7f, $7f ; ▶B BACK

BattleLinkEnd::

SECTION "Battle Link Medicine", ROMX[$7d00], BANK[$03]

; Bridge ItemUseMedicine (bank 3) to the bank-$0f wait while preserving the
; live party-mon pointer, selected-mon identity and scratch registers.
; Returns Z on ready/native bypass and NZ on B cancel.
BattleLinkMedicineWait:
    push hl
    push de
    push bc
    ld b, $0f
    ld hl, BattleLinkAwaitMedicine
    call Bankswitch
    pop bc
    pop de
    pop hl
    ld a, [wBattleLinkStash - 1]
    and a
    ret

; Patched over the first three effect instructions after .checkMonStatus has
; proved the selected mon has the ailment this item cures.
BattleLinkMedicineStatusGate::
    call BattleLinkMedicineWait
    jr nz, BattleLinkMedicineCancel
    xor a                      ; displaced: clear the party status
    ld [hl], a
    ld a, b
    ret

; Patched at .updateInBattleFaintedData, reached only after native code has
; proved a fainted target was paired with Revive or Max Revive.
BattleLinkMedicineReviveGate::
    call BattleLinkMedicineWait
    jr nz, BattleLinkMedicineCancel
    ld a, [wIsInBattle]        ; displaced instruction
    ret

; Patched at .notFullHP, after native code has proved HP can be restored (or
; a full-HP Full Restore has already been redirected through the status gate).
BattleLinkMedicineHpGate::
    call BattleLinkMedicineWait
    jr nz, BattleLinkMedicineCancel
    xor a                      ; displaced: disable the low-health alarm
    ld [wLowHealthAlarm], a
    ret

; Patched at ItemUsePPRestore.storeNewAmount after native target/move
; selection and the full-PP no-effect check, but before the PP byte changes.
; Elixirs pass here once per restorable move; the resolved-turn flag makes
; every pass after the first immediate.
BattleLinkPPRestoreGate::
    call BattleLinkMedicineWait
    jr nz, BattleLinkPPRestoreCancel
    ld a, [hl]                 ; displaced instructions
    and %11000000
    ret

BattleLinkPPRestoreCancel:
    pop af                     ; discard the patch CALL return address
    jp ItemUsePPRestoreDone    ; native cleanup pops saved wWhichPokemon

BattleLinkMedicineCancel:
    ; Drop the patch CALL's return address, reproduce .canceledItemUse after
    ; its already-consumed entry pushes, then join the native .done tail.
    pop af
    xor a
    ld [wActionResultOrTookBattleTurn], a
    jp ItemUseMedicineDone

BattleLinkMedicineEnd::
