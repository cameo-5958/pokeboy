#pragma once
#include <cstdint>
#include <cstddef>
#include <memory>

#include "bus/bus.h"
#include "cpu/cpu.h"
#include "ppu/ppu.h"
#include "timer/timer.h"
#include "joypad/joypad.h"
#include "apu/apu.h"
#include "cart/cart.h"

class GameBoy {
public:
    bool  load_rom(const uint8_t* data, size_t len);
    void  reset_post_boot();                       // PDF §4.3 — byte-exact
    void  run_frame();                             // exactly 70224 T-cycles
    const uint8_t* framebuffer() const { return ppu.fb; }        // 160*144, 0-3
    void  set_input(uint8_t buttons, uint8_t dpad) {
        uint8_t pressed = (uint8_t)((buttons & ~joypad.buttons) | (dpad & ~joypad.dpad));
        joypad.buttons = buttons; joypad.dpad = dpad;
        if (pressed) bus.if_reg |= 0x10;           // joypad interrupt on new press
    }
    int   read_audio(float* stereo, int max_frames) { return apu.drain(stereo, max_frames); }
    const uint8_t* save_ram(size_t* len) const;    // battery saves (M4)
    bool  load_save_ram(const uint8_t* data, size_t len);
    bool  has_battery() const { return cart && cart->has_battery && !cart->ram.empty(); }

    Bus    bus;
    CPU    cpu;
    PPU    ppu;
    Timer  timer;
    Joypad joypad;
    APU    apu;
    std::unique_ptr<Cartridge> cart;
private:
    int frame_budget = 0;
};
