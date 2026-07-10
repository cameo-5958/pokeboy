#include "bus.h"
#include "cart/cart.h"
#include "ppu/ppu.h"
#include "timer/timer.h"
#include "joypad/joypad.h"
#include "apu/apu.h"
#include "boot/boot_rom.h"
#include <cstdio>

uint8_t Bus::read8(uint16_t a) {
    if (boot_rom_enabled && a < 0x8000) {
        size_t offset = a < CUSTOM_BOOT_FIXED_SIZE
            ? a
            : CUSTOM_BOOT_FIXED_SIZE + (size_t)boot_rom_bank * CUSTOM_BOOT_BANK_SIZE
                + (a - CUSTOM_BOOT_FIXED_SIZE);
        return offset < CUSTOM_BOOT_ROM_SIZE ? CUSTOM_BOOT_ROM[offset] : 0xFF;
    }
    if (a < 0x8000) return cart->read_rom(a);
    if (a < 0xA000) return vram[a - 0x8000];       // TODO(M3): return 0xFF in mode 3
    if (a < 0xC000) return cart->read_ram(a);
    if (a < 0xE000) return wram[a - 0xC000];
    if (a < 0xFE00) return wram[a - 0xE000];       // echo
    if (a < 0xFEA0) return oam[a - 0xFE00];        // TODO(M3): block in modes 2-3
    if (a < 0xFF00) return 0xFF;
    if (a < 0xFF80) return read_io(a);
    if (a < 0xFFFF) return hram[a - 0xFF80];
    return ie_reg;
}

void Bus::write8(uint16_t a, uint8_t v) {
    if (a < 0x8000) { cart->write_mbc(a, v); return; }
    if (a < 0xA000) { vram[a - 0x8000] = v; return; }
    if (a < 0xC000) { cart->write_ram(a, v); return; }
    if (a < 0xE000) { wram[a - 0xC000] = v; return; }
    if (a < 0xFE00) { wram[a - 0xE000] = v; return; }
    if (a < 0xFEA0) { oam[a - 0xFE00] = v; return; }
    if (a < 0xFF00) return;
    if (a < 0xFF80) { write_io(a, v); return; }
    if (a < 0xFFFF) { hram[a - 0xFF80] = v; return; }
    ie_reg = v;
}

uint8_t Bus::read_io(uint16_t a) {
    switch (a) {
        case 0xFF00: return joypad->read();
        case 0xFF04: return timer->div();
        case 0xFF05: return timer->tima;
        case 0xFF06: return timer->tma;
        case 0xFF07: return timer->tac | 0xF8;
        case 0xFF0F: return if_reg | 0xE0;         // top 3 bits read 1
        case 0xFF40: return ppu->lcdc;
        case 0xFF41: return ppu->stat_read();
        case 0xFF42: return ppu->scy;   case 0xFF43: return ppu->scx;
        case 0xFF44: return ppu->ly;    case 0xFF45: return ppu->lyc;
        case 0xFF47: return ppu->bgp;
        case 0xFF48: return ppu->obp0;  case 0xFF49: return ppu->obp1;
        case 0xFF50: return boot_rom_enabled ? 0 : 1;
        case 0xFF51: return boot_rom_bank;
        case 0xFF4A: return ppu->wy;    case 0xFF4B: return ppu->wx;
        default:
            if (a >= 0xFF10 && a <= 0xFF3F) return apu->read_reg(a);
            return io_misc[a - 0xFF00];
    }
}

void Bus::write_io(uint16_t a, uint8_t v) {
    switch (a) {
        case 0xFF00: joypad->select = v & 0x30; return;
        case 0xFF01: sb = v; return;
        case 0xFF02:                                // Blargg serial "printer"
            if (v == 0x81) { fputc(sb, stdout); fflush(stdout); }
            io_misc[2] = v; return;
        case 0xFF04: timer->reset_div(); return;    // any write clears the counter
        case 0xFF05: timer->tima = v; return;
        case 0xFF06: timer->tma  = v; return;
        case 0xFF07: timer->tac  = v & 0x07; return;
        case 0xFF0F: if_reg = v & 0x1F; return;
        case 0xFF40: ppu->lcdc = v; return;
        case 0xFF41: ppu->stat_write(v); return;
        case 0xFF42: ppu->scy = v; return;  case 0xFF43: ppu->scx = v; return;
        case 0xFF45: ppu->lyc = v; return;
        case 0xFF46: {                               // OAM DMA — instant copy is fine
            uint16_t src = v << 8;
            for (int i = 0; i < 0xA0; i++) oam[i] = read8(src + i);
            return;
        }
        case 0xFF47: ppu->bgp  = v; return;
        case 0xFF48: ppu->obp0 = v; return; case 0xFF49: ppu->obp1 = v; return;
        case 0xFF4A: ppu->wy   = v; return; case 0xFF4B: ppu->wx   = v; return;
        case 0xFF50: if (v) boot_rom_enabled = false; return;
        case 0xFF51: if (boot_rom_enabled) boot_rom_bank = v; return;
        default:
            if (a >= 0xFF10 && a <= 0xFF3F) { apu->write_reg(a, v); return; }
            io_misc[a - 0xFF00] = v; return;
    }
}

void Bus::write_io_raw(uint16_t a, uint8_t v) { io_misc[a - 0xFF00] = v; write_io(a, v); }
