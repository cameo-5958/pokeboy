#include "event.h"
#include <cstring>
namespace pkai {
using namespace symbols;
SideSnapshot snapshot_side(const Memory& m, unsigned side) {
    SideSnapshot s{};
    if (side == 0) { s.slot = m.read(wEnemyMonPartyPos); s.hp = m.word(wEnemyMonHP); s.max_hp = m.word(wEnemyMonMaxHP); s.status = m.read(wEnemyMonStatus); }
    else { s.slot = m.read(wPlayerMonNumber); s.hp = m.word(wBattleMonHP); s.max_hp = m.word(wBattleMonMaxHP); s.status = m.read(wBattleMonStatus); }
    return s;
}
void EventState::commit(const Memory& m, uint8_t kind) {
    side[0] = snapshot_side(m, 0); side[1] = snapshot_side(m, 1);
    last_action = kind < 3 ? kind : 3; ++decisions;
}
void EventState::serialize(StateIO& s) {
    side[0].serialize(s); side[1].serialize(s); s.v(last_action); s.v(decisions);
    if (last_action > 3) s.fail();
}
int8_t event_q127(uint32_t a, uint32_t b) {
    if (!b) b = 1;
    const uint64_t n = uint64_t(127) * a;
    uint64_t q = n / b; const uint64_t r = n - q * b;
    if (2 * r > b || (2 * r == b && (q & 1))) ++q;   // half to even, like np.rint
    return q > 127 ? int8_t(127) : int8_t(q);
}
void build_event(const EventState& st, const Memory& m, int8_t* ev) {
    std::memset(ev, 0, EVENT_DIM);
    if (!st.decisions) return;
    if (st.last_action < 3) ev[st.last_action] = 127;
    for (unsigned i = 0; i < 2; ++i) {
        const auto& pre = st.side[i]; const auto now = snapshot_side(m, i);
        int8_t* e = ev + 3 + 6 * i;
        const bool same = pre.slot == now.slot;
        e[0] = same && pre.hp > now.hp ? event_q127(pre.hp - now.hp, pre.max_hp) : 0;
        e[1] = now.hp == 0 ? 127 : 0;
        e[2] = same ? 0 : 127;
        e[3] = pre.status == 0 && now.status != 0 ? 127 : 0;
        e[4] = event_q127(now.hp, now.max_hp);
        e[5] = now.status ? 127 : 0;
    }
    ev[15] = event_q127(st.decisions < 50 ? st.decisions : 50, 50);
}
}
