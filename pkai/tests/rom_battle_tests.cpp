// Save-state fixture construction executes the ROM's own AddPartyMon and
// InitBattle routines. No ROM byte is patched and all battle logic is native.
#include "core.h"
#include "pkai/features.h"
#include <fstream>
#include <iterator>
#include <iostream>
#include <map>
#include <string>
#include <cstdlib>
using namespace pkai;
using namespace pkai::symbols;
#define CHECK(x) do {if(!(x)){std::cerr<<__LINE__<<": "<<#x<<" pc="<<std::hex<<g.cpu.pc<<std::dec<<"\n";std::exit(1);}}while(0)
struct Fixture {
 GameBoy g;
 std::map<std::string,std::pair<unsigned,unsigned>> sym;
 uint64_t cycles=0;
 bool auto_input=true, auto_ai=true;
 uint32_t seen[15]{};
 uint32_t replacement_decisions{}, turn_decisions{};
 std::vector<uint8_t> start_state;
 bool expected_ai=true;
 Fixture(bool recognised=true):expected_ai(recognised){
  std::ifstream f(AI_ROM,std::ios::binary);std::vector<uint8_t> r{std::istreambuf_iterator<char>(f),{}};
  if(!recognised) r.back()^=1;
  CHECK(g.load_rom(r.data(),r.size()));g.reset_post_boot();
  std::ifstream sf(AI_SYM);std::string line;
  while(std::getline(sf,line)){unsigned bank,addr;char name[256];if(sscanf(line.c_str(),"%x:%x %255s",&bank,&addr,name)==3)sym[name]={bank,addr};}
 }
 uint16_t addr(const std::string& n){auto it=sym.find(n);CHECK(it!=sym.end());return it->second.second;}
 void put(const std::string& n,uint8_t v){g.bus.write8(addr(n),v);}
 void jump(const std::string& n){auto v=sym.at(n);if(v.first){g.bus.write8(0x2000,v.first);put("hLoadedROMBank",v.first);}g.cpu.pc=v.second;g.cpu.halted=false;g.cpu.stopped=false;}
 bool at(const std::string& n){auto a=sym.at(n);return g.cpu.pc==a.second && (a.first==0 || g.cart->mapped_rom_offset(g.cpu.pc)/0x4000==a.first);}
 void until(const std::string& n,unsigned max_frames=4000){auto end=g.ai.frame+max_frames;while(!at(n) && g.ai.frame<end)tick();CHECK(at(n));}
 void restore(){CHECK(g.load_state(start_state.data(),start_state.size()));cycles=0;}
 void tick(){
  if(g.bus.read8(g.cpu.pc)==0xec && g.ai.battle && g.cpu.a<15) ++seen[g.cpu.a];
  const auto opcode=g.bus.read8(g.cpu.pc);
  int t=g.cpu.execute_next();
  if(opcode==0xeb && g.cpu.a==1) {
    auto& sched=g.ai.scheduler;
    CHECK(legal_action(legal_mask(g.ai_memory(),observe(g.ai_memory(),g.ai.tracker),sched.request.kind),sched.request.result));
    CHECK(sched.request.visible_frames<=300);
    if(sched.request.kind==1)++replacement_decisions;else ++turn_decisions;
  }g.ppu.tick(t,g.bus);g.timer.tick(t,g.bus);g.apu.tick(t);cycles+=t;
  if(cycles>=70224){cycles-=70224;++g.ai.frame;if(auto_input)g.set_input((g.ai.frame%8)<4?1:0,0);if(auto_ai)g.ai_step(monotonic_ns()+4000000);}
 }
 void begin_call(const std::string& n){g.cpu.sp=0xdff0;g.bus.write8(--g.cpu.sp,0xc1);g.bus.write8(--g.cpu.sp,0x00);jump(n);}
 void finish_call(){for(unsigned i=0;i<20000000;++i){if(g.cpu.pc==0xc100)return;tick();}CHECK(false);}
 void call(const std::string& n){begin_call(n);finish_call();}
 void prepare(){
  for(int i=0;i<400;++i)g.run_frame();
  put("wPartyCount",0);put("wCurPartySpecies",0xb0);put("wCurEnemyLevel",25);put("wMonDataLocation",16);call("AddPartyMon");
  put("wCurPartySpecies",0x99);put("wCurEnemyLevel",25);put("wMonDataLocation",16);call("AddPartyMon");
  // Put Ember first in this deterministic, legal fixture.
  put("wPartyMon1Moves",52);
  put("wCurOpponent",201);put("wTrainerNo",1);put("wOptions",0xc1);put("wCurMap",0);put("wLinkState",0);
  put("wPlayerName",0x80);g.bus.write8(addr("wPlayerName")+1,0x50);
  g.cpu.sp=0xdff0;g.bus.write8(--g.cpu.sp,0xc1);g.bus.write8(--g.cpu.sp,0);jump("InitBattle");
  until("MainInBattleLoop");CHECK(g.ai.battle==expected_ai);CHECK(g.ai.scheduler.request.sequence==0);CHECK(g.save_state(start_state));
 }
 void scenarios(){
  // Forced native states must not request inference or dispatch an item/switch.
  for(auto pair : {std::pair<const char*,int>{"wEnemyBattleStatus2",32}, {"wEnemyBattleStatus2",64},
       {"wEnemyBattleStatus1",1},{"wEnemyBattleStatus1",2},{"wEnemyBattleStatus1",16},{"wEnemyBattleStatus1",32},
       {"wEnemyMonStatus",1},{"wEnemyMonStatus",32},{"wPlayerBattleStatus1",32}}) {
    restore();put(pair.first,pair.second);auto seq=g.ai.scheduler.request.sequence;call("AISelect");
    CHECK(g.ai.scheduler.request.sequence==seq);CHECK(g.bus.read8(wAIAction)==0);CHECK(g.bus.read8(wAIDisarmed)==0);
  }
  restore();until("MoveSelectionMenu");auto round=g.ai.tracker.round;
  auto_input=false;g.set_input(2,0);until("MainInBattleLoop");CHECK(g.ai.tracker.round==round);
  g.set_input(0,0);auto_input=true;until("AISelect");CHECK(g.ai.tracker.round==round);
  // Native send-out paths consume a latched, validated target.
  restore();put("wAIAction",1);g.bus.write8(wAIAction+1,1);
  auto switches=seen[SwitchEnemy];auto seq=g.ai.scheduler.request.sequence;call("AIDispatch");CHECK(g.ai.scheduler.request.sequence==seq);CHECK(g.bus.read8(wEnemyMonPartyPos)==1);CHECK(g.bus.read8(wAISwitchTarget)==255);CHECK(seen[SwitchEnemy]==switches+1);
  // Invalid cell must clear carry and leave the active unchanged.
  for(unsigned slot:{0u,6u,255u}){restore();put("wAIAction",1);g.bus.write8(wAIAction+1,slot);call("AIDispatch");CHECK(!g.cpu.flag(CPU::FC));CHECK(g.bus.read8(wEnemyMonPartyPos)==0);}
  restore();put("wWhichPokemon",1);auto ps=seen[SwitchPlayer];call("SwitchPlayerMon");CHECK(g.bus.read8(wPlayerMonNumber)==1);CHECK(seen[SwitchPlayer]==ps+1);CHECK(g.ai.tracker.player[1].known);
  // Potion from the player's bag consumes a turn and emits its own event.
  restore();put("wNumBagItems",1);put("wBagItems",20);g.bus.write8(addr("wBagItems")+1,3);g.bus.write8(addr("wBagItems")+2,255);
  put("wCurItem",20);put("wCurrentMenuItem",0);put("wWhichPokemon",0);
  g.bus.write8(wBattleMonHP,0);g.bus.write8(wBattleMonHP+1,10);g.bus.write8(wPartyMon1HP,0);g.bus.write8(wPartyMon1HP+1,10);
  auto items=seen[PlayerItem];call("UseBagItem");CHECK(seen[PlayerItem]==items+1);CHECK(g.ai_memory().word(wBattleMonHP)>10);
  // ROM item validation: normalise $ff, enforce the native HP threshold,
  // then execute Potion and decrement the per-send-out count.
  restore();put("wTrainerClass",42);put("wAICount",255);put("wAIAction",2);g.bus.write8(wAIAction+1,20);
  g.bus.write8(wEnemyMonHP,0);g.bus.write8(wEnemyMonHP+1,1);call("AIDispatch");CHECK(g.cpu.flag(CPU::FC));CHECK(g.bus.read8(wAICount)==0);CHECK(g.ai_memory().word(wEnemyMonHP)>1);
  restore();put("wAIAction",2);g.bus.write8(wAIAction+1,20);call("AIDispatch");CHECK(!g.cpu.flag(CPU::FC));CHECK(g.bus.read8(wAICount)==3);
  // Native Substitute creation, damage to it, then a KO: trap wrappers must
  // retain the returned B and measure HP/Substitute independently.
  restore();put("wEnemySelectedMove",164);call("AIExecuteEnemyMove");
  CHECK(g.bus.read8(wEnemyBattleStatus2)&16);auto sub=g.bus.read8(wEnemySubstituteHP);CHECK(sub>0);
  auto hp=g.ai_memory().word(wEnemyMonHP);put("wPlayerSelectedMove",52);put("wPlayerMoveListIndex",0);call("AIExecutePlayerMove");
  const auto e=g.ai.tracker.events[(g.ai.tracker.head+63)%64];CHECK(e.kind==MoveEndPlayer && e.announced);CHECK(e.substitute_loss[1]==sub);CHECK(e.hp_loss[1]==0);CHECK(g.ai_memory().word(wEnemyMonHP)==hp);
  put("wPlayerSelectedMove",52);call("AIExecutePlayerMove");
  const auto ko=g.ai.tracker.events[(g.ai.tracker.head+63)%64];CHECK(ko.defender_fainted && g.cpu.b==0);CHECK(ko.hp_loss[1]==hp);
  // Exercise the actual waiting loop with no compute slices at all.
  restore();auto_ai=false;begin_call("AISelect");until("AIInferHook");tick();CHECK(g.ai.scheduler.pending());
  std::vector<uint8_t> pending,ready;CHECK(g.save_state(pending));auto first=g.ai.frame;
  finish_call();CHECK(g.ai.frame-first<=302);CHECK(g.ai.scheduler.timeouts==1);CHECK(g.bus.read8(wAIWaiting)==0);
  CHECK(g.load_state(pending.data(),pending.size()));g.ai_step(monotonic_ns()+100000000);CHECK(g.ai.scheduler.stage==Scheduler::Ready);auto result=g.ai.scheduler.request.result;CHECK(g.save_state(ready));finish_call();
  CHECK(g.load_state(ready.data(),ready.size()));finish_call();CHECK(g.bus.read8(wAIAction)==result.kind && g.bus.read8(wAIAction+1)==result.payload);
  CHECK(g.load_state(pending.data(),pending.size()));g.ai_step(monotonic_ns()+100000000);finish_call();CHECK(g.bus.read8(wAIAction)==result.kind && g.bus.read8(wAIAction+1)==result.payload);
  auto_ai=true;
  // Entity features: fixed layout, candidates wired to the 16-way head, deterministic hash.
  restore();
  {
    auto mem=g.ai_memory();auto obs=observe(mem,g.ai.tracker);auto legal=legal_mask(mem,obs,0);
    auto F=build_features(mem,obs,legal,0);
    CHECK(F.count==27);CHECK(F.legal==legal.bits);
    CHECK(F.tokens[0].type==TokField);
    for(unsigned i=0;i<6;++i){CHECK(F.tokens[1+i].type==TokOwnMon && F.tokens[1+i].candidate==5+i);CHECK(F.tokens[7+i].type==TokPlayerMon);CHECK(F.tokens[21+i].type==TokItem && F.tokens[21+i].candidate==11+i);}
    for(unsigned i=0;i<4;++i){CHECK(F.tokens[13+i].type==TokOwnMove && F.tokens[13+i].candidate==1+i);CHECK(F.tokens[17+i].type==TokPlayerMove);}
    CHECK(F.tokens[1].present && F.tokens[1].f[0]==127);           // own active present and flagged active
    CHECK(F.tokens[13].present && F.tokens[13].f[18]==127);        // first own move is damaging (Ember-class fixture)
    CHECK(F.tokens[13].f[5]>0 && F.tokens[13].f[8]>0);             // max damage fraction and hit rate populated
    CHECK(F.tokens[7].present && F.tokens[7].f[1]==127);           // player active is known
    CHECK(features_hash(F)==features_hash(build_features(mem,obs,legal,0)));
    { // Fixture dump for the Python binding parity test (ROM tables only, no RAM reads).
      Memory tables{nullptr,[](void*,uint16_t)->uint8_t{return 0;},[](void*,uint16_t,uint8_t){},mem.rom,mem.rom_size};
      auto ref=build_features(tables,obs,legal_mask(tables,obs,0),0);
      std::ofstream ob("pkai_fixture_observation.bin",std::ios::binary);ob.write(reinterpret_cast<const char*>(&obs),sizeof obs);
      std::ofstream hb("pkai_fixture_hash.txt");hb<<features_hash(ref)<<"\n";
    }
    g.bus.write8(wEnemyMonHP+1,1);auto obs2=observe(mem,g.ai.tracker);
    CHECK(features_hash(build_features(mem,obs2,legal_mask(mem,obs2,0),0))!=features_hash(F));
  }
  // Recognised ROM without backend: native selection and original TrainerAI.
  restore();g.ai.backend_available=false;call("AISelect");CHECK(g.bus.read8(wAIDisarmed)==1);CHECK(!g.ai.scheduler.pending());call("AIDispatch");CHECK(!g.cpu.flag(CPU::FC));g.ai.backend_available=true;
  restore();
 }
 void battle(){
  bool began=false;unsigned frames=0;uint64_t prev=0;
  for(;frames<18000;++frames){
   g.set_input((frames%8)<4?1:0,0);
   auto stop=g.ai.frame+1;while(g.ai.frame<stop && g.cpu.pc!=0xc100)tick();
   began|=g.ai.battle;
   if(g.ai.scheduler.completions!=prev){prev=g.ai.scheduler.completions;std::cout<<"decision "<<prev<<" round "<<g.ai.tracker.round<<" action "<<int(g.ai.scheduler.request.result.kind)<<":"<<int(g.ai.scheduler.request.result.payload)<<" playerMove "<<int(g.bus.read8(wPlayerMoveNum))<<" HP "<<g.ai_memory().word(wBattleMonHP)<<":"<<g.ai_memory().word(wEnemyMonHP)<<" slot "<<int(g.bus.read8(wEnemyMonPartyPos))<<"\n";}
   if(g.cpu.pc==0xc100)break;
  }
  std::cout<<"frames="<<frames<<" began="<<began<<" rounds="<<g.ai.tracker.round<<" events="<<g.ai.tracker.event_count<<" decisions="<<prev<<" pc="<<std::hex<<g.cpu.pc<<std::dec<<"\n";
  CHECK(began==expected_ai);CHECK(frames<18000);
  if(expected_ai){CHECK(prev>0);CHECK(g.ai.tracker.round>0);CHECK(seen[FaintEnemy]>0);CHECK(seen[AnnouncedPlayer]>0);CHECK(seen[MoveEndPlayer]>0);CHECK(replacement_decisions>0 && turn_decisions>0);}
  else {CHECK(prev==0);CHECK(g.ai.tracker.event_count==0);CHECK(g.bus.read8(wAIDisarmed)==1);}
 }
};
int main(){auto fixture=std::make_unique<Fixture>();fixture->prepare();fixture->scenarios();fixture->battle();
 auto unknown=std::make_unique<Fixture>(false);unknown->prepare();unknown->battle();}
