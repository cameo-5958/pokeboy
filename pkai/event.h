#pragma once
// Event vector of the PEP GRU input: what happened to each active Pokémon since
// the trainer's previous decision, as ai/sim/trainer_env.py TrainerEnv._event
// produced it during training, quantised exactly like IntPEP.quantize_event
// (int8 at EV_SCALE = 1/127, round half to even, saturate) using integers only.
//
// Layout (entries 16..63 are zero): [0..2] one-hot of the previous action class
// (move / switch / item); for side i (0 = the trainer, 1 = the player) at
// base = 3 + 6*i: [base] fraction of max HP the active mon lost (0 if it was
// replaced), [+1] active HP is 0, [+2] active slot changed, [+3] status went
// from none to some, [+4] current HP / max HP, [+5] status is nonzero now;
// [15] = min(updates, 50) / 50.
#include <cstdint>
#include "memory.h"
#include "../gameboy/core/state/state.h"
namespace pkai {
constexpr unsigned EVENT_DIM = 64;
struct SideSnapshot {
    uint8_t slot{}, status{};      // party index of the active mon, raw status byte
    uint16_t hp{}, max_hp{};
    void serialize(StateIO& s) { s.v(slot); s.v(status); s.v(hp); s.v(max_hp); }
};
// Persists across the decisions of one battle (reset by $DB, part of the save state).
struct EventState {
    SideSnapshot side[2]{};        // [0] = enemy (the trainer), [1] = player
    uint8_t last_action{3};        // Action::kind of the previous decision, 3 = none
    uint32_t decisions{};          // the sim's `round`: updates so far
    // Taken when a decision is published: the sim snapshots before each update.
    void commit(const Memory&, uint8_t action_kind);
    // The sim's PASS update (player replaces a fainted mon, no trainer decision).
    void auto_step(const Memory& m) { commit(m, 3); }
    void serialize(StateIO&);
};
SideSnapshot snapshot_side(const Memory&, unsigned side);
// rint(127*a/b) with ties to even, saturated to 127; b == 0 reads as 1 (the
// sim's max(1, max_hp)). Bit-identical to np.rint((a/b)/EV_SCALE) for every
// 0 <= a <= b < 1000 (pkai_event_tests sweeps it against a Python table).
int8_t event_q127(uint32_t a, uint32_t b);
// Fills ev8 from the previous state and the current RAM. All zeros before the
// first update, like the sim's initial last_event.
void build_event(const EventState&, const Memory&, int8_t ev8[EVENT_DIM]);
}
