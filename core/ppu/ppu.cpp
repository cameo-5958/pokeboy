// ppu.cpp — the state machine; the renderer body comes from PDF §6.6
#include "ppu.h"
#include "bus/bus.h"

void PPU::set_mode(int m, Bus& bus) {
    mode = m;
    static const uint8_t int_bit[4] = {0x08, 0x10, 0x20, 0x00};  // STAT enables m0,m1,m2
    if (int_bit[m] & stat) bus.if_reg |= 0x02;
}

void PPU::tick(int tcycles, Bus& bus) {
    if (!(lcdc & 0x80)) { ly = 0; dot = 0; mode = 0; return; }   // LCD off
    dot += tcycles;
    while (dot >= 456) {
        dot -= 456;
        if (ly < 144 && mode != 1) render_scanline(bus);         // draw at end of line
        ly++;
        if (ly == 144) { set_mode(1, bus); bus.if_reg |= 0x01; } // VBlank interrupt
        if (ly > 153)  { ly = 0; win_line = 0; }
        if (ly < 144)  set_mode(2, bus);
        if (ly == lyc && (stat & 0x40)) bus.if_reg |= 0x02;      // LYC coincidence
    }
    if (ly < 144) {                                              // mode progression in-line
        if      (dot < 80  && mode != 2) set_mode(2, bus);
        else if (dot >= 80 && dot < 252 && mode != 3) mode = 3;  // mode 3: no STAT int
        else if (dot >= 252 && mode != 0) set_mode(0, bus);
    }
}

void PPU::render_scanline(Bus& bus) {
    // TODO(M2): background path   TODO(M3): window + sprites
    // Complete implementation: PDF §6.6 (uses bus.vram / bus.oam)
    (void)bus;
}
