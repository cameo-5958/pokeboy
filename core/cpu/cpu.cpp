#include "cpu.h"
#include "bus/bus.h"
#include <cstdio>
#include <cstdlib>

uint8_t  CPU::fetch8()  { return bus->read8(pc++); }
uint16_t CPU::fetch16() { uint16_t v = bus->read16(pc); pc += 2; return v; }
void     CPU::push16(uint16_t v) { sp -= 2; bus->write16(sp, v); }
uint16_t CPU::pop16()   { uint16_t v = bus->read16(sp); sp += 2; return v; }

uint8_t CPU::get_r(int i) {          // 0..7 = B,C,D,E,H,L,(HL),A
    switch (i) { case 0: return b; case 1: return c; case 2: return d; case 3: return e;
                 case 4: return h; case 5: return l; case 6: return bus->read8(hl);
                 default: return a; }
}
void CPU::set_r(int i, uint8_t v) {
    switch (i) { case 0: b=v; break; case 1: c=v; break; case 2: d=v; break; case 3: e=v; break;
                 case 4: h=v; break; case 5: l=v; break; case 6: bus->write8(hl,v); break;
                 default: a=v; }
}

void CPU::alu(int op, uint8_t v) {   // full 8-op implementation in PDF §3.2 — copy it here
    // TODO(M1): ADD/ADC/SUB/SBC/AND/XOR/OR/CP with exact Z N H C behaviour
    (void)op; (void)v;
}

bool CPU::handle_interrupts() {
    if (ei_delay && --ei_delay == 0) ime = true;
    uint8_t pending = bus->ie_reg & bus->if_reg & 0x1F;
    if (pending) halted = false;
    if (!ime || !pending) return false;
    for (int bit = 0; bit < 5; bit++)
        if (pending & (1 << bit)) {
            ime = false;
            bus->if_reg &= ~(1 << bit);
            push16(pc);
            pc = 0x0040 + bit * 8;
            return true;
        }
    return false;
}

int CPU::execute_next() {
    if (handle_interrupts()) return 20;
    if (halted) return 4;
    uint8_t op = fetch8();
    if (op == 0xCB) return exec_cb(fetch8());

    if (op >= 0x40 && op <= 0x7F && op != 0x76) {           // LD r,r'
        set_r((op >> 3) & 7, get_r(op & 7));
        return ((op & 7) == 6 || ((op >> 3) & 7) == 6) ? 8 : 4;
    }
    if (op >= 0x80 && op <= 0xBF) {                         // ALU A,r
        alu((op >> 3) & 7, get_r(op & 7));
        return (op & 7) == 6 ? 8 : 4;
    }
    switch (op) {
        case 0x00: return 4;                                             // NOP
        case 0x01: bc = fetch16(); return 12;   case 0x11: de = fetch16(); return 12;
        case 0x21: hl = fetch16(); return 12;   case 0x31: sp = fetch16(); return 12;
        case 0x3E: a = fetch8(); return 8;                               // LD A,d8
        case 0xC3: pc = fetch16(); return 16;                            // JP a16
        case 0x18: { int8_t r = (int8_t)fetch8(); pc += r; return 12; }  // JR r8
        case 0x20: { int8_t r = (int8_t)fetch8();                        // JR NZ,r8
                     if (!flag(FZ)) { pc += r; return 12; } return 8; }
        case 0x28: { int8_t r = (int8_t)fetch8();                        // JR Z,r8
                     if ( flag(FZ)) { pc += r; return 12; } return 8; }
        case 0xCD: { uint16_t t = fetch16(); push16(pc); pc = t; return 24; }  // CALL
        case 0xC9: pc = pop16(); return 16;                              // RET
        case 0xEA: bus->write8(fetch16(), a); return 16;                 // LD (a16),A
        case 0xFA: a = bus->read8(fetch16()); return 16;                 // LD A,(a16)
        case 0xE0: bus->write8(0xFF00 + fetch8(), a); return 12;         // LDH (a8),A
        case 0xF0: a = bus->read8(0xFF00 + fetch8()); return 12;         // LDH A,(a8)
        case 0xF3: ime = false; return 4;                                // DI
        case 0xFB: ei_delay = 2; return 4;                               // EI
        case 0x76: halted = true; return 4;                              // HALT
        // TODO(M1): remaining opcodes — work through Blargg cpu_instrs test by test,
        // with the cycle matrix (PDF Appendix A) and gbdev.io/gb-opcodes/optables open.
    }
    fprintf(stderr, "unimplemented opcode %02X at %04X\n", op, pc - 1);
    exit(1);                       // fail loudly: this IS the M1 to-do list
}

int CPU::exec_cb(uint8_t op) {
    int r = op & 7, kind = op >> 6, n = (op >> 3) & 7;
    uint8_t v = get_r(r);
    switch (kind) {
        case 1:                                                   // BIT n,r
            set_flag(FZ, !(v & (1 << n))); set_flag(FN, 0); set_flag(FH, 1);
            return r == 6 ? 12 : 8;
        case 2: set_r(r, v & ~(1 << n)); return r == 6 ? 16 : 8;  // RES n,r
        case 3: set_r(r, v |  (1 << n)); return r == 6 ? 16 : 8;  // SET n,r
        default:
            // TODO(M1): kind==0 -> RLC RRC RL RR SLA SRA SWAP SRL, selected by n
            fprintf(stderr, "unimplemented CB %02X\n", op); exit(1);
    }
}
