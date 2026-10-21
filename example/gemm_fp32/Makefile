# Single-tile FP32 GEMM (CHARM mm1_kernel3_L0, 32x32x32). Vitis 2024.2.
# One command builds everything CHAIDO needs:  make   (= aie + aiesim)
SETUP    ?= /tools/xilinx/Vitis/2024.2/settings64.sh
PLATFORM ?= /tools/xilinx/Vitis/2024.2/base_platforms/xilinx_vck190_base_202420_1/xilinx_vck190_base_202420_1.xpfm

SRC   = src
GRAPH = $(SRC)/graph.cc
INCS  = -I$(SRC)
DEPS  = $(GRAPH) $(SRC)/graph.h $(SRC)/kernel.cc $(SRC)/para_L0.h

.PHONY: all aie aiesim clean

# compile for the AIE (-> Work/, Map_Report.csv) then run the fault-free sim
# (-> aiesimulator_output/output.txt = golden + profile_instr_25_1.txt = CHAIDO's profile).
all: aie aiesim

libadf.a: $(DEPS)
	. $(SETUP) && v++ -c --mode aie --target=hw --platform=$(PLATFORM) $(INCS) $(GRAPH) -o $@
aie: libadf.a

aiesim:
	. $(SETUP) && aiesimulator --pkg-dir=Work --input-dir=data --profile

clean:
	rm -rf Work aiesimulator_output .Xil *.log libadf.a ISS_RPC_SERVER_PORT \
	       .AIE_SIM_CMD_LINE_OPTIONS pl_sample_count* sol.db throughput_info.json \
	       AIESim_FIFO_Guidance.json Map_Report.csv xcd.log chaido/results
