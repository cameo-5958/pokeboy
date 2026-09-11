#pragma once
#include "scheduler.h"
namespace pkai {
uint32_t crc32(const uint8_t*,size_t);
class Hook {
public:
    Tracker tracker;
    Scheduler scheduler;
    bool recognised=false, backend_available=true, battle=false;
    uint64_t frame=0;
    void load_rom(const uint8_t*,size_t);
    void reset();
    void opcode(const Memory&,uint8_t opcode,uint8_t& a,uint8_t b);
    void step(const Memory& m,int64_t deadline) { if(recognised && backend_available && battle) scheduler.step(m,tracker,deadline); }
    // Loads pkai.weights and switches decisions to the PEP model; false (and
    // scheduler.weights_error()) leaves the random backend in place.
    bool load_weights(const char* path) { return scheduler.load_weights(path); }
    void serialize(StateIO&);
};
}
