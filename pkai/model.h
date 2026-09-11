#pragma once
// PEP integer model (spec §8, executable specification ai/models/pep_int.py).
// One decision: Features + int8 event vector + int16 GRU hidden -> pointer
// logits over the 16 actions, uint8 probabilities at temperature 0.5, value
// accumulator and the new hidden state. Runs either in one shot (`run`) or as a
// fixed program of sub-stages (`begin`/`step`) so the scheduler can admit each
// against its frame deadline: one entity token of input projection, one 8-row
// block of a GEMM, one attention head, one GRU step, one LUT/requant pass.
// All scratch is allocated once in bind(); nothing is allocated per decision.
#include <cstdint>
#include <string>
#include <vector>
#include "features.h"
#include "weights.h"
#include "kernels/kernels.h"
namespace pkai {

constexpr unsigned EV_DIM = 64;
constexpr unsigned N_ACTIONS = 16;
constexpr unsigned N_TOKEN_TYPES = 6;
constexpr unsigned GEMM_BLOCK_ROWS = 8;

enum ModelOpKind : uint8_t { OpEmbed = 0, OpGemm = 1, OpAttnHead = 2, OpGru = 3, OpPass = 4, OpKindCount = 5 };
const char* model_op_kind_name(ModelOpKind);

// Called with every intermediate that pep_int records (same names, same C-order
// bytes) as soon as it is complete. Used by the bit-exactness test.
using TraceSink = void (*)(void* ctx, const char* name, const void* data, size_t nbytes);

class PepModel {
public:
    static constexpr unsigned MAX_OPS = 256;     // fixed op-program table
    bool bind(const Weights& w);                 // resolve every tensor; false + error() on mismatch
    bool bound() const { return bound_; }
    const std::string& error() const { return error_; }
    const WeightsConfig& config() const { return cfg_; }
    unsigned gru_size() const { return cfg_.gru; }

    void set_trace(TraceSink sink, void* ctx) { sink_ = sink; sink_ctx_ = ctx; }

    // One-shot decision.
    void run(const Features& f, const int8_t* ev8, const int16_t* h_in);
    // Resumable decision: begin() then step() until it returns true.
    void begin(const Features& f, const int8_t* ev8, const int16_t* h_in);
    bool step();                                 // executes one op; true when the decision is complete
    void cancel() { pc_ = n_ops_; active_ = false; }   // abandon a decision (timeout); outputs are then stale
    bool done() const { return pc_ >= n_ops_; }
    bool active() const { return active_; }
    ModelOpKind next_kind() const { return done() ? OpPass : ModelOpKind(ops_[pc_].kind); }
    unsigned op_count() const { return n_ops_; }
    unsigned op_index() const { return pc_; }

    // Outputs (valid after done()).
    const int16_t* logits_q8() const { return logits_q8_; }   // 1/256 nat, temperature 1, masked = -32768
    const int16_t* logits_t() const { return logits_t_; }     // after temperature 0.5 (<< 1)
    const uint8_t* probs() const { return probs_; }           // Q0.8 softmax of logits_t over legal actions
    int32_t value_acc() const { return value_acc_; }
    const int16_t* hidden() const { return h_new_.data(); }   // Q1.14 int16[gru]

private:
    struct Linear { const int8_t* w{}; const int32_t* b{}; kernels::Requant rq; unsigned C{}, K{}; };
    struct Layer { kernels::Requant attn_in, ffn_in; Linear q, k, v, o, f1, f2; kernels::Requant pv; int logit_shift{}; int16_t alpha_attn{}, alpha_ffn{}; };
    struct Embed { Linear lin; const int8_t* tables[4]{}; unsigned rows[4]{}, widths[4]{}, cols[4]{}, n_tables{}, first{}, count{}; const char* name{}; };
    struct Op { uint8_t kind, fn, layer, index; };

    bool resolve_linear(const Weights&, const char* name, unsigned C, unsigned K, bool bias, Linear&);
    bool resolve_requant(const Weights&, const char* name, unsigned n, kernels::Requant&);
    bool resolve_table(const Weights&, const char* name, unsigned width, const int8_t*&, unsigned& rows);
    void build_program();
    void exec(const Op&);
    void gemm_block(const int8_t* x, unsigned K, const Linear& lin, unsigned block, int8_t* out, int32_t lo, int32_t hi);
    void gemm_block(const int8_t* x, unsigned K, const Linear& lin, unsigned block, int16_t* out, int32_t lo, int32_t hi);
    void gemm_rows(const int8_t* x, unsigned rows, const Linear& lin, int8_t* out, int32_t lo, int32_t hi);
    void gemm_rows(const int8_t* x, unsigned rows, const Linear& lin, int16_t* out, int32_t lo, int32_t hi);
    void run_head(unsigned layer_or_pool, unsigned head, bool pool);
    void finish_pointer();
    void emit(const char* name, const void* data, size_t nbytes) { if (sink_) sink_(sink_ctx_, name, data, nbytes); }
    void emit_layer(unsigned layer, const char* suffix, const void* data, size_t nbytes);

    bool bound_{}, active_{};
    std::string error_;
    WeightsConfig cfg_{};
    unsigned d_{}, heads_{}, dh_{}, ffn_{}, g_{}, layers_{}, blocks_{};
    kernels::Luts luts_{};
    Embed embeds_[N_TOKEN_TYPES]{};
    std::vector<Layer> layer_;
    kernels::Requant pool_in_{}, pool_pv_{}, gru_h8_{}, ptr_in_{};
    Linear pool_k_{}, pool_v_{}, pool_o_{}, gru_ih_{}, gru_hh_{}, ctx_{}, ptr_q_{}, ptr_k_{};
    const int8_t* pool_q8_{}; int pool_logit_shift_{}, ptr_logit_shift_{};
    const int16_t* ptr_b_type_{}; const int8_t* value_w_{}; int32_t value_b_{};

    // Program.
    Op ops_[MAX_OPS]{}; unsigned n_ops_{}, pc_{};

    // Inputs of the current decision.
    Features feat_{}; int8_t ev8_[EV_DIM]{}; std::vector<int16_t> h_in_;
    uint8_t key_mask_[MAX_TOKENS]{};

    // Scratch (sized in bind()).
    std::vector<int16_t> x16_, o16_, gi_, gh_, r_, z_, n_, h_new_, d16_, p_q15_;
    std::vector<int8_t> in8_, a8_, q8_, k8_, v8_, pv8_, f8_, hid8_, cs8_, gx8_, h8_, hc8_, cx8_, c8_, pq8_, tk8_, pk8_;
    std::vector<int32_t> acc_, scores_, sum_, recip_, pv_acc_;
    std::vector<uint8_t> probs_attn_;
    int32_t score32_[MAX_TOKENS]{}; int16_t token_logit16_[MAX_TOKENS]{};
    int16_t logits_q8_[N_ACTIONS]{}, logits_t_[N_ACTIONS]{}, ptr_d16_[N_ACTIONS]{}, ptr_p_[N_ACTIONS]{};
    uint8_t probs_[N_ACTIONS]{}; int32_t ptr_sum_{}, ptr_recip_{}, value_acc_{};
    TraceSink sink_{}; void* sink_ctx_{};
};

}
