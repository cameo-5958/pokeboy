#pragma once
#include <cstdint>
#include "state/state.h"
class Bus;
namespace gbmod { class Runtime; }

struct CPU {
    union { struct { uint8_t f, a; }; uint16_t af; };   // little-endian pairs
    union { struct { uint8_t c, b; }; uint16_t bc; };
    union { struct { uint8_t e, d; }; uint16_t de; };
    union { struct { uint8_t l, h; }; uint16_t hl; };
    uint16_t sp = 0, pc = 0;
    bool ime = false, halted = false, stopped = false;
    int  ei_delay = 0;
    Bus* bus = nullptr;
    gbmod::Runtime* mods = nullptr;

    enum { FZ = 0x80, FN = 0x40, FH = 0x20, FC = 0x10 };
    void set_flag(uint8_t fl, bool on) { f = on ? (f | fl) : (f & ~fl); f &= 0xF0; }
    bool flag(uint8_t fl) const { return f & fl; }

    int execute_next();                 // returns T-cycles

    // bus/mods are host-owned wiring, not machine state: they are rebound by
    // GameBoy::load_state and must never enter the stream.
    void serialize(StateIO& s) {
        s.v(af); s.v(bc); s.v(de); s.v(hl); s.v(sp); s.v(pc);
        s.v(ime); s.v(halted); s.v(stopped); s.v(ei_delay);
        if (!s.saving()) f &= 0xF0;     // low flag nibble is always clear on DMG
    }
private:
    bool handle_interrupts();
    int  exec_cb(uint8_t op);
    void alu(int op, uint8_t v);
    uint8_t rot(int kind, uint8_t v);   // RLC RRC RL RR SLA SRA SWAP SRL
    uint8_t inc8(uint8_t v);
    uint8_t dec8(uint8_t v);
    void    add_hl(uint16_t v);
    uint16_t sp_plus_r8();
    void    daa();
    uint8_t get_r(int i);
    void    set_r(int i, uint8_t v);
    uint8_t  fetch8();
    uint16_t fetch16();
    void     push16(uint16_t v);
    uint16_t pop16();
};
