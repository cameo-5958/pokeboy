#pragma once
#include "observe.h"
namespace pkai {
struct Mask { uint16_t bits{}; bool struggle{}; uint8_t item{}; bool legal(unsigned i) const { return i<16 && (bits&(1u<<i)); } };
struct Action { uint8_t kind{}, payload{}, sequence{}; void serialize(StateIO& s) { s.v(kind);s.v(payload);s.v(sequence); } };
Mask legal_mask(const Memory&, const Observation&, uint8_t request_kind);
Action random_action(const Mask&, uint32_t draw, uint8_t sequence);
bool legal_action(const Mask&, const Action&);
}
