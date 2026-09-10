; Events: round=0; begin=2/3; end=4/5; residual=6/7;
; faint=8/9; switch=10/11; announced=12/13; player item=14.
AIStartBattle::
	xor a
	ld [wAIRoundOpen], a
	ld [wAIDisarmed], a
	ld [wAIReplacement], a
	ld [wAIWaiting], a
	dec a
	ld [wAISwitchTarget], a
	ld a, [wIsInBattle]
	cp 2
	ret nz
	ld a, [wLinkState]
	and a
	ret nz
AIBattleStartHook::
.AIEventHook_17
	db $db
	ret

AIRoundBegin::
	push af
	ld a, [wAIRoundOpen]
	and a
	jr nz, .done
	inc a
	ld [wAIRoundOpen], a
	xor a
.AIEventHook_28
	db $ec
.done
	pop af
	ret

AISelect::
	call SelectEnemyMove
	xor a
	ld [wAIRoundOpen], a
	ld a, [wIsInBattle]
	cp 2
	ret nz
	ld a, [wLinkState]
	and a
	ret nz
	; The decision cell aliases link RNG. Never touch it in a link battle.
	xor a
	ld [wAIAction], a
	ld [wAIDisarmed], a
	ld a, [wEnemyBattleStatus2]
	and (1 << NEEDS_TO_RECHARGE) | (1 << USING_RAGE)
	ret nz
	ld a, [wEnemyBattleStatus1]
	and (1 << CHARGING_UP) | (1 << THRASHING_ABOUT) | (1 << USING_TRAPPING_MOVE) | (1 << STORING_ENERGY)
	ret nz
	ld a, [wEnemyMonStatus]
	and (1 << FRZ) | SLP_MASK
	ret nz
	ld a, [wPlayerBattleStatus1]
	bit USING_TRAPPING_MOVE, a
	ret nz
	ld b, $80
	jp AIPoll

AIPoll::
	push bc
	ld a, b
AIInferHook::
.AIEventHook_64
	db $eb
	pop bc
	and a
	jr z, .pending
	cp 1
	jr z, .ready
	ld a, 1
	ld [wAIDisarmed], a
.ready
	jp AIRestoreScreen
.pending
	push bc
	call AIDrawWaiting
	call DelayFrame
	pop bc
	jr AIPoll

AIChooseSendOut::
	ld a, [wAISwitchTarget]
	ld b, a
	ld a, $ff
	ld [wAISwitchTarget], a
	ld a, b
	cp $ff
	jr nz, .validate
	ld a, [wAIReplacement]
	and a
	ret z
	xor a
	ld [wAIReplacement], a
	ld a, [wLinkState]
	and a
	ret nz
	ld a, [wIsInBattle]
	cp 2
	jr nz, .invalid
	xor a
	ld [wAIDisarmed], a
	ld b, $81
	call AIPoll
	ld a, [wAIDisarmed]
	and a
	jr nz, .invalid
	ld a, [wAIAction]
	cp 1
	jr nz, .invalid
	ld a, [wAIAction + 1]
.validate
	call AIValidateSwitch
	ret nc
	ld a, b
	ld [wWhichPokemon], a
	xor a
	ld [wAIReplacement], a
	scf
	ret
.invalid
	and a
	ret

AIValidateActionSwitch::
	ld a, [wAIAction + 1]
	jp AIValidateSwitch

; A=slot, carry set iff valid; B retains slot. Also called across banks.
AIValidateSwitch::
	ld b, a
	ld a, [wEnemyPartyCount]
	cp b
	jr z, .invalid
	jr c, .invalid
	ld a, [wEnemyMonPartyPos]
	cp b
	jr z, .invalid
	push bc
	ld a, b
	ld hl, wEnemyMon1HP
	ld bc, PARTYMON_STRUCT_LENGTH
	call AddNTimes
	pop bc
	ld a, [hli]
	or [hl]
	jr z, .invalid
	scf
	ret
.invalid
	and a
	ret

AIExecutePlayerMove::
	push af
	push bc
	ld a, 2
.AIEventHook_152
	db $ec
	pop bc
	pop af
	call ExecutePlayerMove
	push af
	push bc
	ld a, 4
.AIEventHook_157
	db $ec
	pop bc
	pop af
	ret
AIExecuteEnemyMove::
	push af
	push bc
	ld a, 3
.AIEventHook_163
	db $ec
	pop bc
	pop af
	call ExecuteEnemyMove
	push af
	push bc
	ld a, 5
.AIEventHook_168
	db $ec
	pop bc
	pop af
	ret
AIResidual::
	call HandlePoisonBurnLeechSeed
	push af
	push bc
	ldh a, [hWhoseTurn]
	add 6
.AIEventHook_176
	db $ec
	pop bc
	pop af
	ret

AIDrawWaiting::
	ld a, [wAIWaiting]
	and a
	ret nz
	inc a
	ld [wAIWaiting], a
	call SaveScreenTilesToBuffer1
	hlcoord 4, 13
	lb bc, 1, 9
	call TextBoxBorder
	hlcoord 5, 14
	ld de, .text
	jp PlaceString
.text
	db "THINKING…@"
AIRestoreScreen::
	ld a, [wAIWaiting]
	and a
	ret z
	xor a
	ld [wAIWaiting], a
	call LoadScreenTilesFromBuffer1
	jp DrawHUDsAndHPBars
