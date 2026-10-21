#ifndef PARA_L0_H
#define PARA_L0_H
// Tiling constants for mm1_kernel3_L0 (32x32x32 FP32 GEMM).
// Derived from CHARM (https://github.com/arc-research-lab/CHARM), MIT License,
// Copyright (c) 2022 Jinming Zhuang. See ../THIRD_PARTY_NOTICES.txt.
#include <adf/stream/types.h>

const int L0_h1=32;
const int L0_w1=32;
const int L0_w2=32;
const int L0_boundary_i=L0_h1/8;
const int L0_boundary_j=L0_w2/2;
const int L0_boundary_k=L0_w1/8-1;
const int L0_jump_B0=L0_w1-4;
const int L0_jump_A0=L0_h1+8;
const int L0_judge_i=L0_boundary_i-1;
const int L0_judge_j=L0_boundary_j-1;

void mm1_kernel3_L0(input_window_float* __restrict matA, input_window_float* __restrict matB, output_window_float* __restrict matC);

#endif
