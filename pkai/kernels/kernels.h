#pragma once
// Integer kernels for the PEP model (spec §8.3). Scalar reference versions carry
// the `_scalar` suffix and are always compiled; the unsuffixed entry points
// dispatch to NEON on __ARM_NEON targets (kernels/neon/*.cpp) and to the scalar
// versions everywhere else. Both produce identical integers by construction.
#include <cstddef>
#include <cstdint>
#include "fixedpoint.h"
namespace pkai::kernels {

// Per-channel (n == C) or broadcast (n == 1) requantisation parameters.
struct Requant { const int32_t* mult{}; const int8_t* shift{}; unsigned n{1}; int32_t mult_at(unsigned c) const { return mult[n == 1 ? 0 : c]; } int shift_at(unsigned c) const { return shift[n == 1 ? 0 : c]; } };

// The four fixed tables shipped in pkai.weights.
struct Luts { const int16_t* exp_q15{}; const int16_t* recip_q15{}; const int16_t* sigmoid_q14{}; const int16_t* tanh_q14{}; };

// acc[r*C + c] = sum_k x[r*K + k] * w[c*K + k]        int8 x int8 -> int32 (exact)
void gemm_s8_scalar(const int8_t* x, unsigned rows, unsigned K, const int8_t* w, unsigned C, int32_t* acc);
void gemm_s8(const int8_t* x, unsigned rows, unsigned K, const int8_t* w, unsigned C, int32_t* acc);

// acc[r*C + c] = sum_j p[r*L + j] * v[j*stride + c]   uint8 x int8 -> int32 (P·V)
void gemm_u8s8_scalar(const uint8_t* p, unsigned rows, unsigned L, const int8_t* v, unsigned stride, unsigned C, int32_t* acc);
void gemm_u8s8(const uint8_t* p, unsigned rows, unsigned L, const int8_t* v, unsigned stride, unsigned C, int32_t* acc);

// out[r*C + c] = sat(requant(acc[r*C + c] + bias[c]), lo, hi); bias may be null. Out is int8_t or int16_t.
void requant_s32_scalar(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int8_t* out);
void requant_s32_scalar(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int16_t* out);
void requant_s32(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int8_t* out);
void requant_s32(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int16_t* out);

// int16 residual stream -> int8 GEMM input (broadcast multiplier).
void requant_s16_scalar(const int16_t* x, unsigned n, Requant rq, int8_t* out);
void requant_s16(const int16_t* x, unsigned n, Requant rq, int8_t* out);

// x += sat16(rshift_round_even(branch * alpha_q14, 14)), saturating (ReZero residual).
void rezero_add_scalar(int16_t* x, const int16_t* branch, unsigned n, int16_t alpha_q14);

// Integer softmax over L int32 logits along one row. mask[j] != 0 participates.
// Optional trace outputs mirror pep_int.softmax_int: d16, p_q15, sum, recip_q15.
void softmax_lut_scalar(const int32_t* x, const uint8_t* mask, unsigned L, int shift, const Luts& luts,
                        uint8_t* probs, int16_t* d16, int16_t* p_q15, int32_t* sum, int32_t* recip);

// One attention head: q (Lq x d, head columns h*dh..), k/v (L x d), key mask (L).
// Fills scores[Lq*L], d16/p[Lq*L], sum/recip[Lq], probs[Lq*L] (uint8 Q0.8),
// pv_acc[Lq*d] head columns and pv8 (requantised with the broadcast pv multiplier).
struct AttentionHead {
    const int8_t* q; const int8_t* k; const int8_t* v; const uint8_t* key_mask;
    unsigned Lq, L, d, head, dh; int logit_shift; Requant pv_rq; const Luts* luts;
    int32_t* scores; int16_t* d16; int16_t* p_q15; int32_t* sum; int32_t* recip; uint8_t* probs;
    int32_t* pv_acc; int8_t* pv8;
};
void attention_head_scalar(const AttentionHead& a);

// GRU update (Cho et al. 2014, reset gate on h): gi/gh are Q3.12 int16[3g] (r, z, n
// slices), h is Q1.14 int16[g]. Writes r, z, n (Q1.14) traces and h_new.
void gru_step_scalar(const int16_t* gi, const int16_t* gh, const int16_t* h, unsigned g, const Luts& luts,
                     int16_t* r, int16_t* z, int16_t* n, int16_t* h_new);

// Embedding gather: out[i*width + k] = table[clamp(id, 0, rows-1)*width + k].
void embed_gather_scalar(const int8_t* table, unsigned rows, unsigned width, uint32_t id, int8_t* out);

const char* backend_name();   // "scalar" or "neon"

}
