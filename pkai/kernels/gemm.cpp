// Scalar reference GEMM / requantisation kernels (portable C++17, integers only).
#include "kernels.h"
namespace pkai::kernels {
using namespace fx;

void gemm_s8_scalar(const int8_t* x, unsigned rows, unsigned K, const int8_t* w, unsigned C, int32_t* acc) {
    for (unsigned r = 0; r < rows; ++r) {
        const int8_t* xr = x + size_t(r) * K;
        for (unsigned c = 0; c < C; ++c) {
            const int8_t* wc = w + size_t(c) * K;
            int32_t s = 0;
            for (unsigned k = 0; k < K; ++k) s += int32_t(xr[k]) * wc[k];
            acc[size_t(r) * C + c] = s;
        }
    }
}

void gemm_u8s8_scalar(const uint8_t* p, unsigned rows, unsigned L, const int8_t* v, unsigned stride, unsigned C, int32_t* acc) {
    for (unsigned r = 0; r < rows; ++r) {
        int32_t* out = acc + size_t(r) * C;
        for (unsigned c = 0; c < C; ++c) out[c] = 0;
        for (unsigned j = 0; j < L; ++j) {
            const int32_t pj = p[size_t(r) * L + j];
            if (!pj) continue;
            const int8_t* vj = v + size_t(j) * stride;
            for (unsigned c = 0; c < C; ++c) out[c] += pj * vj[c];
        }
    }
}

template <class T>
static void requant_rows(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, T* out) {
    for (unsigned r = 0; r < rows; ++r)
        for (unsigned c = 0; c < C; ++c) {
            const size_t i = size_t(r) * C + c;
            const int32_t a = sat32(int64_t(acc[i]) + (bias ? bias[c] : 0));
            out[i] = T(requant(a, rq.mult_at(c), rq.shift_at(c), lo, hi));
        }
}
void requant_s32_scalar(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int8_t* out) { requant_rows(acc, rows, C, bias, rq, lo, hi, out); }
void requant_s32_scalar(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int16_t* out) { requant_rows(acc, rows, C, bias, rq, lo, hi, out); }

void requant_s16_scalar(const int16_t* x, unsigned n, Requant rq, int8_t* out) {
    const int32_t m = rq.mult[0]; const int s = rq.shift[0];
    for (unsigned i = 0; i < n; ++i) out[i] = int8_t(requant(x[i], m, s, I8_MIN, I8_MAX));
}

void rezero_add_scalar(int16_t* x, const int16_t* branch, unsigned n, int16_t alpha_q14) {
    for (unsigned i = 0; i < n; ++i) {
        const int64_t prod = rshift_round_even(int64_t(branch[i]) * alpha_q14, 14);
        x[i] = int16_t(sat16(int64_t(x[i]) + prod));
    }
}

void embed_gather_scalar(const int8_t* table, unsigned rows, unsigned width, uint32_t id, int8_t* out) {
    if (id >= rows) id = rows - 1;
    const int8_t* row = table + size_t(id) * width;
    for (unsigned k = 0; k < width; ++k) out[k] = row[k];
}

// Scalar dispatch unless CMake compiled kernels/neon/*.cpp (PKAI_NEON_KERNELS).
#if !defined(PKAI_NEON_KERNELS)
void gemm_s8(const int8_t* x, unsigned rows, unsigned K, const int8_t* w, unsigned C, int32_t* acc) { gemm_s8_scalar(x, rows, K, w, C, acc); }
void gemm_u8s8(const uint8_t* p, unsigned rows, unsigned L, const int8_t* v, unsigned stride, unsigned C, int32_t* acc) { gemm_u8s8_scalar(p, rows, L, v, stride, C, acc); }
void requant_s32(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int8_t* out) { requant_s32_scalar(acc, rows, C, bias, rq, lo, hi, out); }
void requant_s32(const int32_t* acc, unsigned rows, unsigned C, const int32_t* bias, Requant rq, int32_t lo, int32_t hi, int16_t* out) { requant_s32_scalar(acc, rows, C, bias, rq, lo, hi, out); }
void requant_s16(const int16_t* x, unsigned n, Requant rq, int8_t* out) { requant_s16_scalar(x, n, rq, out); }
const char* backend_name() { return "scalar"; }
#endif

}
