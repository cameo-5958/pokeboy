#include "cpu.h"
#include "bus/bus.h"

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

void CPU::alu(int op, uint8_t v) {   // 0..7 = ADD ADC SUB SBC AND XOR OR CP
    switch (op) {
        case 0: {                                                        // ADD
            uint16_t r = a + v;
            set_flag(FH, (a & 0xF) + (v & 0xF) > 0xF);
            set_flag(FC, r > 0xFF);
            a = (uint8_t)r;
            set_flag(FZ, a == 0); set_flag(FN, 0);
            break;
        }
        case 1: {                                                        // ADC
            int cy = flag(FC) ? 1 : 0;
            uint16_t r = a + v + cy;
            set_flag(FH, (a & 0xF) + (v & 0xF) + cy > 0xF);
            set_flag(FC, r > 0xFF);
            a = (uint8_t)r;
            set_flag(FZ, a == 0); set_flag(FN, 0);
            break;
        }
        case 2: {                                                        // SUB
            set_flag(FH, (a & 0xF) < (v & 0xF));
            set_flag(FC, a < v);
            a -= v;
            set_flag(FZ, a == 0); set_flag(FN, 1);
            break;
        }
        case 3: {                                                        // SBC
            int cy = flag(FC) ? 1 : 0;
            int r = a - v - cy;
            set_flag(FH, (a & 0xF) - (v & 0xF) - cy < 0);
            set_flag(FC, r < 0);
            a = (uint8_t)r;
            set_flag(FZ, a == 0); set_flag(FN, 1);
            break;
        }
        case 4: a &= v; set_flag(FZ,a==0); set_flag(FN,0); set_flag(FH,1); set_flag(FC,0); break;
        case 5: a ^= v; set_flag(FZ,a==0); set_flag(FN,0); set_flag(FH,0); set_flag(FC,0); break;
        case 6: a |= v; set_flag(FZ,a==0); set_flag(FN,0); set_flag(FH,0); set_flag(FC,0); break;
        case 7:                                                          // CP
            set_flag(FZ, a == v); set_flag(FN, 1);
            set_flag(FH, (a & 0xF) < (v & 0xF)); set_flag(FC, a < v);
            break;
    }
}

uint8_t CPU::rot(int kind, uint8_t v) {  // 0..7 = RLC RRC RL RR SLA SRA SWAP SRL
    bool oc = flag(FC), nc = false;
    uint8_t r = 0;
    switch (kind) {
        case 0: nc = v & 0x80; r = (uint8_t)((v << 1) | (nc ? 1 : 0));    break;
        case 1: nc = v & 0x01; r = (uint8_t)((v >> 1) | (nc ? 0x80 : 0)); break;
        case 2: nc = v & 0x80; r = (uint8_t)((v << 1) | (oc ? 1 : 0));    break;
        case 3: nc = v & 0x01; r = (uint8_t)((v >> 1) | (oc ? 0x80 : 0)); break;
        case 4: nc = v & 0x80; r = (uint8_t)(v << 1);                     break;
        case 5: nc = v & 0x01; r = (uint8_t)((v >> 1) | (v & 0x80));      break;
        case 6: nc = false;    r = (uint8_t)((v << 4) | (v >> 4));        break;
        case 7: nc = v & 0x01; r = (uint8_t)(v >> 1);                     break;
    }
    set_flag(FZ, r == 0); set_flag(FN, 0); set_flag(FH, 0); set_flag(FC, nc);
    return r;
}

uint8_t CPU::inc8(uint8_t v) {
    v++;
    set_flag(FZ, v == 0); set_flag(FN, 0); set_flag(FH, (v & 0xF) == 0);
    return v;
}
uint8_t CPU::dec8(uint8_t v) {
    set_flag(FH, (v & 0xF) == 0);
    v--;
    set_flag(FZ, v == 0); set_flag(FN, 1);
    return v;
}
void CPU::add_hl(uint16_t v) {
    uint32_t r = hl + v;
    set_flag(FN, 0);
    set_flag(FH, (hl & 0xFFF) + (v & 0xFFF) > 0xFFF);
    set_flag(FC, r > 0xFFFF);
    hl = (uint16_t)r;
}
uint16_t CPU::sp_plus_r8() {         // shared by ADD SP,r8 / LD HL,SP+r8
    int8_t r8 = (int8_t)fetch8();
    set_flag(FZ, 0); set_flag(FN, 0);
    set_flag(FH, (sp & 0x0F) + (r8 & 0x0F) > 0x0F);
    set_flag(FC, (sp & 0xFF) + (uint8_t)r8 > 0xFF);
    return (uint16_t)(sp + r8);
}
void CPU::daa() {
    int adj = 0;
    bool cy = flag(FC);
    if (!flag(FN)) {
        if (flag(FH) || (a & 0xF) > 9) adj |= 0x06;
        if (cy || a > 0x99) { adj |= 0x60; cy = true; }
        a += (uint8_t)adj;
    } else {
        if (flag(FH)) adj |= 0x06;
        if (cy)       adj |= 0x60;
        a -= (uint8_t)adj;
    }
    set_flag(FZ, a == 0); set_flag(FH, 0); set_flag(FC, cy);
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
    if ((op & 0xC7) == 0x04) {                              // INC r
        int r = (op >> 3) & 7;
        set_r(r, inc8(get_r(r)));
        return r == 6 ? 12 : 4;
    }
    if ((op & 0xC7) == 0x05) {                              // DEC r
        int r = (op >> 3) & 7;
        set_r(r, dec8(get_r(r)));
        return r == 6 ? 12 : 4;
    }
    if ((op & 0xC7) == 0x06) {                              // LD r,d8
        int r = (op >> 3) & 7;
        set_r(r, fetch8());
        return r == 6 ? 12 : 8;
    }
    if ((op & 0xC7) == 0xC6) {                              // ALU A,d8
        alu((op >> 3) & 7, fetch8());
        return 8;
    }
    if ((op & 0xC7) == 0xC7) {                              // RST
        push16(pc);
        pc = op & 0x38;
        return 16;
    }

    switch (op) {
        case 0x00: return 4;                                             // NOP
        case 0x10: fetch8(); return 4;                                   // STOP (as NOP)
        case 0x01: bc = fetch16(); return 12;   case 0x11: de = fetch16(); return 12;
        case 0x21: hl = fetch16(); return 12;   case 0x31: sp = fetch16(); return 12;
        case 0x02: bus->write8(bc, a); return 8;                         // LD (BC),A
        case 0x12: bus->write8(de, a); return 8;                         // LD (DE),A
        case 0x22: bus->write8(hl++, a); return 8;                       // LD (HL+),A
        case 0x32: bus->write8(hl--, a); return 8;                       // LD (HL-),A
        case 0x0A: a = bus->read8(bc); return 8;                         // LD A,(BC)
        case 0x1A: a = bus->read8(de); return 8;                         // LD A,(DE)
        case 0x2A: a = bus->read8(hl++); return 8;                       // LD A,(HL+)
        case 0x3A: a = bus->read8(hl--); return 8;                       // LD A,(HL-)
        case 0x03: bc++; return 8;  case 0x13: de++; return 8;           // INC rr
        case 0x23: hl++; return 8;  case 0x33: sp++; return 8;
        case 0x0B: bc--; return 8;  case 0x1B: de--; return 8;           // DEC rr
        case 0x2B: hl--; return 8;  case 0x3B: sp--; return 8;
        case 0x09: add_hl(bc); return 8;  case 0x19: add_hl(de); return 8;
        case 0x29: add_hl(hl); return 8;  case 0x39: add_hl(sp); return 8;
        case 0x08: { uint16_t t = fetch16(); bus->write16(t, sp); return 20; }  // LD (a16),SP
        case 0x07: a = rot(0, a); set_flag(FZ, 0); return 4;             // RLCA
        case 0x0F: a = rot(1, a); set_flag(FZ, 0); return 4;             // RRCA
        case 0x17: a = rot(2, a); set_flag(FZ, 0); return 4;             // RLA
        case 0x1F: a = rot(3, a); set_flag(FZ, 0); return 4;             // RRA
        case 0x27: daa(); return 4;                                      // DAA
        case 0x2F: a = ~a; set_flag(FN, 1); set_flag(FH, 1); return 4;   // CPL
        case 0x37: set_flag(FN, 0); set_flag(FH, 0); set_flag(FC, 1); return 4;        // SCF
        case 0x3F: set_flag(FN, 0); set_flag(FH, 0); set_flag(FC, !flag(FC)); return 4;// CCF
        case 0x18: { int8_t r = (int8_t)fetch8(); pc += r; return 12; }  // JR r8
        case 0x20: { int8_t r = (int8_t)fetch8();
                     if (!flag(FZ)) { pc += r; return 12; } return 8; }
        case 0x28: { int8_t r = (int8_t)fetch8();
                     if ( flag(FZ)) { pc += r; return 12; } return 8; }
        case 0x30: { int8_t r = (int8_t)fetch8();
                     if (!flag(FC)) { pc += r; return 12; } return 8; }
        case 0x38: { int8_t r = (int8_t)fetch8();
                     if ( flag(FC)) { pc += r; return 12; } return 8; }
        case 0xC3: pc = fetch16(); return 16;                            // JP a16
        case 0xC2: { uint16_t t = fetch16(); if (!flag(FZ)) { pc = t; return 16; } return 12; }
        case 0xCA: { uint16_t t = fetch16(); if ( flag(FZ)) { pc = t; return 16; } return 12; }
        case 0xD2: { uint16_t t = fetch16(); if (!flag(FC)) { pc = t; return 16; } return 12; }
        case 0xDA: { uint16_t t = fetch16(); if ( flag(FC)) { pc = t; return 16; } return 12; }
        case 0xE9: pc = hl; return 4;                                    // JP HL
        case 0xCD: { uint16_t t = fetch16(); push16(pc); pc = t; return 24; }  // CALL
        case 0xC4: { uint16_t t = fetch16(); if (!flag(FZ)) { push16(pc); pc = t; return 24; } return 12; }
        case 0xCC: { uint16_t t = fetch16(); if ( flag(FZ)) { push16(pc); pc = t; return 24; } return 12; }
        case 0xD4: { uint16_t t = fetch16(); if (!flag(FC)) { push16(pc); pc = t; return 24; } return 12; }
        case 0xDC: { uint16_t t = fetch16(); if ( flag(FC)) { push16(pc); pc = t; return 24; } return 12; }
        case 0xC9: pc = pop16(); return 16;                              // RET
        case 0xD9: pc = pop16(); ime = true; return 16;                  // RETI
        case 0xC0: if (!flag(FZ)) { pc = pop16(); return 20; } return 8;
        case 0xC8: if ( flag(FZ)) { pc = pop16(); return 20; } return 8;
        case 0xD0: if (!flag(FC)) { pc = pop16(); return 20; } return 8;
        case 0xD8: if ( flag(FC)) { pc = pop16(); return 20; } return 8;
        case 0xC5: push16(bc); return 16;  case 0xD5: push16(de); return 16;
        case 0xE5: push16(hl); return 16;  case 0xF5: push16(af); return 16;
        case 0xC1: bc = pop16(); return 12;  case 0xD1: de = pop16(); return 12;
        case 0xE1: hl = pop16(); return 12;
        case 0xF1: af = pop16() & 0xFFF0; return 12;                     // POP AF: low nibble of F reads 0
        case 0xEA: bus->write8(fetch16(), a); return 16;                 // LD (a16),A
        case 0xFA: a = bus->read8(fetch16()); return 16;                 // LD A,(a16)
        case 0xE0: bus->write8(0xFF00 + fetch8(), a); return 12;         // LDH (a8),A
        case 0xF0: a = bus->read8(0xFF00 + fetch8()); return 12;         // LDH A,(a8)
        case 0xE2: bus->write8(0xFF00 + c, a); return 8;                 // LD (C),A
        case 0xF2: a = bus->read8(0xFF00 + c); return 8;                 // LD A,(C)
        case 0xE8: sp = sp_plus_r8(); return 16;                         // ADD SP,r8
        case 0xF8: hl = sp_plus_r8(); return 12;                         // LD HL,SP+r8
        case 0xF9: sp = hl; return 8;                                    // LD SP,HL
        case 0xF3: ime = false; ei_delay = 0; return 4;                  // DI
        case 0xFB: if (!ime && !ei_delay) ei_delay = 2; return 4;        // EI (delayed one instr)
        case 0x76: halted = true; return 4;                              // HALT
        default:   return 4;      // D3 DB DD E3 E4 EB EC ED F4 FC FD: illegal, treat as NOP
    }
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
        default:                                                  // rotate/shift group
            set_r(r, rot(n, v));
            return r == 6 ? 16 : 8;
    }
}
