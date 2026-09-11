#pragma once
#include <array>
#include "memory.h"
#include "event.h"
#include "../gameboy/core/state/state.h"
namespace pkai {
enum EventKind : uint8_t { RoundBegin=0, MoveBeginPlayer=2, MoveBeginEnemy=3,
    MoveEndPlayer=4, MoveEndEnemy=5, ResidualPlayer=6, ResidualEnemy=7,
    FaintPlayer=8, FaintEnemy=9, SwitchPlayer=10, SwitchEnemy=11,
    AnnouncedPlayer=12, AnnouncedEnemy=13, PlayerItem=14 };
struct PublicMon {
    bool known{};
    uint8_t species{}, level{}, status{}, types[2]{}, moves[4]{};
    uint8_t revealed_moves[21]{}; // move IDs 1..165, including called moves
    uint16_t hp{}, max_hp{};
    uint32_t observed_round{};
    void serialize(StateIO& s);
};
struct Sample {
    uint16_t hp[2]{};
    uint8_t substitute[2]{}, status[2]{}, stages[2][6]{};
    void serialize(StateIO& s);
};
struct Event {
    uint8_t kind{}, actor{}, move{}, crit{}, miss{}, effectiveness{};
    bool announced{}, outcome_valid{}, defender_fainted{};
    uint16_t hp_loss[2]{}, substitute_loss[2]{}, last_damage{};
    int8_t stage_delta[2][6]{};
    uint8_t status_before[2]{}, status_after[2]{};
    uint32_t round{};
    void serialize(StateIO& s);
};
struct Tracker {
    std::array<PublicMon,6> player{};
    std::array<Event,64> events{};
    std::array<int16_t,128> hidden{};
    EventState event{};   // GRU event-vector state, see event.h
    Sample before{};
    uint32_t round{}, event_count{}, dropped{};
    uint8_t head{}, size{}, pending{}, actor{}, announced_move{};
    bool move_open{}, announced{}, overflow{};
    void reset() { *this = Tracker{}; }
    void capture(const Memory&, uint8_t kind, uint8_t returned_b);
    void drain_one() { if (pending) --pending; }
    void serialize(StateIO& s);
};
uint8_t public_status(uint8_t raw);
Sample sample(const Memory&);
}
