#pragma once

#include <adf.h>
#include "para_L0.h"

// Per-tile shape from CHARM 2.0 FP32 SGEMM:
//   L0_h1=32 (TI), L0_w1=32 (TK), L0_w2=32 (TJ)
// One iteration computes C[32][32] = A[32][32] * B[32][32] in FP32.
// This is the 32x32x32 single-AIE workload from CHARM 2.0 Section 5.3.

constexpr int A_BYTES = L0_h1 * L0_w1 * 4;   // 4096
constexpr int B_BYTES = L0_w1 * L0_w2 * 4;   // 4096
constexpr int C_BYTES = L0_h1 * L0_w2 * 4;   // 4096

// Baseline = a single GEMM call; the bundled inputs are sized for exactly
// one iteration (GEMM_ITERS=1).
#ifndef GEMM_ITERS
#define GEMM_ITERS 1
#endif

class TopGraph : public adf::graph {
private:
    adf::kernel k;

public:
    adf::input_plio  a_plio;
    adf::input_plio  b_plio;
    adf::output_plio c_plio;

    TopGraph() {
        // Basenames pair with the simulator's --input-dir=data flag.
        a_plio = adf::input_plio::create("a",  adf::plio_128_bits,
                                         "input_A.txt");
        b_plio = adf::input_plio::create("b",  adf::plio_128_bits,
                                         "input_B.txt");
        c_plio = adf::output_plio::create("c", adf::plio_128_bits,
                                          "output.txt");

        k = adf::kernel::create(mm1_kernel3_L0);
        adf::source(k)                = "kernel.cc";
        adf::runtime<adf::ratio>(k)   = 1.0;
        adf::location<adf::kernel>(k) = adf::tile(25, 1);

        adf::connect<adf::window<A_BYTES>>(a_plio.out[0], k.in[0]);
        adf::connect<adf::window<B_BYTES>>(b_plio.out[0], k.in[1]);
        adf::connect<adf::window<C_BYTES>>(k.out[0], c_plio.in[0]);
    }
};
