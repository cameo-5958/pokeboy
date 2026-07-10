#include "timer.h"
#include "bus/bus.h"
void Timer::tick(int tcycles, Bus& bus) {
    for (int i = 0; i < tcycles; i++) {
        counter++;
        static const int bit[4] = {9, 3, 5, 7};
        bool signal = (tac & 0x04) && (counter & (1 << bit[tac & 3]));
        if (prev_signal && !signal)
            if (++tima == 0) { tima = tma; bus.if_reg |= 0x04; }
        prev_signal = signal;
    }
}
