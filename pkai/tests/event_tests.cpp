// Event vector (pkai/event.h) against ai/sim/trainer_env.py TrainerEnv._event and
// ai/models/pep_int.py IntPEP.quantize_event. The reference constants were produced by
//   cd ai && uv run python -c "..."   (EV_SCALE = 1/127, np.rint, clip to int8)
// over every 0 <= a <= b < 1000: FNV-1a of the int8 values, plus spot values and
// the round table min(r, 50)/50 for r in 0..60.
#include "pkai/tracker.h"
#include <array>
#include <cstdio>
#include <cstdlib>
#include <vector>
using namespace pkai;
using namespace pkai::symbols;
#define CHECK(x) do {if(!(x)){std::fprintf(stderr,"%d: %s\n",__LINE__,#x);std::exit(1);}}while(0)
static std::array<uint8_t,65536> ram;
static Memory mem() {
    return Memory{ram.data(), [](void* p,uint16_t a){return static_cast<uint8_t*>(p)[a];},
        [](void* p,uint16_t a,uint8_t v){static_cast<uint8_t*>(p)[a]=v;},nullptr,0};
}
static void word(uint16_t a,uint16_t v){ram[a]=uint8_t(v>>8);ram[a+1]=uint8_t(v);}
int main() {
    // Quantisation: exhaustive sweep hashed like the Python table.
    uint32_t h=2166136261u; unsigned count=0;
    for(uint32_t b=1;b<1000;++b) for(uint32_t a=0;a<=b;++a) {h=(h^uint8_t(event_q127(a,b)))*16777619u;++count;}
    CHECK(count==500499); CHECK(h==0xb2c59194u);
    const struct {uint32_t a,b;int8_t v;} spots[]={{1,254,0},{3,254,2},{5,254,2},{1,2,64},{127,254,64},{63,127,63},{100,999,13},{999,999,127},{0,7,0},{1,127,1}};
    for(auto& s:spots) CHECK(event_q127(s.a,s.b)==s.v);
    const int8_t rounds[61]={0,3,5,8,10,13,15,18,20,23,25,28,30,33,36,38,41,43,46,48,51,53,56,58,61,64,66,69,71,74,76,79,81,84,86,89,91,94,97,99,102,104,107,109,112,114,117,119,122,124,127,127,127,127,127,127,127,127,127,127,127};
    for(uint32_t r=0;r<61;++r) CHECK(event_q127(r<50?r:50,50)==rounds[r]);
    CHECK(event_q127(1,1)==127); CHECK(event_q127(5,0)==127); CHECK(event_q127(300,100)==127);   // flags, max(1,max), saturation

    // build_event on synthetic RAM.
    ram.fill(0); auto m=mem(); int8_t ev[EVENT_DIM];
    ram[wEnemyMonPartyPos]=1; word(wEnemyMonHP,80); word(wEnemyMonMaxHP,100); ram[wEnemyMonStatus]=0;
    ram[wPlayerMonNumber]=2; word(wBattleMonHP,50); word(wBattleMonMaxHP,200); ram[wBattleMonStatus]=0;
    EventState st{}; build_event(st,m,ev);
    for(auto v:ev) CHECK(v==0);                                   // before the first update: the sim's initial last_event
    st.commit(m,0); CHECK(st.decisions==1 && st.last_action==0 && st.side[0].hp==80 && st.side[1].max_hp==200);
    word(wEnemyMonHP,30); ram[wEnemyMonStatus]=0x40;               // enemy lost 50/100, got paralysed
    word(wBattleMonHP,0); ram[wBattleMonStatus]=0x03;              // player fainted (sleep counter set)
    build_event(st,m,ev);
    CHECK(ev[0]==127 && ev[1]==0 && ev[2]==0);
    CHECK(ev[3]==event_q127(50,100) && ev[3]==64); CHECK(ev[4]==0); CHECK(ev[5]==0); CHECK(ev[6]==127); CHECK(ev[7]==event_q127(30,100)); CHECK(ev[8]==127);
    CHECK(ev[9]==event_q127(50,200) && ev[9]==32 && ev[10]==127 && ev[11]==0 && ev[12]==127 && ev[13]==0 && ev[14]==127);
    CHECK(ev[15]==3); for(unsigned i=16;i<EVENT_DIM;++i) CHECK(ev[i]==0);
    // Replacement: loss is zero when the slot changed; status "went from none" needs pre == 0.
    ram[wPlayerMonNumber]=0; word(wBattleMonHP,10); word(wBattleMonMaxHP,40); ram[wBattleMonStatus]=0x08;
    st.commit(m,1); ram[wPlayerMonNumber]=3; word(wBattleMonHP,5); ram[wBattleMonStatus]=0x10;
    build_event(st,m,ev); CHECK(ev[1]==127 && ev[0]==0); CHECK(ev[9]==0 && ev[11]==127 && ev[12]==0 && ev[13]==event_q127(5,40) && ev[14]==127);
    CHECK(ev[15]==5);
    for(unsigned i=0;i<60;++i) st.commit(m,2); build_event(st,m,ev); CHECK(ev[15]==127 && ev[2]==127 && ev[0]==0);

    // The tracker takes the sim's PASS update on FaintPlayer only while the enemy is alive and not yet replaced.
    Tracker t{}; ram.fill(0); ram[wEnemyMonPartyPos]=1; word(wEnemyMonHP,20); word(wEnemyMonMaxHP,20); ram[wPlayerMonNumber]=0; word(wBattleMonMaxHP,50);
    t.event.commit(m,0); CHECK(t.event.decisions==1);
    t.capture(m,MoveEndPlayer,1); CHECK(t.event.decisions==1);
    t.capture(m,FaintPlayer,0); CHECK(t.event.decisions==2 && t.event.last_action==3 && t.event.side[0].hp==20);
    word(wEnemyMonHP,0); t.capture(m,FaintPlayer,0); CHECK(t.event.decisions==2);                          // enemy fainted too: a replacement decision follows
    word(wEnemyMonHP,20); ram[wEnemyMonPartyPos]=2; t.capture(m,FaintPlayer,0); CHECK(t.event.decisions==2); // enemy already replaced
    ram[wPlayerMonNumber]=1; word(wBattleMonHP,50); build_event(t.event,m,ev); CHECK(ev[0]==0 && ev[1]==0 && ev[2]==0 && ev[11]==127 && ev[13]==127 && ev[15]==5);
    t.reset(); CHECK(t.event.decisions==0);

    // Save-state round trip of the tracker carries the event state.
    Tracker a{}; a.event.commit(m,2); a.event.side[1].status=0x20;
    std::vector<uint8_t> out; {StateIO s(&out); a.serialize(s); CHECK(s.ok());}
    Tracker b{}; {StateIO s(out.data(),out.size()); b.serialize(s); CHECK(s.ok());}
    CHECK(b.event.decisions==1 && b.event.last_action==2 && b.event.side[0].slot==2 && b.event.side[0].hp==20 && b.event.side[1].status==0x20 && b.event.side[1].hp==50);
    out[out.size()-5]=9; {Tracker c{}; StateIO s(out.data(),out.size()); c.serialize(s); CHECK(!s.ok());}   // last_action range check
    std::puts("pkai_event_tests ok");
}
