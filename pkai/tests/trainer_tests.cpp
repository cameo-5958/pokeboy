// Trainer party dump vs the ROM source: the ROM's own ReadTrainer is executed
// for every class/party; this checks the derived counts and the special-move
// wiring (LoneMoves, TeamMoves, champion rival) against data/trainers/*.asm.
#include "pkai/tools/trainer_dump.h"
#include <cstdlib>
#include <iostream>
using namespace pkai::trainers;
#define CHECK(x) do { if (!(x)) { std::cerr << __LINE__ << ": " << #x << "\n"; std::exit(1); } } while (0)

static const Class& cls(const Dump& d, const char* name) {
    for (auto& c : d.classes) if (c.name == name) return c;
    CHECK(false); std::abort();
}
static bool knows(const Mon& m, uint8_t move) { for (auto x : m.moves) if (x == move) return true; return false; }

int main() {
    Loader loader(AI_ROM, AI_SYM);
    const Dump d = loader.dump_all();
    CHECK(d.rom_crc32 == pkai::tables::rom_crc32);
    CHECK(d.classes.size() == 47);
    CHECK(d.party_count() == 391);

    unsigned mons = 0;
    for (auto& c : d.classes) {
        CHECK(c.parties.empty() == (c.name == "UNUSED_JUGGLER" || c.name == "CHIEF"));
        for (auto& p : c.parties) {
            CHECK(p.mons.size() >= 1 && p.mons.size() <= 6);
            for (auto& m : p.mons) {
                ++mons;
                CHECK(m.dex >= 1 && m.dex <= 151);
                CHECK(m.level >= 1 && m.level <= 100);
                CHECK(m.move_count() >= 1 && m.move_count() <= 4);
                // ReadTrainer writes special moves into slot 3 without
                // touching PP (enemy PP is never used), so that slot may
                // carry 0 PP when the mon had fewer than 3 level-up moves.
                for (int k = 0; k < 4; ++k) {
                    CHECK(!(m.pp[k] != 0 && m.moves[k] == 0));
                    if (m.moves[k] != 0 && m.pp[k] == 0) CHECK(k == 2 && p.lone_attack_no != 0);
                }
                CHECK(m.max_hp > 0 && m.hp == m.max_hp);
                CHECK(m.attack > 0 && m.defense > 0 && m.speed > 0 && m.special > 0);
                CHECK(m.status == 0);
                CHECK(m.dvs[0] == 0x98 && m.dvs[1] == 0x88);   // ATKDEFDV_TRAINER / SPDSPCDV_TRAINER
            }
        }
    }

    // Brock (data/trainers/parties.asm): Geodude L12, Onix L14; PewterGym.asm
    // sets wGymLeaderNo = 1 so LoneMoves entry 1 gives the 2nd mon Bide.
    const auto& brock = cls(d, "BROCK");
    CHECK(brock.parties.size() == 1);
    const auto& bp = brock.parties[0];
    CHECK(bp.lone_attack_no == 1 && bp.mons.size() == 2);
    CHECK(bp.mons[0].dex == 74 && bp.mons[0].level == 12);   // Geodude
    CHECK(bp.mons[1].dex == 95 && bp.mons[1].level == 14);   // Onix
    CHECK(bp.mons[1].moves[2] == 117 && !knows(bp.mons[0], 117));   // Bide in slot 3 of Onix only
    CHECK(bp.mons[0].species == 0xa9 && bp.mons[1].species == 0x22);
    // Other gym leaders: Misty's Starmie Bubblebeam, Surge's Raichu
    // Thunderbolt, Erika's Vileplume Mega Drain, Koga's Weezing Toxic,
    // Sabrina's Alakazam Psywave, Blaine's Arcanine Fire Blast, Giovanni's
    // Viridian Rhydon Fissure (and not his Hideout/Silph parties).
    CHECK(cls(d, "MISTY").parties[0].mons[1].moves[2] == 61);
    CHECK(cls(d, "LT_SURGE").parties[0].mons[2].moves[2] == 85);
    CHECK(cls(d, "ERIKA").parties[0].mons[2].moves[2] == 72);
    CHECK(cls(d, "KOGA").parties[0].mons[3].moves[2] == 92);
    CHECK(cls(d, "SABRINA").parties[0].mons[3].moves[2] == 149);
    CHECK(cls(d, "BLAINE").parties[0].mons[3].moves[2] == 126);
    const auto& gio = cls(d, "GIOVANNI");
    CHECK(gio.parties.size() == 3 && gio.parties[2].mons.size() == 5 && gio.parties[2].mons[4].moves[2] == 90);
    CHECK(gio.parties[0].lone_attack_no == 0 && !knows(gio.parties[0].mons.back(), 90));
    // TeamMoves: every Elite Four member's 5th mon gets the class move.
    CHECK(cls(d, "LORELEI").parties[0].mons[4].moves[2] == 59);   // Blizzard
    CHECK(cls(d, "BRUNO").parties[0].mons[4].moves[2] == 90);     // Fissure
    CHECK(cls(d, "AGATHA").parties[0].mons[4].moves[2] == 92);    // Toxic
    CHECK(cls(d, "LANCE").parties[0].mons[4].moves[2] == 112);    // Barrier
    // Champion rival: Pidgeot Sky Attack, starter move by wRivalStarter.
    const auto& r3 = cls(d, "RIVAL3");
    CHECK(r3.parties.size() == 3);
    for (auto& p : r3.parties) { CHECK(p.mons.size() == 6 && p.mons[0].moves[2] == 143); }
    CHECK(r3.parties[0].rival_starter == STARTER2 && r3.parties[0].mons[5].dex == 9 && r3.parties[0].mons[5].moves[2] == 59);    // Blastoise Blizzard
    CHECK(r3.parties[1].rival_starter == STARTER3 && r3.parties[1].mons[5].dex == 3 && r3.parties[1].mons[5].moves[2] == 72);    // Venusaur Mega Drain
    CHECK(r3.parties[2].rival_starter == STARTER1 && r3.parties[2].mons[5].dex == 6 && r3.parties[2].mons[5].moves[2] == 126);   // Charizard Fire Blast
    // Level formats: Youngster 1 is a shared-level record, Lance is per-mon.
    CHECK(cls(d, "YOUNGSTER").parties[0].mons.size() == 2 && cls(d, "YOUNGSTER").parties[0].mons[1].level == 11);
    CHECK(cls(d, "LANCE").parties[0].mons[4].dex == 149 && cls(d, "LANCE").parties[0].mons[4].level == 62);
    std::cout << "trainer dump: " << d.party_count() << " parties, " << mons << " mons\n";
    return 0;
}
