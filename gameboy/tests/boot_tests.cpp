#include "core.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <set>
#include <vector>

static uint32_t framebuffer_hash(const GameBoy& gb) {
    uint32_t hash = 2166136261u;
    for (int i = 0; i < 160 * 144; i++) {
        hash ^= gb.framebuffer()[i];
        hash *= 16777619u;
    }
    return hash;
}

int main() {
    std::vector<uint8_t> rom(0x8000, 0x00); // NOP cartridge after the boot handoff
    rom[0x147] = 0x00;

    GameBoy gb;
    assert(gb.load_rom(rom.data(), rom.size()));
    gb.reset_custom_boot();
    assert(gb.bus.boot_rom_enabled);
    assert(gb.cpu.pc == 0x0000);

    gb.cpu.execute_next();
    assert(gb.cpu.pc == 0x0200);               // JP into the hardcoded boot bytecode

    std::set<uint32_t> animation_frames;
    int elapsed_frames = 0;
    while (gb.bus.boot_rom_enabled && elapsed_frames < 320) {
        gb.run_frame();
        elapsed_frames++;
        if (elapsed_frames % 10 == 0)
            animation_frames.insert(framebuffer_hash(gb));
    }

    assert(!gb.bus.boot_rom_enabled);
    assert(elapsed_frames >= 295 && elapsed_frames <= 305); // approximately five seconds
    assert(animation_frames.size() >= 10);                  // actual changing PPU output
    assert(gb.cpu.pc >= 0x0100);                            // executing cartridge bytes
    assert(gb.cpu.af == 0x01B0);
    assert(gb.cpu.bc == 0x0013);
    assert(gb.cpu.de == 0x00D8);
    assert(gb.cpu.hl == 0x014D);
    assert(gb.cpu.sp == 0xFFFE);

    std::printf("custom boot completed in %d frames with %zu sampled images\n",
                elapsed_frames, animation_frames.size());
    return 0;
}
