// tests/state_tests.cpp — save/load state round-trips and rejection paths.
//
// The property that matters: a state loaded back must make the machine run
// IDENTICALLY from that point. Comparing only the bytes we wrote would pass
// even if a field were missing from the stream, so every check here resumes
// the machine and compares what it actually computes.
#include "core.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace {

// ROM-only cartridge that keeps CPU, timer, WRAM and the PPU all moving, so a
// field dropped from the stream shows up as a divergence rather than sitting in
// a quiet corner of an idle machine.
std::vector<uint8_t> make_rom() {
    std::vector<uint8_t> rom(0x8000, 0x00);
    rom[0x147] = 0x00;                                  // ROM only, no MBC
    const uint8_t entry[] = {
        0x3E, 0x05,             // LD A,$05
        0xE0, 0x07,             // LDH ($07),A   -- TAC: timer on
        0xC3, 0x50, 0x01,       // JP $0150
    };
    memcpy(&rom[0x100], entry, sizeof(entry));
    const uint8_t loop[] = {
        0x21, 0x00, 0xC0,       // LD HL,$C000
        0x34,                   // INC (HL)
        0x23,                   // INC HL
        0x34,                   // INC (HL)
        0x18, 0xF8,             // JR -8  -> $0150
    };
    memcpy(&rom[0x150], loop, sizeof(loop));
    return rom;
}

// Covers INTERNAL divider/dot counters, not just the headline registers. An
// earlier version of this hash sampled only tima/ly and happily passed with
// timer.counter dropped from the stream: TIMA reconverges across a frame
// boundary, so the divergence washed out before anything observable saw it.
// Anything a subsystem needs to resume must be hashed here or the round-trip
// tests below cannot see it go missing.
uint32_t machine_hash(GameBoy& gb) {
    uint32_t h = 2166136261u;
    auto mix = [&](const void* p, size_t n) {
        const uint8_t* b = static_cast<const uint8_t*>(p);
        for (size_t i = 0; i < n; i++) { h ^= b[i]; h *= 16777619u; }
    };
    mix(gb.framebuffer(), 160 * 144);
    mix(gb.bus.wram, sizeof(gb.bus.wram));
    mix(gb.bus.vram, sizeof(gb.bus.vram));
    mix(gb.bus.hram, sizeof(gb.bus.hram));
    mix(gb.bus.oam, sizeof(gb.bus.oam));
    const uint16_t regs[] = { gb.cpu.af, gb.cpu.bc, gb.cpu.de,
                              gb.cpu.hl, gb.cpu.sp, gb.cpu.pc };
    mix(regs, sizeof(regs));
    const uint8_t misc[] = { gb.bus.if_reg, gb.bus.ie_reg,
                             (uint8_t)gb.cpu.ime, (uint8_t)gb.cpu.halted,
                             (uint8_t)gb.cpu.stopped,
                             gb.ppu.ly, gb.ppu.lyc, gb.ppu.lcdc, gb.ppu.stat,
                             gb.timer.tima, gb.timer.tma, gb.timer.tac,
                             (uint8_t)gb.timer.prev_signal };
    mix(misc, sizeof(misc));
    const int internals[] = { gb.ppu.dot, gb.ppu.mode, gb.ppu.win_line,
                              gb.cpu.ei_delay };
    mix(internals, sizeof(internals));
    mix(&gb.timer.counter, sizeof(gb.timer.counter));   // the divider itself
    return h;
}

// The APU keeps its channel/frame-sequencer state entirely private, so the only
// way to prove it round-tripped is to listen to what it produces. Drains
// everything queued; callers must equalise both machines before comparing.
uint32_t drain_audio_hash(GameBoy& gb) {
    uint32_t h = 2166136261u;
    float buf[4096];
    int n;
    while ((n = gb.read_audio(buf, 2048)) > 0) {
        const uint8_t* b = reinterpret_cast<const uint8_t*>(buf);
        for (size_t i = 0; i < size_t(n) * 2 * sizeof(float); i++) {
            h ^= b[i]; h *= 16777619u;
        }
    }
    return h;
}

void run(GameBoy& gb, int frames) { for (int i = 0; i < frames; i++) gb.run_frame(); }

#define CHECK(c) do { if (!(c)) { \
    std::fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #c); \
    return false; } } while (0)

// Save, run on, restore, run the same span again: the machine must land on the
// exact same state both times.
bool test_round_trip_resumes_identically() {
    const std::vector<uint8_t> rom = make_rom();
    GameBoy gb;
    CHECK(gb.load_rom(rom.data(), rom.size()));
    gb.reset_post_boot();
    run(gb, 40);

    std::vector<uint8_t> state;
    CHECK(gb.save_state(state));
    CHECK(!state.empty());
    const uint32_t at_save = machine_hash(gb);

    run(gb, 25);
    const uint32_t expected = machine_hash(gb);
    CHECK(expected != at_save);                 // the ROM must actually be doing work

    CHECK(gb.load_state(state.data(), state.size()));
    CHECK(machine_hash(gb) == at_save);         // restored to the save point

    run(gb, 25);
    CHECK(machine_hash(gb) == expected);        // and resumes identically
    return true;
}

// A state must restore onto a different handle, which is what a frontend
// loading a state from disk into a fresh emulator actually does.
bool test_restores_onto_a_fresh_machine() {
    const std::vector<uint8_t> rom = make_rom();
    GameBoy a;
    CHECK(a.load_rom(rom.data(), rom.size()));
    a.reset_post_boot();
    run(a, 33);

    std::vector<uint8_t> state;
    CHECK(a.save_state(state));

    GameBoy b;
    CHECK(b.load_rom(rom.data(), rom.size()));
    b.reset_post_boot();
    run(b, 7);                                   // deliberately out of sync
    CHECK(machine_hash(b) != machine_hash(a));

    CHECK(b.load_state(state.data(), state.size()));
    CHECK(machine_hash(b) == machine_hash(a));

    // Empty both queues so the comparison below covers only audio generated
    // after the restore (loading already flushed b's).
    drain_audio_hash(a); drain_audio_hash(b);

    run(a, 20); run(b, 20);
    CHECK(machine_hash(b) == machine_hash(a));   // still in lockstep afterwards
    CHECK(drain_audio_hash(b) == drain_audio_hash(a));  // APU resumed too
    return true;
}

// A state from different ROM bytes loads into a machine that looks fine and
// then desyncs later, so it must be refused up front.
bool test_rejects_state_from_another_rom() {
    std::vector<uint8_t> rom_a = make_rom();
    std::vector<uint8_t> rom_b = make_rom();
    rom_b[0x2000] = 0x42;                        // same size, different bytes

    GameBoy a;
    CHECK(a.load_rom(rom_a.data(), rom_a.size()));
    a.reset_post_boot();
    run(a, 10);
    std::vector<uint8_t> state;
    CHECK(a.save_state(state));

    GameBoy b;
    CHECK(b.load_rom(rom_b.data(), rom_b.size()));
    b.reset_post_boot();
    run(b, 10);
    const uint32_t before = machine_hash(b);

    CHECK(!b.load_state(state.data(), state.size()));
    CHECK(machine_hash(b) == before);            // and left untouched
    return true;
}

// A truncated state must fail without half-overwriting the live machine.
bool test_rejects_truncated_state_without_damage() {
    const std::vector<uint8_t> rom = make_rom();
    GameBoy gb;
    CHECK(gb.load_rom(rom.data(), rom.size()));
    gb.reset_post_boot();
    run(gb, 15);

    std::vector<uint8_t> state;
    CHECK(gb.save_state(state));

    run(gb, 5);
    const uint32_t before = machine_hash(gb);

    for (size_t len : { size_t(0), size_t(3), size_t(16), state.size() / 2,
                        state.size() - 1 }) {
        CHECK(!gb.load_state(state.data(), len));
        CHECK(machine_hash(gb) == before);       // machine survives every attempt
    }

    // Still healthy: a valid load after the failures must work.
    CHECK(gb.load_state(state.data(), state.size()));
    return true;
}

bool test_rejects_corrupt_header() {
    const std::vector<uint8_t> rom = make_rom();
    GameBoy gb;
    CHECK(gb.load_rom(rom.data(), rom.size()));
    gb.reset_post_boot();
    run(gb, 12);

    std::vector<uint8_t> state;
    CHECK(gb.save_state(state));
    const uint32_t before = machine_hash(gb);

    std::vector<uint8_t> bad_magic = state;
    bad_magic[0] = 'X';
    CHECK(!gb.load_state(bad_magic.data(), bad_magic.size()));
    CHECK(machine_hash(gb) == before);

    std::vector<uint8_t> bad_version = state;
    bad_version[4] = 0xFF;                       // version is the u32 after magic
    CHECK(!gb.load_state(bad_version.data(), bad_version.size()));
    CHECK(machine_hash(gb) == before);
    return true;
}

// Cartridge RAM and the MBC bank registers are machine state: a state taken
// with a non-default bank mapped must come back with that same bank mapped.
bool test_preserves_cart_ram_and_mbc_banks() {
    std::vector<uint8_t> rom(0x80000, 0x00);     // 512K, MBC1 + RAM + battery
    rom[0x147] = 0x03;
    rom[0x148] = 0x04;                           // 512K ROM (32 banks)
    rom[0x149] = 0x03;                           // 32K RAM (4 banks)
    memcpy(&rom[0x100], "\x00\xC3\x00\x01", 4);  // NOP; JP $0100

    GameBoy gb;
    CHECK(gb.load_rom(rom.data(), rom.size()));
    gb.reset_post_boot();

    gb.bus.write8(0x0000, 0x0A);                 // RAM enable
    gb.bus.write8(0x6000, 0x01);                 // MBC1 mode 1 (RAM banking)
    gb.bus.write8(0x4000, 0x02);                 // RAM bank 2
    gb.bus.write8(0xA000, 0xAB);                 // marker in bank 2
    gb.bus.write8(0x2000, 0x05);                 // ROM bank 5

    std::vector<uint8_t> state;
    CHECK(gb.save_state(state));

    GameBoy fresh;
    CHECK(fresh.load_rom(rom.data(), rom.size()));
    fresh.reset_post_boot();
    CHECK(fresh.load_state(state.data(), state.size()));

    // Reads go through the MBC, so this only holds if the bank registers came
    // back too -- cart RAM contents alone would read from bank 0 and miss.
    CHECK(fresh.bus.read8(0xA000) == 0xAB);
    return true;
}

struct Test { const char* name; bool (*fn)(); };

} // namespace

int main() {
    const Test tests[] = {
        { "round trip resumes identically",   test_round_trip_resumes_identically },
        { "restores onto a fresh machine",    test_restores_onto_a_fresh_machine },
        { "rejects state from another rom",   test_rejects_state_from_another_rom },
        { "rejects truncated state",          test_rejects_truncated_state_without_damage },
        { "rejects corrupt header",           test_rejects_corrupt_header },
        { "preserves cart ram and mbc banks", test_preserves_cart_ram_and_mbc_banks },
    };
    int failed = 0;
    for (const Test& t : tests) {
        const bool ok = t.fn();
        std::printf("%-36s %s\n", t.name, ok ? "ok" : "FAILED");
        if (!ok) failed++;
    }
    std::printf("\n%d/%zu passed\n", (int)(sizeof(tests) / sizeof(*tests)) - failed,
                sizeof(tests) / sizeof(*tests));
    return failed == 0 ? 0 : 1;
}
