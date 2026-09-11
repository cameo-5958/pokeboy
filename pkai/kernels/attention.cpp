// LUT softmax and single-head attention, scalar reference (pep_int.softmax_int / _attention).
#include "kernels.h"
namespace pkai::kernels {
using namespace fx;

static constexpr unsigned SOFTMAX_MAX_L = 64;

void softmax_lut_scalar(const int32_t* x, const uint8_t* mask, unsigned L, int shift, const Luts& luts,
                        uint8_t* probs, int16_t* d16, int16_t* p_q15, int32_t* sum, int32_t* recip) {
    int32_t p[SOFTMAX_MAX_L];
    if (L > SOFTMAX_MAX_L) L = SOFTMAX_MAX_L;
    int64_t m = I32_MIN;
    for (unsigned j = 0; j < L; ++j) if (mask[j] && x[j] > m) m = x[j];
    int32_t s = 0;
    for (unsigned j = 0; j < L; ++j) {
        int64_t d = mask[j] ? m - x[j] : 0;
        int64_t v;
        if (shift >= 0) v = rshift_round_even(d, unsigned(shift));
        else { if (d > (int64_t(1) << 20)) d = int64_t(1) << 20; v = d << unsigned(-shift); }
        const int32_t dv = int32_t(clamp64(v, 0, I16_MAX));
        p[j] = mask[j] ? exp_lut(dv, luts.exp_q15) : 0;
        if (d16) d16[j] = int16_t(dv);
        if (p_q15) p_q15[j] = int16_t(p[j]);
        s += p[j];
    }
    const bool any = s > 0;
    const int32_t s_safe = any ? s : (1 << 14);
    const int n = 15 - int(bit_length(s_safe));                       // s_safe << n in [2^14, 2^15)
    const int32_t s_n = n >= 0 ? s_safe << n : s_safe >> (-n);
    const int32_t idx = (s_n >> 6) - 256;                             // 0..255 by construction
    const int64_t r0 = luts.recip_q15[idx < 0 ? 0 : idx > 255 ? 255 : idx];
    const int64_t err = (int64_t(1) << 29) - int64_t(s_n) * r0;
    const int64_t r1 = r0 + ((r0 * err) >> 29);                        // one Newton step
    if (sum) *sum = s;
    if (recip) *recip = int32_t(r1);
    const unsigned pshift = unsigned(21 - n);
    for (unsigned j = 0; j < L; ++j) {
        if (!(mask[j] && any)) { probs[j] = 0; continue; }
        probs[j] = uint8_t(clamp64(rshift_round_even(int64_t(p[j]) * r1, pshift), 0, 255));
    }
}

void attention_head_scalar(const AttentionHead& a) {
    const unsigned off = a.head * a.dh;
    for (unsigned i = 0; i < a.Lq; ++i) {
        const int8_t* qi = a.q + size_t(i) * a.d + off;
        int32_t* row = a.scores + size_t(i) * a.L;
        for (unsigned j = 0; j < a.L; ++j) {
            const int8_t* kj = a.k + size_t(j) * a.d + off;
            int32_t s = 0;
            for (unsigned c = 0; c < a.dh; ++c) s += int32_t(qi[c]) * kj[c];
            row[j] = s;
        }
        softmax_lut_scalar(row, a.key_mask, a.L, a.logit_shift, *a.luts, a.probs + size_t(i) * a.L,
                           a.d16 ? a.d16 + size_t(i) * a.L : nullptr, a.p_q15 ? a.p_q15 + size_t(i) * a.L : nullptr,
                           a.sum ? a.sum + i : nullptr, a.recip ? a.recip + i : nullptr);
        int32_t* pv = a.pv_acc + size_t(i) * a.d + off;
        for (unsigned c = 0; c < a.dh; ++c) pv[c] = 0;
        const uint8_t* pi = a.probs + size_t(i) * a.L;
        for (unsigned j = 0; j < a.L; ++j) {
            const int32_t pj = pi[j];
            if (!pj) continue;
            const int8_t* vj = a.v + size_t(j) * a.d + off;
            for (unsigned c = 0; c < a.dh; ++c) pv[c] += pj * vj[c];
        }
        int8_t* o = a.pv8 + size_t(i) * a.d + off;
        for (unsigned c = 0; c < a.dh; ++c) o[c] = int8_t(requant(pv[c], a.pv_rq.mult[0], a.pv_rq.shift[0], I8_MIN, I8_MAX));
    }
}

}
