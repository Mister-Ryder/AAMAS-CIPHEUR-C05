// Direct MWIS-to-MIP adapter for the author's unmodified feasibilityjump.hh.
// The optimizer itself is from SINTEF/feasibilityjump, commit 93f1c2ae4bb00fa333dd82c23d7d1abec7dd4fc5.
#include "../../source/feasibilityjump/feasibilityjump.hh"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using clock_type = std::chrono::steady_clock;

int main(int argc, char **argv) {
    if (argc != 5) {
        std::cerr << "usage: fj_mwis GRAPH SEED SEARCH_SECONDS RESULT_JSON\n";
        return 2;
    }
    const std::string graph_path = argv[1], output_path = argv[4];
    const unsigned int seed = static_cast<unsigned int>(std::stoul(argv[2]));
    const double time_limit = std::stod(argv[3]);
    if (!(time_limit > 0.0)) return 2;
    std::ifstream in(graph_path);
    if (!in) throw std::runtime_error("cannot open graph");
    std::string line;
    if (!std::getline(in, line)) throw std::runtime_error("missing METIS header");
    std::istringstream header(line);
    uint32_t n = 0;
    uint64_t expected_edges = 0;
    int format = 0;
    if (!(header >> n >> expected_edges >> format) || format != 10 || n == 0)
        throw std::runtime_error("expected nonempty METIS10 graph");

    FeasibilityJumpSolver solver(static_cast<int>(seed), 0, 1.0);
    std::vector<int64_t> weights;
    weights.reserve(n);
    std::vector<std::pair<uint32_t, uint32_t>> edges;
    edges.reserve(expected_edges);
    for (uint32_t u = 0; u < n; ++u) {
        if (!std::getline(in, line)) throw std::runtime_error("missing METIS vertex row");
        std::istringstream row(line);
        int64_t weight = 0;
        if (!(row >> weight) || weight < 0 || weight > 9007199254740991LL)
            throw std::runtime_error("vertex weight outside exact-double integer range");
        weights.push_back(weight);
        uint32_t v_one_based;
        while (row >> v_one_based) {
            if (v_one_based < 1 || v_one_based > n || v_one_based == u + 1)
                throw std::runtime_error("bad METIS neighbor index");
            if (v_one_based <= u + 1) continue;
            edges.emplace_back(u, v_one_based - 1);
        }
    }
    if (edges.size() != expected_edges)
        throw std::runtime_error("METIS edge count mismatch");
    int64_t total = 0;
    for (auto w : weights) {
        if (w > std::numeric_limits<int64_t>::max() - total)
            throw std::runtime_error("total weight overflows signed64");
        total += w;
    }
    if (total > 9007199254740991LL)
        throw std::runtime_error("total objective outside exact-double integer range");

    for (auto weight : weights) {
        // Power-of-two scaling preserves the exact objective ordering while
        // matching the author's unit constraint-penalty scale.
        solver.addVar(VarType::Integer, 0.0, 1.0, -std::ldexp(static_cast<double>(weight), -29));
    }
    double row_coeffs[2] = {1.0, 1.0};
    for (const auto [u, v] : edges) {
        int row_ids[2] = {static_cast<int>(u), static_cast<int>(v)};
        solver.addConstraint(RowType::Lte, 1.0, 2, row_ids, row_coeffs, 0);
    }

    std::vector<uint32_t> best;
    int64_t best_value = -1;
    const auto started = clock_type::now();
    double best_time = -1.0;
    uint64_t improvements = 0;
    double last_checkpoint = -1.0;
    auto save_best = [&](double elapsed, bool final) {
        if (best_value < 0) return;
        const std::string temporary_path = output_path + ".tmp";
        std::ofstream out(temporary_path);
        if (!out) throw std::runtime_error("cannot open checkpoint JSON");
        out << "{\"schema\":\"published_mwvc_native_v1\",\"solver\":\"FeasibilityJump\","
            << "\"seed\":" << seed << ",\"selected\":[";
        for (size_t i = 0; i < best.size(); ++i) {
            if (i) out << ',';
            out << best[i];
        }
        out << "],\"value_ticks\":" << best_value
            << ",\"cover_weight_ticks\":" << (total - best_value)
            << ",\"total_weight_ticks\":" << total
            << ",\"improvements\":" << improvements
            << ",\"best_time_seconds\":" << best_time
            << ",\"search_seconds\":" << elapsed
            << ",\"checkpoint_final\":" << (final ? "true" : "false")
            << ",\"objective_scale\":\"2^-29 exactly\"}\n";
        out.close();
        if (!out) throw std::runtime_error("cannot flush checkpoint JSON");
        if (std::rename(temporary_path.c_str(), output_path.c_str()) != 0)
            throw std::runtime_error("cannot atomically replace checkpoint JSON");
        last_checkpoint = elapsed;
    };
    solver.solve(nullptr, [&](FJStatus status) {
        const double elapsed = std::chrono::duration<double>(clock_type::now() - started).count();
        if (status.solution) {
            std::vector<uint32_t> candidate;
            int64_t value = 0;
            for (uint32_t u = 0; u < n; ++u) {
                const double x = status.solution[u];
                if (x == 1.0) { candidate.push_back(u); value += weights[u]; }
                else if (x != 0.0) throw std::runtime_error("FJ emitted nonbinary assignment");
            }
            if (value > best_value) {
                best = std::move(candidate);
                best_value = value;
                best_time = elapsed;
                ++improvements;
                if (last_checkpoint < 0.0 || elapsed - last_checkpoint >= 2.0)
                    save_best(elapsed, false);
            }
        }
        return elapsed >= time_limit ? CallbackControlFlow::Terminate : CallbackControlFlow::Continue;
    });
    if (best_value < 0) throw std::runtime_error("FJ returned no feasible assignment");
    save_best(std::chrono::duration<double>(clock_type::now() - started).count(), true);
    std::cout << "selected=" << best.size() << " value_ticks=" << best_value << '\n';
    return 0;
}
