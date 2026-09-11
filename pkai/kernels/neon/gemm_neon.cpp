// NEON versions of the GEMMs and the requantisation (armv7-a / aarch64). Compiled
// to nothing elsewhere. Every lane computes exactly the scalar contract:
//   * int8 x int8 products are exact in int16 (VMULL), pair-accumulated into int32
//     (VPADAL); integer sums are order-independent, so the result equals the scalar loop.
//   * sat32(acc + bias) is VQADD.
//   * the negative-shift branch is a saturating left shift (VQSHL by register).
//   * VQRDMULH is the srdmh() of the spec by definition.
//   * VRSHL rounds ties toward +inf; the half-to-even correction subtracts one from
//     odd results that came from an exact tie (see fx::rshift_round_even).
#if defined(__ARM_NEON) && defined(PKAI_NEON_KERNELS)
#include <arm_neon.h>
#include "../kernels.h"
namespace pkai::kernels {
using namespace fx;

const char* backend_name() { return "neon"; }

static inline int32_t hsum(int32x4_t v) {
    const int32x2_t s = vpadd_s32(vget_low_s32(v), vget_high_s32(v));
    return vget_lane_s32(vpadd_s32(s, s), 0);
}

void gemm_s8(const int8_t* x, unsigned rows, unsigned K, const int8_t* w, unsigned C, int32_t* acc) {
    const unsigned K16 = K & ~15u, K8 = K & ~7u;
    for (unsigned r = 0; r < rows; ++r) {
        const int8_t* xr = x + size_t(r) * K;
        int32_t* out = acc + size_t(r) * C;
        unsigned c = 0;
        for (; c + 4 <= C; c += 4) {
            const int8_t* w0 = w + size_t(c) * K; const int8_t* w1 = w0 + K; const int8_t* w2 = w1 + K; const int8_t* w3 = w2 + K;
            int32x4_t a0 = vdupq_n_s32(0), a1 = a0, a2 = a0, a3 = a0;
            unsigned k = 0;
            for (; k < K16; k += 16) {
                const int8x16_t xv = vld1q_s8(xr + k);
                const int8x16_t v0 = vld1q_s8(w0 + k), v1 = vld1q_s8(w1 + k), v2 = vld1q_s8(w2 + k), v3 = vld1q_s8(w3 + k);
                a0 = vpadalq_s16(a0, vmull_s8(vget_low_s8(xv), vget_low_s8(v0)));
                a0 = vpadalq_s16(a0, vmull_s8(vget_high_s8(xv), vget_high_s8(v0)));
                a1 = vpadalq_s16(a1, vmull_s8(vget_low_s8(xv), vget_low_s8(v1)));
                a1 = vpadalq_s16(a1, vmull_s8(vget_high_s8(xv), vget_high_s8(v1)));
                a2 = vpadalq_s16(a2, vmull_s8(vget_low_s8(xv), vget_low_s8(v2)));
                a2 = vpadalq_s16(a2, vmull_s8(vget_high_s8(xv), vget_high_s8(v2)));
                a3 = vpadalq_s16(a3, vmull_s8(vget_low_s8(xv), vget_low_s8(v3)));
                a3 = vpadalq_s16(a3, vmull_s8(vget_high_s8(xv), vget_high_s8(v3)));
            }
            for (; k < K8; k += 8) {
                const int8x8_t xv = vld1_s8(xr + k);
                a0 = vpadalq_s16(a0, vmull_s8(xv, vld1_s8(w0 + k)));
                a1 = vpadalq_s16(a1, vmull_s8(xv, vld1_s8(w1 + k)));
                a2 = vpadalq_s16(a2, vmull_s8(xv, vld1_s8(w2 + k)));
                a3 = vpadalq_s16(a3, vmull_s8(xv, vld1_s8(w3 + k)));
            }
            int32_t s0 = hsum(a0), s1 = hsum(a1), s2 = hsum(a2), s3 = hsum(a3);
            for (; k < K; ++k) { s0 += int32_t(xr[k]) * w0[k]; s1 += int32_t(xr[k]) * w1[k]; s2 += int32_t(xr[k]) * w2[k]; s3 += int32_t(xr[k]) * w3[k]; }
            out[c] = s0; out[c + 1] = s1; out[c + 2] = s2; out[c + 3] = s3;
        }
        for (; c < C; ++c) {
            const int8_t* wc = w + size_t(c) * K;
            int32_t s = 0;
            for (unsigned k = 0; k < K; ++k) s += int32_t(xr[k]) * wc[k];
            out[c] = s;
        }
    }
}

void gemm_u8s8(const uint8_t* p, unsigned rows, unsigned L, const int8_t* v, unsigned stride, unsigned C, int32_t* acc) {
    for (unsigned r = 0; r < rows; ++r) {
        int32_t* out = acc + size_t(r) * C;
        for (unsigned c = 0; c < C; ++c) out[c] = 0;
        for (unsigned j = 0; j < L; ++j) {
            const int32_t pj = p[size_t(r) * L + j];
            if (!pj) continue;
            const int16x8_t pv = vdupq_n_s16(int16_t(pj));
            const int8_t* vj = v + size_t(j) * stride;
            unsigned c = 0;
            for (; c + 8 <= C; c += 8) {
                const int16x8_t vv = vmovl_s8(vld1_s8(vj + c));
                int32x4_t lo = vld1q_s32(out + c), hi = vld1q_s32(out + c + 4);
                lo = vmlal_s16(lo, vget_low_s16(vv), vget_low_s16(pv));
                hi = vmlal_s16(hi, vget_high_s16(vv), vget_high_s16(pv));
                vst1q_s32(out + c, lo); vst1q_s32(out + c + 4, hi);
            }
            for (; c < C; ++c) out[c] += pj * vj[c];
        }
    }
}

// sat(rshift_round_even(vqrdmulh(vqshl(a, left), mult), right), lo, hi) on four lanes.
static inline int32x4_t requant4(int32x4_t a, int32x4_t mult, int32x4_t shift, int32x4_t lo, int32x4_t hi) {
    const int32x4_t zero = vdupq_n_s32(0), one = vdupq_n_s32(1);
    const int32x4_t left = vmaxq_s32(vnegq_s32(shift), zero);
    const int32x4_t right = vmaxq_s32(shift, zero);
    a = vqshlq_s32(a, left);
    const int32x4_t y = vqrdmulhq_s32(a, mult);
    int32x4_t q = vrshlq_s32(y, vnegq_s32(right));                       // ties toward +inf
    const int32x4_t rem = vandq_s32(y, vsubq_s32(vshlq_s32(one, right), one));
    const int32x4_t half = vshlq_s32(one, vsubq_s32(right, one));         // 0 when right == 0
    const uint32x4_t tie = vandq_u32(vceqq_s32(rem, half), vcgtq_s32(right, zero));
    const int32x4_t corr = vandq_s32(vandq_s32(q, one), vreinterpretq_s32_u32(tie));
    q = vsubq_s32(q, corr);                                               // odd tie result -> even
    return vminq_s32(vmaxq_s32(q, lo), hi);
}

template <class T>
static void requant_neon(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, T* out) {
    const int32x4_t lov = vdupq_n_s32(lo), hiv = vdupq_n_s32(hi);
    for (unsigned r = 0; r < rows; ++r) {
        const int32_t* ar = acc + size_t(r) * C;
        T* orow = out + size_t(r) * C;
        unsigned c = 0;
        for (; c + 8 <= C; c += 8) {
            int32x4_t a0 = vld1q_s32(ar + c), a1 = vld1q_s32(ar + c + 4);
            if (bias) { a0 = vqaddq_s32(a0, vld1q_s32(bias + c)); a1 = vqaddq_s32(a1, vld1q_s32(bias + c + 4)); }
            int32x4_t m0, m1, s0, s1;
            if (rq.n == 1) {
                m0 = m1 = vdupq_n_s32(rq.mult[0]); s0 = s1 = vdupq_n_s32(rq.shift[0]);
            } else {
                m0 = vld1q_s32(rq.mult + c); m1 = vld1q_s32(rq.mult + c + 4);
                const int16x8_t sh = vmovl_s8(vld1_s8(rq.shift + c));
                s0 = vmovl_s16(vget_low_s16(sh)); s1 = vmovl_s16(vget_high_s16(sh));
            }
            const int32x4_t q0 = requant4(a0, m0, s0, lov, hiv), q1 = requant4(a1, m1, s1, lov, hiv);
            const int16x8_t n16 = vcombine_s16(vqmovn_s32(q0), vqmovn_s32(q1));
            if constexpr (sizeof(T) == 1) vst1_s8(reinterpret_cast<int8_t*>(orow + c), vqmovn_s16(n16));
            else vst1q_s16(reinterpret_cast<int16_t*>(orow + c), n16);
        }
        for (; c < C; ++c) {
            const int32_t a = sat32(int64_t(ar[c]) + (bias ? bias[c] : 0));
            orow[c] = T(requant(a, rq.mult_at(c), rq.shift_at(c), lo, hi));
        }
    }
}

void requant_s32(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int8_t* out) { requant_neon(acc, rows, C, bias, rq, lo, hi, out); }
void requant_s32(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int16_t* out) { requant_neon(acc, rows, C, bias, rq, lo, hi, out); }

void requant_s16(const int16_t* x, unsigned n, Requant rq, int8_t* out) {
    const int32x4_t m = vdupq_n_s32(rq.mult[0]), s = vdupq_n_s32(rq.shift[0]);
    const int32x4_t lov = vdupq_n_s32(I8_MIN), hiv = vdupq_n_s32(I8_MAX);
    unsigned i = 0;
    for (; i + 8 <= n; i += 8) {
        const int16x8_t xv = vld1q_s16(x + i);
        const int32x4_t q0 = requant4(vmovl_s16(vget_low_s16(xv)), m, s, lov, hiv);
        const int32x4_t q1 = requant4(vmovl_s16(vget_high_s16(xv)), m, s, lov, hiv);
        vst1_s8(out + i, vqmovn_s16(vcombine_s16(vqmovn_s32(q0), vqmovn_s32(q1))));
    }
    for (; i < n; ++i) out[i] = int8_t(requant(x[i], rq.mult[0], rq.shift[0], I8_MIN, I8_MAX));
}

}
#endif
