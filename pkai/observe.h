#pragma once
#include "tracker.h"
namespace pkai {
struct OwnMon {
    uint8_t species{}, level{}, status{}, moves[4]{}, types[2]{}, dvs[2]{};
    uint16_t hp{}, max_hp{}, stats[4]{};
};
struct Observation {
    std::array<OwnMon,6> own{};
    std::array<PublicMon,6> player{};
    OwnMon active{};
    uint8_t own_count{}, player_count{}, own_slot{}, player_slot{}, trainer_class{}, count{}, disabled{};
    uint8_t stages[6]{}, battle_status[3]{}, substitute{}, player_stages[6]{}, player_visible_status[3]{};
    uint8_t confusion_counter{}, toxic_counter{};
    uint32_t round{};
};
Observation observe(const Memory&, const Tracker&);
}
