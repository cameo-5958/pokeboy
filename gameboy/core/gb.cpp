#include "core.h"
#include <algorithm>
#include <cstring>

bool GameBoy::load_rom(const uint8_t* data, size_t len) {
    cart = Cartridge::create(data, len);           // picks MBC from header byte $0147
    if (!cart) return false;
    bus.attach(cart.get(), &ppu, &timer, &joypad, &apu);
    cpu.bus = &bus;
    cpu.mods = &mods;
    mods.on_rom_loaded(cart.get());
    return true;
}

void GameBoy::reset_custom_boot() {
    cpu = CPU{};
    cpu.bus = &bus;
    cpu.mods = &mods;
    cpu.sp = 0xFFFE;
    cpu.pc = 0x0000;

    ppu = PPU{};
    memset(bus.vram, 0, sizeof(bus.vram));
    memset(bus.wram, 0, sizeof(bus.wram));
    memset(bus.oam, 0, sizeof(bus.oam));
    memset(bus.hram, 0, sizeof(bus.hram));
    bus.if_reg = 0;
    bus.ie_reg = 0;
    bus.boot_rom_bank = 0;
    bus.boot_rom_enabled = true;
    frame_budget = 0;
}

void GameBoy::reset_post_boot() {                  // values: PDF §4.3
    cpu = CPU{}; cpu.bus = &bus; cpu.mods = &mods;
    cpu.af = 0x01B0; cpu.bc = 0x0013; cpu.de = 0x00D8; cpu.hl = 0x014D;
    cpu.sp = 0xFFFE; cpu.pc = 0x0100;
    static const struct { uint16_t a; uint8_t v; } io[] = {
        {0xFF00,0xCF},{0xFF02,0x7E},{0xFF04,0xAB},{0xFF07,0xF8},{0xFF0F,0xE1},
        {0xFF10,0x80},{0xFF11,0xBF},{0xFF12,0xF3},{0xFF13,0xFF},{0xFF14,0xBF},
        {0xFF16,0x3F},{0xFF18,0xFF},{0xFF19,0xBF},{0xFF1A,0x7F},{0xFF1B,0xFF},
        {0xFF1C,0x9F},{0xFF1D,0xFF},{0xFF1E,0xBF},{0xFF20,0xFF},{0xFF23,0xBF},
        {0xFF24,0x77},{0xFF25,0xF3},{0xFF26,0xF1},{0xFF40,0x91},{0xFF41,0x85},
        {0xFF46,0xFF},{0xFF47,0xFC},{0xFF50,0x01},
    };
    for (auto& r : io) bus.write_io_raw(r.a, r.v);
    bus.boot_rom_enabled = false;
    bus.boot_rom_bank = 0;
    frame_budget = 0;
}

void GameBoy::run_frame() {
    frame_budget += 70224;
    while (frame_budget > 0) {
        int t = cpu.execute_next();
        ppu.tick(t, bus);
        timer.tick(t, bus);
        apu.tick(t);                               // no-op until M6
        frame_budget -= t;
    }
}

const uint8_t* GameBoy::save_ram(size_t* len) const {
    if (!cart || cart->ram.empty()) { *len = 0; return nullptr; }
    *len = cart->ram.size(); return cart->ram.data();
}

bool GameBoy::load_save_ram(const uint8_t* data, size_t len) {
    if (!cart || cart->ram.empty() || len > cart->ram.size()) return false;
    std::copy(data, data + len, cart->ram.begin());
    return true;
}
