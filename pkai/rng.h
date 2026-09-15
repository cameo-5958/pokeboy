#pragma once
#include <cstdint>
#include "../gameboy/core/state/state.h"
namespace pkai {
struct Rng {
    uint32_t state[4]{1,2,3,4};
    static uint32_t rot(uint32_t x,int k) { return (x<<k)|(x>>(32-k)); }
    void seed(uint32_t x) { for(auto& v:state) { x+=0x9e3779b9; uint32_t z=x; z=(z^(z>>16))*0x85ebca6b; z=(z^(z>>13))*0xc2b2ae35; v=z^(z>>16); } }
    uint32_t next() { auto result=rot(state[1]*5,7)*9; auto t=state[1]<<9;
        state[2]^=state[0];state[3]^=state[1];state[1]^=state[2];state[0]^=state[3];state[2]^=t;state[3]=rot(state[3],11);return result; }
    void serialize(StateIO& s) { s.arr(state); }
};
}
