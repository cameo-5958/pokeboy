#pragma once
#include <cstdint>
#include <cstddef>
#include <memory>
#include <vector>

#include "bus/bus.h"
#include "cpu/cpu.h"
#include "ppu/ppu.h"
#include "timer/timer.h"
#include "joypad/joypad.h"
#include "apu/apu.h"
#include "cart/cart.h"
#include "pkai/hook.h"

class GameBoy {
public:
    bool  load_rom(const uint8_t* data, size_t len);
    void  reset_custom_boot();                     // executes the bundled boot bytecode at $0000
    void  reset_post_boot();                       // PDF §4.3 - byte-exact
    void  run_frame();                             // exactly 70224 T-cycles
    const uint8_t* framebuffer() const { return ppu.fb; }        // 160*144, 0-3
    void  set_input(uint8_t buttons, uint8_t dpad) {
        uint8_t pressed = (uint8_t)((buttons & ~joypad.buttons) | (dpad & ~joypad.dpad));
        joypad.buttons = buttons; joypad.dpad = dpad;
        if (pressed) {
            bus.if_reg |= 0x10;                    // joypad interrupt on new press
            cpu.stopped = false;                   // STOP wakes even when IME/IE block service
        }
    }
    int   read_audio(float* stereo, int max_frames) { return apu.drain(stereo, max_frames); }
    const uint8_t* save_ram(size_t* len) const;    // SRAM plus an MBC3 RTC trailer when present
    bool  load_save_ram(const uint8_t* data, size_t len);
    bool  has_battery() const { return cart && cart->has_battery && !cart->ram.empty(); }

    // Whole-machine snapshot: CPU, bus memories, PPU, timer, joypad, APU, and
    // cartridge RAM + MBC banks. Distinct from save_ram, which contains only
    // battery-backed cartridge data (SRAM and, for MBC3, RTC state).
    //
    // The stream carries the ROM's size and hash, so load_state rejects a state
    // taken against different ROM bytes instead of resuming into corruption.
    // AI tracker, RNG, pending identity and READY result are transactional.
    bool  save_state(std::vector<uint8_t>& out);
    bool  load_state(const uint8_t* data, size_t len);
    static constexpr uint32_t STATE_VERSION = 4;   // 4: tracker carries the event-vector state

    Bus    bus;
    CPU    cpu;
    PPU    ppu;
    Timer  timer;
    Joypad joypad;
    APU    apu;
    pkai::Hook ai;
    pkai::Memory ai_bus;
    pkai::Memory ai_memory();
    void ai_step(int64_t deadline) { ai.step(ai_memory(), deadline); }
    std::unique_ptr<Cartridge> cart;
private:
    void transfer_state(StateIO& s);               // shared save/load body
    bool check_state_header(StateIO& s) const;
    int frame_budget = 0;
};
