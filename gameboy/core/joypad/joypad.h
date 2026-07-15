#pragma once
#include <cstdint>
#include "state/state.h"
struct Joypad {
    uint8_t select = 0x30, buttons = 0, dpad = 0;   // masks: 1 = held
    uint8_t read() const {
        uint8_t lines = 0x0F;
        if (!(select & 0x20)) lines &= ~buttons;    // A=1 B=2 Sel=4 Start=8
        if (!(select & 0x10)) lines &= ~dpad;       // R=1 L=2 U=4 D=8
        return 0xC0 | select | lines;
    }
    void serialize(StateIO& s) { s.v(select); s.v(buttons); s.v(dpad); }
};
