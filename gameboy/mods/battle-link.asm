; Breadwinner: Battle Link
; Pokemon Red (US) fixed-bank shim.  The .gbmod manifest guards every entry
; patch and the zero-filled placement range against the supported ROM.

DEF wTileMap                  EQU $c3a0
DEF wEnemySelectedMove        EQU $ccdd
DEF wEnemyMoveListIndex       EQU $cce2
DEF wBuffer                   EQU $cee9
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
DEF hAutoBGTransferEnabled    EQU $ffba

DEF wCurItem                  EQU $cf91

DEF RedrawPartyMenu           EQU $14d9
DEF DelayFrame                EQU $20af
DEF UseItem                   EQU $30bc
DEF GBPalNormal               EQU $3ddc
DEF LoadScreenTilesFromBuffer1 EQU $3725
DEF DrawHUDsAndHPBars         EQU $4d5a
DEF SelectEnemyMove           EQU $5564
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
    ; The AWAITING bar goes into wTileMap, which only reaches VRAM while
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
    ; A: 0=pending, 1=remote decision, 3=native/forced bypass, 4=random
    ; fallback (invalid endpoint response or deadline)
    and a
    jr z, BattleLinkPending
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
    ; The local action is already committed. Game Boy input and the remote
    ; response are independent from this point, so B must not cancel or
    ; duplicate the one request for this turn.
    jr BattleLinkPoll

BattleLinkReady:
    ; A still holds the host status: flash RECEIVED (1) or REJECTED (4)
    ; over the bar before the scene is repaired.
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

; EnemySendOut normally starts its candidate scan immediately after the
; current slot. A remote switch carries the exact requested slot in wBuffer+1.
; Consuming the stash here (not just in dispatch) covers the send-out after a
; faint, where MainInBattleLoop skips the dispatch site and the pending switch
; would otherwise be re-applied on a later turn.
BattleLinkChooseSwitch::
    ld a, [wBattleLinkStash]
    cp ACTION_SWITCH
    jr nz, .native
    xor a
    ld [wBattleLinkStash], a
    ld a, [wBattleLinkStash + 1]
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

; Patched over `call GBPalNormal` just before the fall-through into
; SwitchPlayerMon: the mon must not leave the field until the decision is in.
; The local switch is already committed, so the wait cannot be backed out.
BattleLinkAwaitSwitch::
    call GBPalNormal
    call BattleLinkAwait
    ret

; Patched over `call UseItem` in UseBagItem. Only items that spend the turn
; with no further menu pre-wait here: balls (which commit even when the
; trainer blocks them), the X items, X Accuracy, Guard Spec, Dire Hit and
; the battle Poké Flute.
; Medicine waits after target selection and native effect validation via
; one of three bank-3 gates instead.
; Ether/Elixir waits after target/move selection via a bank-3 effect gate.
; Everything else — fossils, key items and "not the time" cases — runs
; native with no wait.
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
.native
    jp UseItem

; Core wait: polls the host for this turn's single decision. Returns a = 1
; when it arrives and is stashed, or a = 3 when the host chooses native
; handling on the first poll. The transfer flag is restored on every exit;
; the caller owns any screen repair.
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
    cp 3
    jr z, BattleLinkAwaitDone  ; native bypass, first poll, no bar drawn
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
    ; Once the local move/item/switch has reached this hook it is committed.
    ; Ignore B and keep polling the same host request until it resolves.
    jr BattleLinkAwaitPoll

; Battle-scene flavour of the wait, used by the switch and bag hooks: the
; battlefield is restored over the bar after the decision arrives.
BattleLinkAwait::
    call BattleLinkAwaitCore
    cp 3
    ret z                      ; bypass: no bar was drawn
    call BattleLinkAwaitRestoreScene
    and a
    ret
BattleLinkAwaitRestoreScene:
    call LoadScreenTilesFromBuffer1
    jp DrawHUDsAndHPBars

; Runs under Bankswitch from a bank-3 medicine gate after the player picked
; a target and native code proved the effect is valid. The party menu is
; redrawn to clear the bar after the decision arrives.
BattleLinkAwaitMedicine::
    call BattleLinkAwaitCore
    cp 3
    ret z
    call RedrawPartyMenu
    ret

; The wait UI lives here rather than in the host so dialog changes ship with
; the package instead of an app rebuild. A single 12-tile status bar on the
; bottom textbox line of wTileMap; the wait hooks force auto BG transfer on
; and restore the scene from the tile buffers after the response arrives.
BattleLinkDrawWaiting::
    ; Remember that the bar reached the screen so a decision that resolves
    ; on the very first poll doesn't flash over an untouched scene.
    ld a, 1
    ld [wBuffer + 4], a
    ld hl, BattleLinkWaitBar
    jr BattleLinkDrawBar

; A holds the host status: 1 flashes RECEIVED, anything else REJECTED (the
; random fallback, status 4). Skipped when no bar was ever drawn this wait.
; The caller repairs the screen afterwards; DelayFrame keeps music running.
BattleLinkFlashResult:
    ld hl, BattleLinkReceivedBar
    cp 1
    jr z, .checkDrawn
    ld hl, BattleLinkRejectedBar
.checkDrawn
    ld a, [wBuffer + 4]
    and a
    ret z
    call BattleLinkDrawBar
    ld b, 24
.hold
    call DelayFrame
    dec b
    jr nz, .hold
    ret

; Copies the 12-tile bar at hl over the bottom textbox line, matching the
; chatbox alignment of the old AWAITING box.
BattleLinkDrawBar:
    ld de, wTileMap + 16 * 20 + 4
    ld b, 12
.tile
    ld a, [hli]
    ld [de], a
    inc de
    dec b
    jr nz, .tile
    ret

BattleLinkWaitBar:
    db $7f, $80, $96, $80, $88, $93, $88, $8d, $86, $75, $7f, $7f ;  AWAITING…
BattleLinkReceivedBar:
    db $7f, $7f, $91, $84, $82, $84, $88, $95, $84, $83, $7f, $7f ;   RECEIVED
BattleLinkRejectedBar:
    db $7f, $7f, $91, $84, $89, $84, $82, $93, $84, $83, $7f, $7f ;   REJECTED

BattleLinkEnd::

SECTION "Battle Link Medicine", ROMX[$7d00], BANK[$03]

; Bridge ItemUseMedicine (bank 3) to the bank-$0f wait while preserving the
; live party-mon pointer, selected-mon identity and scratch registers.
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
    ret

; Patched over the first three effect instructions after .checkMonStatus has
; proved the selected mon has the ailment this item cures.
BattleLinkMedicineStatusGate::
    call BattleLinkMedicineWait
    xor a                      ; displaced: clear the party status
    ld [hl], a
    ld a, b
    ret

; Patched at .updateInBattleFaintedData, reached only after native code has
; proved a fainted target was paired with Revive or Max Revive.
BattleLinkMedicineReviveGate::
    call BattleLinkMedicineWait
    ld a, [wIsInBattle]        ; displaced instruction
    ret

; Patched at .notFullHP, after native code has proved HP can be restored (or
; a full-HP Full Restore has already been redirected through the status gate).
BattleLinkMedicineHpGate::
    call BattleLinkMedicineWait
    xor a                      ; displaced: disable the low-health alarm
    ld [wLowHealthAlarm], a
    ret

; Patched at ItemUsePPRestore.storeNewAmount after native target/move
; selection and the full-PP no-effect check, but before the PP byte changes.
; Elixirs pass here once per restorable move; the resolved-turn flag makes
; every pass after the first immediate.
BattleLinkPPRestoreGate::
    call BattleLinkMedicineWait
    ld a, [hl]                 ; displaced instructions
    and %11000000
    ret

BattleLinkMedicineEnd::
