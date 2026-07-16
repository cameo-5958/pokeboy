#include "core.h"
#include <cstdint>
#include <cstdio>
#include <set>
#include <vector>

// Deliberately NOT <cassert>. CMake builds this as Release, which defines
// NDEBUG and compiles every assert() away -- including anything called inside
// one. This test used to say `assert(gb.load_rom(...))`, so in Release the ROM
// was never loaded at all: bus.attach() never ran and the first write_io
// dereferenced a null joypad, segfaulting before it checked anything. Removing
// the side effect alone would have been worse -- the test would then run the
// boot and exit 0 having verified nothing. CHECK always evaluates its
// condition, in every build type.
static int failures = 0;
#define CHECK(condition) do { \
    if (!(condition)) { \
        std::fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        failures++; \
    } \
} while (0)

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
    if (!gb.load_rom(rom.data(), rom.size())) {
        std::fprintf(stderr, "load_rom failed\n");
        return 1;                              // nothing below is meaningful
    }
    gb.reset_custom_boot();
    CHECK(gb.bus.boot_rom_enabled);
    CHECK(gb.cpu.pc == 0x0000);

    gb.cpu.execute_next();
    CHECK(gb.cpu.pc == 0x0200);                // JP into the hardcoded boot bytecode

    std::set<uint32_t> animation_frames;
    int elapsed_frames = 0;
    while (gb.bus.boot_rom_enabled && elapsed_frames < 320) {
        gb.run_frame();
        elapsed_frames++;
        if (elapsed_frames % 10 == 0)
            animation_frames.insert(framebuffer_hash(gb));
    }

    CHECK(!gb.bus.boot_rom_enabled);
    CHECK(elapsed_frames >= 150 && elapsed_frames <= 175); // logo boot: ~2.7 seconds
    CHECK(animation_frames.size() >= 5);                   // fade + glint actually animate
    CHECK(gb.cpu.pc >= 0x0100);                            // executing cartridge bytes
    CHECK(gb.cpu.af == 0x01B0);
    CHECK(gb.cpu.bc == 0x0013);
    CHECK(gb.cpu.de == 0x00D8);
    CHECK(gb.cpu.hl == 0x014D);
    CHECK(gb.cpu.sp == 0xFFFE);

    std::printf("custom boot completed in %d frames with %zu sampled images (%d failures)\n",
                elapsed_frames, animation_frames.size(), failures);
    return failures == 0 ? 0 : 1;
}
