#include "hook.h"
namespace pkai {
using namespace symbols;
uint32_t crc32(const uint8_t* bytes,size_t n) {
    uint32_t crc=~0u;
    for(size_t i=0;i<n;++i) {crc^=bytes[i];for(int j=0;j<8;++j) crc=(crc>>1)^(0xedb88320u & (0u-(crc&1)));}
    return ~crc;
}
void Hook::reset() {tracker.reset();scheduler.reset();battle=false;frame=0;}
void Hook::load_rom(const uint8_t* p,size_t n) {reset();recognised=crc32(p,n)==tables::rom_crc32;}
void Hook::opcode(const Memory& m,uint8_t op,uint8_t& a,uint8_t b) {
    if(!recognised) return;
    if(op==0xeb && !backend_available) {a=2;return;}
    if(m.read(wIsInBattle)!=2 || m.read(wLinkState)!=0) {if(op==0xeb)a=2;return;}
    if(op==0xdb) {
        tracker.reset();scheduler.reset();battle=true;
        // Emulator-visible DIV and cartridge RNG bytes supply battle entropy.
        scheduler.rng.seed(uint32_t(frame)^uint32_t(m.read(0xff04))<<24^uint32_t(m.read(hRandomAdd))<<8^m.read(hRandomSub));
    } else if(op==0xec && battle) tracker.capture(m,a,b);
    else if(op==0xeb) a=battle?scheduler.poll(m,tracker,a&0x7f,frame):2;
}
void Hook::serialize(StateIO& s) {s.v(frame);s.v(battle);tracker.serialize(s);scheduler.serialize(s);}
}
