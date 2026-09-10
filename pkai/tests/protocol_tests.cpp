#include "core.h"
#include "pkai/hook.h"
#include <fstream>
#include <iterator>
#include <iostream>
#include <cstdlib>
#include <cstring>
using namespace pkai;
using namespace pkai::symbols;
#define CHECK(x) do {if(!(x)){std::cerr<<__LINE__<<": "<<#x<<"\n";std::exit(1);}}while(0)
std::vector<uint8_t> rom() {std::ifstream f(AI_ROM,std::ios::binary);return {std::istreambuf_iterator<char>(f),{}};}
void word(GameBoy& g,uint16_t a,uint16_t v){g.bus.write8(a,v>>8);g.bus.write8(a+1,v);}
void setup(GameBoy& g,const std::vector<uint8_t>& r){
 CHECK(g.load_rom(r.data(),r.size()));g.reset_post_boot();
 g.bus.write8(wIsInBattle,2);g.bus.write8(wLinkState,0);g.bus.write8(wTrainerClass,1);
 g.bus.write8(wEnemyPartyCount,2);g.bus.write8(wPartyCount,2);g.bus.write8(wEnemyMonPartyPos,0);
 g.bus.write8(wEnemyMonMoves,33);g.bus.write8(wEnemyMonMoves+1,45);
 word(g,wEnemyMonHP,100);word(g,wEnemyMonMaxHP,100);
 word(g,wEnemyMon1HP,100);word(g,wEnemyMon2HP,100);
 word(g,wBattleMonHP,100);word(g,wBattleMonMaxHP,100);g.bus.write8(wBattleMonSpecies,153);
 g.bus.write8(wBattleMonLevel,10);g.bus.write8(wAICount,255);
}
void op(GameBoy& g,uint8_t opcode,uint8_t a){
 g.bus.write8(0xc000,opcode);g.cpu.pc=0xc000;g.cpu.a=a;g.cpu.f=0xb0;g.cpu.bc=0x1234;g.cpu.de=0x5678;g.cpu.hl=0x9abc;g.cpu.ime=false;g.cpu.halted=false;
 CHECK(g.cpu.execute_next()==4);CHECK(g.cpu.pc==0xc001);CHECK(g.cpu.f==0xb0);CHECK(g.cpu.bc==0x1234);CHECK(g.cpu.de==0x5678);CHECK(g.cpu.hl==0x9abc);
}
int main(){
 auto r=rom();CHECK(r.size()>0);CHECK(crc32(r.data(),r.size())==tables::rom_crc32);
 auto g=std::make_unique<GameBoy>();setup(*g,r);CHECK(g->ai.recognised);
 for(bool custom:{true,false}){if(custom)g->reset_custom_boot();else g->reset_post_boot();CHECK(g->cpu.ai==&g->ai);CHECK(g->cpu.ai_memory==&g->ai_bus);}
 setup(*g,r);op(*g,0xdb,0x87);CHECK(g->cpu.a==0x87 && g->ai.battle);
 op(*g,0xd3,0x87);CHECK(g->cpu.a==0x87);
 auto m=g->ai_memory();auto o=observe(m,g->ai.tracker);CHECK(o.count==3);
 auto mask=legal_mask(m,o,0);CHECK(mask.bits==0x23);
 for(unsigned disable=0;disable<=4;++disable){o.disabled=disable;auto a=legal_mask(m,o,0);CHECK(a.legal(0)==(disable!=1));CHECK(a.legal(1)==(disable!=2));}
 o.active.moves[1]=0;o.disabled=1;mask=legal_mask(m,o,0);CHECK(mask.struggle && mask.legal(0));
 o=observe(m,g->ai.tracker);mask=legal_mask(m,o,1);CHECK(mask.bits==0x20);
 for(unsigned cls=1;cls<=47;++cls){g->bus.write8(wTrainerClass,cls);g->bus.write8(wEnemyMonStatus,8);word(*g,wEnemyMonHP,1);o=observe(m,g->ai.tracker);mask=legal_mask(m,o,0);
   auto id=m.table(tables::AIItemTable,3*(cls-1));CHECK(bool(mask.bits&0xfc00)==bool(id));
   g->bus.write8(wAICount,0);CHECK(!(legal_mask(m,observe(m,g->ai.tracker),0).bits&0xfc00));g->bus.write8(wAICount,255);
 }
 setup(*g,r);op(*g,0xdb,0);op(*g,0xec,RoundBegin);CHECK(g->ai.tracker.round==1);
 op(*g,0xec,MoveBeginPlayer);g->bus.write8(wPlayerMoveNum,33);op(*g,0xec,AnnouncedPlayer);
 word(*g,wEnemyMonHP,0);g->bus.write8(wEnemySubstituteHP,0);op(*g,0xec,MoveEndPlayer);
 const auto& e=g->ai.tracker.events[(g->ai.tracker.head+63)%64];CHECK(e.hp_loss[1]==100 && e.move==33 && e.announced);
 CHECK(g->ai.tracker.player[0].moves[0]==33);
 // CANNOT_MOVE must not reveal stale move/outcome flags.
 op(*g,0xec,MoveBeginPlayer);g->bus.write8(wPlayerMoveNum,99);op(*g,0xec,MoveEndPlayer);
 CHECK(!g->ai.tracker.events[(g->ai.tracker.head+63)%64].outcome_valid);CHECK(g->ai.tracker.player[0].moves[1]==0);
 g->bus.write8(wEnemyBattleStatus2,16);g->bus.write8(wEnemySubstituteHP,20);op(*g,0xec,MoveBeginPlayer);
 op(*g,0xec,AnnouncedPlayer);g->bus.write8(wEnemyBattleStatus2,0);op(*g,0xec,MoveEndPlayer);
 CHECK(g->ai.tracker.events[(g->ai.tracker.head+63)%64].substitute_loss[1]==20);
 for(int i=0;i<80;++i)op(*g,0xec,PlayerItem);CHECK(g->ai.tracker.overflow && g->ai.tracker.pending==64);
 setup(*g,r);op(*g,0xdb,0);op(*g,0xeb,0x80);CHECK(g->cpu.a==0 && g->ai.scheduler.pending());
 auto steps=g->ai.scheduler.compute_steps;g->ai_step(monotonic_ns()-1);CHECK(g->ai.scheduler.compute_steps==steps);
 std::vector<uint8_t> pending,ready;CHECK(g->save_state(pending));
 g->ai_step(monotonic_ns()+100000000);CHECK(g->ai.scheduler.stage==Scheduler::Ready);auto action=g->ai.scheduler.request.result;CHECK(g->save_state(ready));
 op(*g,0xeb,0x80);CHECK(g->cpu.a==1);
 CHECK(g->load_state(pending.data(),pending.size()));g->ai_step(monotonic_ns()+100000000);CHECK(g->ai.scheduler.request.result.kind==action.kind && g->ai.scheduler.request.result.payload==action.payload);
 CHECK(g->load_state(ready.data(),ready.size()));op(*g,0xeb,0x80);CHECK(g->cpu.a==1 && g->bus.read8(wAIAction+1)==action.payload);
 CHECK(g->load_state(pending.data(),pending.size()));
 for(int i=0;i<300;++i){op(*g,0xeb,0x80);CHECK(g->cpu.a==0);++g->ai.frame;}
 op(*g,0xeb,0x80);CHECK(g->cpu.a==1 && g->ai.scheduler.timeouts==1);
 // Materialise Struggle in the real protocol cells; enemy PP is irrelevant.
 setup(*g,r);g->bus.write8(wEnemyPartyCount,1);g->bus.write8(wEnemyMonMoves+1,0);g->bus.write8(wEnemyDisabledMove,16);
 op(*g,0xdb,0);op(*g,0xeb,0x80);g->ai_step(monotonic_ns()+100000000);op(*g,0xeb,0x80);
 CHECK(g->cpu.a==1 && g->bus.read8(wEnemySelectedMove)==165 && g->bus.read8(wEnemyMoveListIndex)==0);
 g->ai.backend_available=false;auto cell=g->bus.read8(wAIAction);op(*g,0xeb,0x80);CHECK(g->cpu.a==2 && g->bus.read8(wAIAction)==cell);
 // Whole-ROM mismatch, including a mutation far from the cartridge header.
 r.back()^=1;setup(*g,r);CHECK(!g->ai.recognised);g->bus.write8(wAIAction,77);
 for(auto opcode:{0xdb,0xec,0xeb}){op(*g,opcode,0x80);CHECK(g->cpu.a==0x80 && g->bus.read8(wAIAction)==77);}
 std::vector<uint8_t> bytes;StateIO io(&bytes);g->ai.serialize(io);CHECK(bytes.size()<8192);
 std::cout<<"protocol, masks, tracker, starvation and transactional restore passed; AI state "<<bytes.size()<<" bytes\n";
}
