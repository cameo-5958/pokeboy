#include "tracker.h"
#include <algorithm>
namespace pkai {
using namespace symbols;
uint8_t public_status(uint8_t raw) { return raw & 7 ? 1 : raw & 0xf8; }
void PublicMon::serialize(StateIO& s) {
    s.v(known); s.v(species); s.v(level); s.v(status); s.arr(types); s.arr(moves); s.arr(revealed_moves);
    s.v(hp); s.v(max_hp); s.v(observed_round);
}
void Sample::serialize(StateIO& s) { s.arr(hp); s.arr(substitute); s.arr(status); s.arr(stages); }
void Event::serialize(StateIO& s) {
    s.v(kind); s.v(actor); s.v(move); s.v(crit); s.v(miss); s.v(effectiveness);
    s.v(announced); s.v(outcome_valid); s.v(defender_fainted); s.arr(hp_loss);
    s.arr(substitute_loss); s.v(last_damage); s.arr(stage_delta);
    s.arr(status_before); s.arr(status_after); s.v(round);
}
Sample sample(const Memory& m) {
    Sample x{};
    for (int side=0; side<2; ++side) {
        auto base=side ? wEnemyMon : wBattleMon;
        x.hp[side]=m.word(base + wBattleMonHP-wBattleMon);
        x.status[side]=public_status(m.read(base+wBattleMonStatus-wBattleMon));
        x.substitute[side]=m.read(side ? wEnemySubstituteHP : wPlayerSubstituteHP);
        // A broken Substitute may leave a stale HP byte behind.
        if (!(m.read(side ? wEnemyBattleStatus2 : wPlayerBattleStatus2)&0x10)) x.substitute[side]=0;
        for(int j=0;j<6;++j) x.stages[side][j]=m.read((side?wEnemyMonStatMods:wPlayerMonStatMods)+j);
    }
    return x;
}
void Tracker::capture(const Memory& m, uint8_t kind, uint8_t b) {
    if (kind > PlayerItem || kind==1) return;
    if (kind==RoundBegin) ++round;
    const auto now=sample(m);
    const auto slot=m.read(wPlayerMonNumber);
    if(slot<player.size()) {
        auto& p=player[slot]; p.known=true;
        p.species=m.read(wBattleMonSpecies); p.level=m.read(wBattleMonLevel);
        p.hp=now.hp[0]; p.max_hp=m.word(wBattleMonMaxHP); p.status=now.status[0];
        p.types[0]=m.read(wBattleMonType1); p.types[1]=m.read(wBattleMonType2);
        p.observed_round=round;
        if(kind==AnnouncedPlayer) {
            auto move=m.read(wPlayerMoveNum);
            if(move && move<=165) p.revealed_moves[move/8]|=1u<<(move%8);
            if(move && std::find(std::begin(p.moves),std::end(p.moves),move)==std::end(p.moves))
                for(auto& v:p.moves) if(!v) { v=move; break; }
        }
    }
    Event e{}; e.kind=kind; e.actor=kind&1; e.round=round;
    if(kind==MoveBeginPlayer || kind==MoveBeginEnemy) {
        before=now; actor=kind&1; move_open=true; announced=false; announced_move=0;
    }
    if(kind==AnnouncedPlayer || kind==AnnouncedEnemy) {
        e.move=m.read(kind==AnnouncedPlayer?wPlayerMoveNum:wEnemyMoveNum);
        e.announced=true;
        if(move_open && actor==(kind&1)) { announced=true; announced_move=e.move; }
    }
    if((kind==MoveEndPlayer || kind==MoveEndEnemy) && move_open && actor==(kind&1)) {
        e.announced=announced; e.move=announced_move; e.outcome_valid=announced;
        e.defender_fainted=b==0;
        for(int i=0;i<2;++i) {
            e.hp_loss[i]=before.hp[i]>now.hp[i]?before.hp[i]-now.hp[i]:0;
            e.substitute_loss[i]=before.substitute[i]>now.substitute[i]?before.substitute[i]-now.substitute[i]:0;
            e.status_before[i]=before.status[i]; e.status_after[i]=now.status[i];
            for(int j=0;j<6;++j) e.stage_delta[i][j]=int(now.stages[i][j])-before.stages[i][j];
        }
        if(announced) {
            e.last_damage=m.word(wDamage); e.crit=m.read(wCriticalHitOrOHKO);
            e.miss=m.read(wMoveMissed); e.effectiveness=m.read(wTypeEffectiveness);
        }
        move_open=false;
    }
    // The sim advances one update without a trainer decision when only the player's
    // mon fainted (trainer PASS, player SWITCH); mirror its snapshot here. A fainted
    // or already replaced enemy means a replacement decision follows instead.
    if(kind==FaintPlayer && m.word(wEnemyMonHP)!=0 && m.read(wEnemyMonPartyPos)==event.side[0].slot) event.auto_step(m);
    if(pending==64) { overflow=true; ++dropped; hidden.fill(0); --pending; }
    events[head]=e; head=(head+1)%64; if(size<64) ++size;
    ++pending; ++event_count;
}
void Tracker::serialize(StateIO& s) {
    for(auto& p:player) p.serialize(s);
    for(auto& e:events) e.serialize(s);
    for(auto& h:hidden) s.v(h);
    before.serialize(s); s.v(round); s.v(event_count); s.v(dropped);
    s.v(head); s.v(size); s.v(pending); s.v(actor); s.v(announced_move);
    s.v(move_open); s.v(announced); s.v(overflow);
    event.serialize(s);
    if(head>=64 || size>64 || pending>64 || actor>1) s.fail();
}
}
