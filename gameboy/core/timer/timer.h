#pragma once
#include <cstdint>
class Bus;
struct Timer {
    uint16_t counter = 0;
    uint8_t  tima = 0, tma = 0, tac = 0;
    bool     prev_signal = false;
    uint8_t  div() const { return counter >> 8; }
    void     reset_div() { counter = 0; }
    void     tick(int tcycles, Bus& bus);
};
