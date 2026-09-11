#pragma once
// Entity tokens for the pointer-style entity policy (spec §7.3).
// Every token is a fixed int8 feature vector plus up to four categorical ids
// that the on-device model turns into embeddings. Fractions are quantised as
// round(v * 127); flags are 0/127; signed quantities use the full range.
#include "calc.h"
#include "mask.h"
namespace pkai {

constexpr unsigned FEAT = 48;
constexpr unsigned MAX_TOKENS = 27;
enum TokenType : uint8_t { TokField = 0, TokOwnMon = 1, TokPlayerMon = 2, TokOwnMove = 3, TokPlayerMove = 4, TokItem = 5 };

struct Token {
    uint8_t type{}, index{}, present{};
    uint8_t candidate{};       // 1 + action index (1..16) when this token is a policy candidate, else 0
    uint16_t cat[4]{};         // species/move/type/item/effect/matchup ids (0 = none/unknown)
    int8_t f[FEAT]{};
};

struct Features {
    Token tokens[MAX_TOKENS]{};
    uint8_t count{};
    uint16_t legal{};          // 16-way legal mask (bit i = action i)
    uint8_t request_kind{};
};

// Combatants as the damage math sees them: own side exact, player side estimated
// from public information (species base stats, level, neutral DV, stat-exp prior).
calc::Combatant own_combatant(const Memory&, const Observation&);
calc::Combatant player_combatant(const Memory&, const Observation&);

Features build_features(const Memory&, const Observation&, const Mask&, uint8_t request_kind);

// Deterministic 32-bit hash of the feature block (used for speculation keys and parity tests).
uint32_t features_hash(const Features&);

}
