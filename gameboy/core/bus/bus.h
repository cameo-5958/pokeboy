#pragma once
#include <cstdint>
#include "state/state.h"
struct Cartridge; struct PPU; struct Timer; struct Joypad; struct APU;

class Bus {
public:
    void attach(Cartridge* c, PPU* p, Timer* t, Joypad* j, APU* a)
        { cart=c; ppu=p; timer=t; joypad=j; apu=a; }

    uint8_t read8(uint16_t a);
    void    write8(uint16_t a, uint8_t v);
    uint16_t read16(uint16_t a) { return read8(a) | (read8(a + 1) << 8); }
    void     write16(uint16_t a, uint16_t v) { write8(a, v & 0xFF); write8(a + 1, (uint8_t)(v >> 8)); }

    void write_io_raw(uint16_t a, uint8_t v);      // reset path: no side effects

    // io_misc/sb are private backing store but are live machine state, so the
    // method lives here where it can reach them.
    void serialize(StateIO& s) {
        s.arr(vram); s.arr(wram); s.arr(oam); s.arr(hram);
        s.v(if_reg); s.v(ie_reg);
        s.v(boot_rom_enabled); s.v(boot_rom_bank);
        s.arr(io_misc); s.v(sb);
    }

    uint8_t vram[0x2000]{}, wram[0x2000]{}, oam[0xA0]{}, hram[0x7F]{};
    uint8_t if_reg = 0, ie_reg = 0;
    bool    boot_rom_enabled = false;
    uint8_t boot_rom_bank = 0;                     // custom boot data window at $4000-$7FFF

    Cartridge* cart{}; PPU* ppu{}; Timer* timer{}; Joypad* joypad{}; APU* apu{};
private:
    uint8_t read8_unrestricted(uint16_t a);
    uint8_t read_io(uint16_t a);
    void    write_io(uint16_t a, uint8_t v);
    uint8_t io_misc[0x80]{};                       // backing store for unhandled regs
    uint8_t sb = 0;                                // serial data (Blargg output)
};
