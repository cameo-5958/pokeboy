// GRU update, scalar reference (pep_int.forward, "GRU over [ev ⊕ c_s]").
#include "kernels.h"
namespace pkai::kernels {
using namespace fx;

void gru_step_scalar(const int16_t* gi, const int16_t* gh, const int16_t* h, unsigned g, const Luts& luts,
                     int16_t* r, int16_t* z, int16_t* n, int16_t* h_new) {
    for (unsigned j = 0; j < g; ++j) {
        const int32_t rj = gate_lut(sat16(int32_t(gi[j]) + gh[j]), luts.sigmoid_q14);
        const int32_t zj = gate_lut(sat16(int32_t(gi[g + j]) + gh[g + j]), luts.sigmoid_q14);
        const int64_t rn = rshift_round_even(int64_t(rj) * gh[2 * g + j], 14);      // Q1.14 x Q3.12 -> Q3.12
        const int32_t nj = gate_lut(sat16(int64_t(gi[2 * g + j]) + rn), luts.tanh_q14);
        const int64_t hn = rshift_round_even(int64_t(Q14_ONE - zj) * nj, 14) + rshift_round_even(int64_t(zj) * h[j], 14);
        r[j] = int16_t(rj); z[j] = int16_t(zj); n[j] = int16_t(nj);
        h_new[j] = int16_t(sat16(hn));
    }
}

}
