#include "mask.h"
namespace pkai {
static int item_slot(uint8_t id) {
    switch(id) { case 0x10: case 0x14: case 0x13: case 0x12:return 10;
    case 0x34:return 11;case 0x37:return 12;case 0x41:return 13;case 0x42:return 14;case 0x43:return 15;default:return -1; }
}
Mask legal_mask(const Memory& m,const Observation& o,uint8_t kind) {
    Mask mask{};
    for(unsigned i=0;i<o.own_count;++i) if(i!=o.own_slot && o.own[i].hp) mask.bits|=1u<<(4+i);
    if(kind==1) return mask;
    for(unsigned i=0;i<4;++i) if(o.active.moves[i] && i+1!=o.disabled) mask.bits|=1u<<i;
    if(!(mask.bits&15)) {mask.bits|=1;mask.struggle=true;}
    if(o.trainer_class<1 || o.trainer_class>tables::trainer_classes || !o.count) return mask;
    unsigned row=3*(o.trainer_class-1);
    uint8_t id=m.table(tables::AIItemTable,row), divisor=m.table(tables::AIItemTable,row+1), status=m.table(tables::AIItemTable,row+2);
    // Native comparison is HP < floor(maxHP / divisor), not HP*divisor < maxHP.
    if(divisor && o.active.hp>=o.active.max_hp/divisor) return mask;
    if(status && !o.active.status) return mask;
    int slot=item_slot(id);
    if(slot>=0) {mask.item=id;mask.bits|=1u<<slot;}
    return mask;
}
static Action action_at(const Mask& mask,unsigned i,uint8_t sequence) {
    return {uint8_t(i<4?0:i<10?1:2),uint8_t(i<4?i:i<10?i-4:mask.item),sequence};
}
Action random_action(const Mask& mask,uint32_t draw,uint8_t sequence) {
    unsigned n=0; for(unsigned i=0;i<16;++i) n+=mask.legal(i);
    if(!n) return {255,0,sequence};
    unsigned k=(uint64_t(draw)*n)>>32;
    for(unsigned i=0;i<16;++i) if(mask.legal(i) && k--==0) return action_at(mask,i,sequence);
    return {255,0,sequence};
}
Action sample_action(const Mask& mask,const uint8_t* probs,uint32_t draw,uint8_t sequence) {
    uint32_t total=0; for(unsigned i=0;i<16;++i) if(mask.legal(i)) total+=probs[i];
    if(!total) return random_action(mask,draw,sequence);
    uint32_t k=uint32_t((uint64_t(draw)*total)>>32);   // uniform in [0,total)
    for(unsigned i=0;i<16;++i) if(mask.legal(i)) { if(k<probs[i]) return action_at(mask,i,sequence); k-=probs[i]; }
    return random_action(mask,draw,sequence);
}
bool legal_action(const Mask& m,const Action& a) {
    if(a.kind==0) return a.payload<4 && m.legal(a.payload);
    if(a.kind==1) return a.payload<6 && m.legal(4+a.payload);
    int slot=item_slot(a.payload);
    return a.kind==2 && a.payload==m.item && slot>=0 && m.legal(slot);
}
}
