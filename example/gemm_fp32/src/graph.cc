#include "graph.h"

TopGraph g;

int main(int /*argc*/, char ** /*argv*/) {
    g.init();
    g.run(GEMM_ITERS);
    g.end();
    return 0;
}
