AIDispatch::
	ld a, [wAIDisarmed]
	and a
	jp nz, TrainerAI
	ld a, [wIsInBattle]
	cp 2
	jr nz, .move
	ld a, [wLinkState]
	and a
	jr nz, .move
	ld a, [wTrainerClass]
	dec a
	cp NUM_TRAINERS
	jr nc, .move
	ld c, a
	ld b, 0
	ld hl, TrainerAIPointers
	add hl, bc
	add hl, bc
	add hl, bc
	ld a, [wAICount]
	cp $ff
	jr nz, .countReady
	ld a, [hl]
	ld [wAICount], a
.countReady
	ld a, [wAIAction]
	cp 1
	jr z, .switch
	cp 2
	jr nz, .move
	ld a, [wAICount]
	and a
	jr z, .move
	ld hl, AIItemTable
	add hl, bc
	add hl, bc
	add hl, bc
	ld a, [wAIAction + 1]
	cp [hl]
	jr nz, .move
	and a
	jr z, .move
	inc hl
	ld a, [hli]
	and a
	jr z, .status
	push hl
	call AICheckIfHPBelowFraction
	pop hl
	jr nc, .move
.status
	ld a, [hl]
	and a
	jr z, .item
	ld a, [wEnemyMonStatus]
	and a
	jr z, .move
.item
	ld a, [wAIAction + 1]
	ld hl, .items
	ld b, 9
.loop
	cp [hl]
	inc hl
	jr z, .use
	inc hl
	inc hl
	dec b
	jr nz, .loop
.move
	and a
	ret
.switch
	callfar AIValidateActionSwitch
	jr nc, .move
	ld a, [wAIAction + 1]
	ld [wAISwitchTarget], a
	jp SwitchEnemyMon
.use
	ld a, [hli]
	ld h, [hl]
	ld l, a
	jp hl
.items
	dbw FULL_RESTORE, AIUseFullRestore
	dbw POTION, AIUsePotion
	dbw SUPER_POTION, AIUseSuperPotion
	dbw HYPER_POTION, AIUseHyperPotion
	dbw FULL_HEAL, AIUseFullHeal
	dbw GUARD_SPEC, AIUseGuardSpec
	dbw X_ATTACK, AIUseXAttack
	dbw X_DEFEND, AIUseXDefend
	dbw X_SPEED, AIUseXSpeed

INCLUDE "data/trainers/ai_items.asm"
