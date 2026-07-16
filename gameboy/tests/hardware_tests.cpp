#include "core.h"
#include <cstdint>
#include <cstdio>
#include <vector>

namespace {

int64_t fake_time = 0;
int64_t fake_clock() { return fake_time; }

#define CHECK(c) do { if (!(c)) { \
    std::fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #c); \
    return false; } } while (0)

void select_rtc(MBC3& cart, uint8_t reg) {
    cart.write_mbc(0x4000, reg);
}

void write_rtc(MBC3& cart, uint8_t reg, uint8_t value) {
    select_rtc(cart, reg);
    cart.write_ram(0xA000, value);
}

uint8_t read_rtc(MBC3& cart, uint8_t reg) {
    select_rtc(cart, reg);
    return cart.read_ram(0xA000);
}

void latch_rtc(MBC3& cart) {
    cart.write_mbc(0x6000, 0);
    cart.write_mbc(0x6000, 1);
}

bool test_mbc3_rtc_latch_rollover_and_carry() {
    fake_time = 1000;
    MBC3 cart(true, fake_clock);
    cart.write_mbc(0x0000, 0x0A);
    write_rtc(cart, 0x08, 58);
    write_rtc(cart, 0x09, 59);
    write_rtc(cart, 0x0A, 23);
    write_rtc(cart, 0x0B, 0xFF);
    write_rtc(cart, 0x0C, 0x01);

    latch_rtc(cart);
    CHECK(read_rtc(cart, 0x08) == 58);
    CHECK(read_rtc(cart, 0x09) == 59);
    CHECK(read_rtc(cart, 0x0A) == 23);
    CHECK(read_rtc(cart, 0x0B) == 0xFF);
    CHECK(read_rtc(cart, 0x0C) == 0x01);

    fake_time += 3;
    CHECK(read_rtc(cart, 0x08) == 58);             // reads remain on the snapshot
    latch_rtc(cart);
    CHECK(read_rtc(cart, 0x08) == 1);
    CHECK(read_rtc(cart, 0x09) == 0);
    CHECK(read_rtc(cart, 0x0A) == 0);
    CHECK(read_rtc(cart, 0x0B) == 0);
    CHECK(read_rtc(cart, 0x0C) == 0x80);           // 512-day overflow is sticky

    write_rtc(cart, 0x0C, 0);                     // software may clear carry
    latch_rtc(cart);
    CHECK(read_rtc(cart, 0x0C) == 0);
    return true;
}

bool test_mbc3_rtc_halt_and_resume() {
    fake_time = 2000;
    MBC3 cart(true, fake_clock);
    cart.write_mbc(0x0000, 0x0A);
    write_rtc(cart, 0x08, 10);
    write_rtc(cart, 0x0C, 0x40);                  // halt

    fake_time += 100;
    latch_rtc(cart);
    CHECK(read_rtc(cart, 0x08) == 10);
    CHECK(read_rtc(cart, 0x0C) == 0x40);

    write_rtc(cart, 0x0C, 0);                     // resume from current host time
    fake_time += 2;
    latch_rtc(cart);
    CHECK(read_rtc(cart, 0x08) == 12);
    CHECK(read_rtc(cart, 0x0C) == 0);
    return true;
}

bool test_mbc3_rtc_state_preserves_live_and_latched_clocks() {
    fake_time = 3000;
    std::vector<uint8_t> rom(0x8000, 0);
    rom[0x147] = 0x10;                             // MBC3 + timer + RAM + battery
    rom[0x149] = 0x02;                             // 8K RAM

    GameBoy original;
    CHECK(original.load_rom(rom.data(), rom.size()));
    auto* original_cart = dynamic_cast<MBC3*>(original.cart.get());
    CHECK(original_cart != nullptr);
    original_cart->set_clock_source(fake_clock);
    original.bus.write8(0x0000, 0x0A);
    write_rtc(*original_cart, 0x08, 12);
    latch_rtc(*original_cart);
    write_rtc(*original_cart, 0x08, 25);          // live clock differs from snapshot
    select_rtc(*original_cart, 0x08);
    original.bus.write8(0x6000, 0);               // half of the latch sequence

    std::vector<uint8_t> state;
    CHECK(original.save_state(state));

    GameBoy restored;
    CHECK(restored.load_rom(rom.data(), rom.size()));
    auto* restored_cart = dynamic_cast<MBC3*>(restored.cart.get());
    CHECK(restored_cart != nullptr);
    restored_cart->set_clock_source(fake_clock);
    CHECK(restored.load_state(state.data(), state.size()));
    CHECK(read_rtc(*restored_cart, 0x08) == 12);   // old snapshot was serialized

    restored.bus.write8(0x6000, 1);
    CHECK(read_rtc(*restored_cart, 0x08) == 25);   // live clock and latch edge were too
    return true;
}

bool test_mbc3_rtc_battery_save_persists_offline_time() {
    fake_time = 4000;
    std::vector<uint8_t> rom(0x8000, 0);
    rom[0x147] = 0x10;
    rom[0x149] = 0x02;

    GameBoy original;
    CHECK(original.load_rom(rom.data(), rom.size()));
    auto* original_cart = dynamic_cast<MBC3*>(original.cart.get());
    CHECK(original_cart != nullptr);
    original_cart->set_clock_source(fake_clock);
    original.bus.write8(0x0000, 0x0A);
    write_rtc(*original_cart, 0x08, 55);
    fake_time += 10;

    size_t save_len = 0;
    const uint8_t* save_data = original.save_ram(&save_len);
    CHECK(save_data != nullptr);
    CHECK(save_len == original.cart->ram.size() + 25);
    const std::vector<uint8_t> save(save_data, save_data + save_len);

    fake_time += 50;                                // time spent with the app closed
    GameBoy restored;
    CHECK(restored.load_rom(rom.data(), rom.size()));
    auto* restored_cart = dynamic_cast<MBC3*>(restored.cart.get());
    CHECK(restored_cart != nullptr);
    restored_cart->set_clock_source(fake_clock);
    CHECK(restored.load_save_ram(save.data(), save.size()));
    restored.bus.write8(0x0000, 0x0A);
    latch_rtc(*restored_cart);
    CHECK(read_rtc(*restored_cart, 0x08) == 55);
    CHECK(read_rtc(*restored_cart, 0x09) == 1);

    // Saves made before the RTC trailer existed are still valid SRAM images.
    std::vector<uint8_t> legacy(restored.cart->ram.size(), 0xA5);
    CHECK(restored.load_save_ram(legacy.data(), legacy.size()));
    CHECK(restored.cart->ram[0] == 0xA5);
    return true;
}

std::vector<uint8_t> make_rom() {
    std::vector<uint8_t> rom(0x8000, 0);
    rom[0x147] = 0;
    return rom;
}

bool test_cpu_vram_and_oam_access_restrictions() {
    const std::vector<uint8_t> rom = make_rom();
    GameBoy gb;
    CHECK(gb.load_rom(rom.data(), rom.size()));
    gb.ppu.lcdc = 0x80;
    gb.bus.vram[0] = 0x11;
    gb.bus.oam[0] = 0x22;

    gb.ppu.mode = 2;
    CHECK(gb.bus.read8(0x8000) == 0x11);
    gb.bus.write8(0x8000, 0x33);
    CHECK(gb.bus.vram[0] == 0x33);
    CHECK(gb.bus.read8(0xFE00) == 0xFF);
    gb.bus.write8(0xFE00, 0x44);
    CHECK(gb.bus.oam[0] == 0x22);

    gb.ppu.mode = 3;
    CHECK(gb.bus.read8(0x8000) == 0xFF);
    gb.bus.write8(0x8000, 0x55);
    CHECK(gb.bus.vram[0] == 0x33);
    CHECK(gb.bus.read8(0xFE00) == 0xFF);
    gb.bus.write8(0xFE00, 0x66);
    CHECK(gb.bus.oam[0] == 0x22);

    gb.ppu.mode = 0;
    CHECK(gb.bus.read8(0x8000) == 0x33);
    CHECK(gb.bus.read8(0xFE00) == 0x22);
    gb.bus.write8(0x8000, 0x77);
    gb.bus.write8(0xFE00, 0x88);
    CHECK(gb.bus.vram[0] == 0x77);
    CHECK(gb.bus.oam[0] == 0x88);

    gb.ppu.mode = 3;
    gb.ppu.lcdc = 0;                               // disabling LCD lifts both gates
    gb.bus.write8(0x8000, 0x99);
    gb.bus.write8(0xFE00, 0xAA);
    CHECK(gb.bus.read8(0x8000) == 0x99);
    CHECK(gb.bus.read8(0xFE00) == 0xAA);
    return true;
}

bool test_ppu_and_dma_keep_internal_memory_access() {
    const std::vector<uint8_t> rom = make_rom();
    GameBoy gb;
    CHECK(gb.load_rom(rom.data(), rom.size()));
    gb.ppu.lcdc = 0x91;
    gb.ppu.bgp = 0xE4;                             // identity palette
    gb.ppu.ly = 0;
    gb.ppu.dot = 455;
    gb.ppu.mode = 3;
    gb.bus.vram[0] = 0x80;                        // color 1 at the first pixel
    gb.bus.vram[1] = 0;
    gb.bus.vram[0x1800] = 0;
    CHECK(gb.bus.read8(0x8000) == 0xFF);           // CPU is blocked
    gb.ppu.tick(1, gb.bus);
    CHECK(gb.ppu.fb[0] == 1);                      // renderer is not

    gb.ppu.mode = 3;
    for (int i = 0; i < 0xA0; i++) gb.bus.vram[i] = static_cast<uint8_t>(i ^ 0x5A);
    gb.bus.write8(0xFF46, 0x80);
    for (int i = 0; i < 0xA0; i++)
        CHECK(gb.bus.oam[i] == static_cast<uint8_t>(i ^ 0x5A));
    CHECK(gb.bus.read8(0xFE00) == 0xFF);           // DMA wrote OAM despite CPU gate
    return true;
}

bool test_stop_waits_for_new_joypad_press_and_round_trips() {
    std::vector<uint8_t> rom = make_rom();
    rom[0x100] = 0x10;                              // STOP
    rom[0x101] = 0x00;                              // required padding byte
    rom[0x102] = 0x3E;                              // LD A,$42
    rom[0x103] = 0x42;

    GameBoy gb;
    CHECK(gb.load_rom(rom.data(), rom.size()));
    gb.reset_post_boot();
    gb.bus.ie_reg = 0;                              // wake without servicing IRQ
    CHECK(gb.cpu.execute_next() == 4);
    CHECK(gb.cpu.stopped);
    CHECK(gb.cpu.pc == 0x102);                      // STOP consumed its padding

    gb.cpu.ime = true;
    gb.bus.ie_reg = 0x01;
    gb.bus.if_reg = 0x01;                           // VBlank cannot wake STOP
    const uint16_t stopped_sp = gb.cpu.sp;
    CHECK(gb.cpu.execute_next() == 4);
    CHECK(gb.cpu.stopped);
    CHECK(gb.cpu.pc == 0x102);
    CHECK(gb.cpu.sp == stopped_sp);                 // no interrupt frame was pushed
    gb.cpu.ime = false;
    gb.bus.ie_reg = 0;
    gb.bus.if_reg = 0;

    std::vector<uint8_t> state;
    CHECK(gb.save_state(state));
    CHECK(gb.cpu.execute_next() == 4);
    CHECK(gb.cpu.pc == 0x102);                      // no instruction fetch while stopped

    gb.set_input(0x01, 0);                          // A button
    CHECK(!gb.cpu.stopped);
    CHECK((gb.bus.if_reg & 0x10) != 0);
    CHECK(gb.cpu.execute_next() == 8);
    CHECK(gb.cpu.a == 0x42);
    CHECK(gb.cpu.pc == 0x104);

    CHECK(gb.load_state(state.data(), state.size()));
    CHECK(gb.cpu.stopped);
    CHECK(gb.cpu.pc == 0x102);
    CHECK(gb.cpu.execute_next() == 4);
    CHECK(gb.cpu.pc == 0x102);
    return true;
}

struct Test { const char* name; bool (*fn)(); };

} // namespace

int main() {
    const Test tests[] = {
        { "MBC3 RTC latch, rollover and carry", test_mbc3_rtc_latch_rollover_and_carry },
        { "MBC3 RTC halt and resume", test_mbc3_rtc_halt_and_resume },
        { "MBC3 RTC state", test_mbc3_rtc_state_preserves_live_and_latched_clocks },
        { "MBC3 RTC battery persistence", test_mbc3_rtc_battery_save_persists_offline_time },
        { "CPU PPU memory gates", test_cpu_vram_and_oam_access_restrictions },
        { "PPU and DMA internal access", test_ppu_and_dma_keep_internal_memory_access },
        { "STOP and joypad wake", test_stop_waits_for_new_joypad_press_and_round_trips },
    };
    int failed = 0;
    for (const Test& test : tests) {
        const bool ok = test.fn();
        std::printf("%-40s %s\n", test.name, ok ? "ok" : "FAILED");
        if (!ok) failed++;
    }
    std::printf("\n%d/%zu passed\n", static_cast<int>(sizeof(tests) / sizeof(*tests)) - failed,
                sizeof(tests) / sizeof(*tests));
    return failed == 0 ? 0 : 1;
}
