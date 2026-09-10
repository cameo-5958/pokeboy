#include "observe.h"
#include <algorithm>
namespace pkai {
using namespace symbols;
static OwnMon mon(const Memory& m, uint16_t base, bool battle) {
    OwnMon p{};
    p.species=m.read(base); p.hp=m.word(base+1); p.status=public_status(m.read(base+4));
    p.types[0]=m.read(base+5); p.types[1]=m.read(base+6);
    for(int i=0;i<4;++i) p.moves[i]=m.read(base+8+i);
    const auto level=battle?wEnemyMonLevel-wEnemyMon:wEnemyMon1Level-wEnemyMon1;
    const auto dv=battle?wEnemyMonDVs-wEnemyMon:wEnemyMon1DVs-wEnemyMon1;
    p.level=m.read(base+level); p.max_hp=m.word(base+level+1);
    p.dvs[0]=m.read(base+dv); p.dvs[1]=m.read(base+dv+1);
    for(int i=0;i<4;++i) p.stats[i]=m.word(base+level+3+2*i);
    return p;
}
Observation observe(const Memory& m, const Tracker& t) {
    Observation o{}; o.player=t.player; o.round=t.round;
    o.own_count=std::min<uint8_t>(6,m.read(wEnemyPartyCount));
    o.player_count=std::min<uint8_t>(6,m.read(wPartyCount));
    o.own_slot=m.read(wEnemyMonPartyPos); o.player_slot=m.read(wPlayerMonNumber);
    o.trainer_class=m.read(wTrainerClass); o.count=m.read(wAICount); o.disabled=m.read(wEnemyDisabledMove)>>4;
    if(o.count==255) o.count=o.trainer_class>=1 && o.trainer_class<=tables::trainer_classes ? m.table(tables::TrainerAIPointers,3*(o.trainer_class-1)):0;
    for(int i=0;i<o.own_count;++i) o.own[i]=mon(m,wEnemyMons+i*(wEnemyMon2-wEnemyMon1),false);
    o.active=mon(m,wEnemyMon,true);
    for(int i=0;i<6;++i) { o.stages[i]=m.read(wEnemyMonStatMods+i); o.player_stages[i]=m.read(wPlayerMonStatMods+i); }
    for(int i=0;i<3;++i) o.battle_status[i]=m.read(wEnemyBattleStatus1+i);
    // Only public flags: confusion, Bide, trapping; Mist/Focus/Substitute;
    // screens, Leech Seed and Transform. Never sleep counters or hidden stats.
    o.player_visible_status[0]=m.read(wPlayerBattleStatus1)&0xa3;
    o.player_visible_status[1]=m.read(wPlayerBattleStatus2)&0x96;
    o.player_visible_status[2]=m.read(wPlayerBattleStatus3)&0x0e;
    o.substitute=m.read(wEnemySubstituteHP);
    o.confusion_counter=m.read(wEnemyConfusedCounter); o.toxic_counter=m.read(wEnemyToxicCounter);
    return o;
}
}
