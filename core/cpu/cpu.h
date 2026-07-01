#pragma once
#include <cstdint>
class Bus;

struct CPU {
    union { struct { uint8_t f, a; }; uint16_t af; };   // little-endian pairs
    union { struct { uint8_t c, b; }; uint16_t bc; };
    union { struct { uint8_t e, d; }; uint16_t de; };
    union { struct { uint8_t l, h; }; uint16_t hl; };
    uint16_t sp = 0, pc = 0;
    bool ime = false, halted = false;
    int  ei_delay = 0;
    Bus* bus = nullptr;

    enum { FZ = 0x80, FN = 0x40, FH = 0x20, FC = 0x10 };
    void set_flag(uint8_t fl, bool on) { f = on ? (f | fl) : (f & ~fl); f &= 0xF0; }
    bool flag(uint8_t fl) const { return f & fl; }

    int execute_next();                 // returns T-cycles
private:
    bool handle_interrupts();
    int  exec_cb(uint8_t op);
    void alu(int op, uint8_t v);
    uint8_t get_r(int i);
    void    set_r(int i, uint8_t v);
    uint8_t  fetch8();
    uint16_t fetch16();
    void     push16(uint16_t v);
    uint16_t pop16();
};
