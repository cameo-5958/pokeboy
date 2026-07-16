#pragma once
#include <cstdint>
#include "state/state.h"
class Bus;
struct Timer {
    uint16_t counter = 0;
    uint8_t  tima = 0, tma = 0, tac = 0;
    bool     prev_signal = false;
    uint8_t  div() const { return counter >> 8; }
    void     reset_div() { counter = 0; }
    void     tick(int tcycles, Bus& bus);
    void     serialize(StateIO& s) {
        s.v(counter); s.v(tima); s.v(tma); s.v(tac); s.v(prev_signal);
    }
};
