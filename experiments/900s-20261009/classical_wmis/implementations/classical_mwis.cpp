// Independent classical MWIS baselines for the CP-SCALE-AU-L002 conflict graphs.
// C++17, one process and one thread. No C05 or CHILS source is used.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using Clock = std::chrono::steady_clock;
using i64 = std::int64_t;

struct Graph {
    int n = 0;
    i64 undirected_edges = 0;
    std::vector<i64> weight;
    std::vector<int> first;
    std::vector<int> adjacent;
    std::vector<double> repair_score;
};

Graph read_dimacs(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open graph: " + path);
    std::string line;
    Graph g;
    std::vector<std::pair<int, int>> edges;
    std::vector<unsigned char> has_weight;
    bool has_header = false;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == 'c') continue;
        std::istringstream row(line);
        char tag;
        row >> tag;
        if (tag == 'p') {
            std::string kind;
            if (has_header || !(row >> kind >> g.n >> g.undirected_edges) || kind != "edge" ||
                g.n < 1 || g.undirected_edges < 0)
                throw std::runtime_error("expected DIMACS header: p edge n m");
            has_header = true;
            g.weight.assign(g.n, 0);
            has_weight.assign(g.n, 0);
            edges.reserve(static_cast<std::size_t>(g.undirected_edges));
        } else if (tag == 'n') {
            int id;
            i64 weight;
            if (!has_header || !(row >> id >> weight) || id < 1 || id > g.n || weight <= 0 ||
                has_weight[id - 1])
                throw std::runtime_error("invalid DIMACS vertex weight");
            has_weight[id - 1] = 1;
            g.weight[id - 1] = weight;
        } else if (tag == 'e') {
            int a, b;
            if (!has_header || !(row >> a >> b) || a < 1 || b < 1 || a > g.n || b > g.n ||
                a == b)
                throw std::runtime_error("invalid DIMACS edge");
            if (a > b) std::swap(a, b);
            edges.emplace_back(a - 1, b - 1);
        } else {
            throw std::runtime_error("unexpected DIMACS line");
        }
    }
    if (!has_header || static_cast<i64>(edges.size()) != g.undirected_edges ||
        std::any_of(has_weight.begin(), has_weight.end(), [](unsigned char x) { return !x; }))
        throw std::runtime_error("incomplete DIMACS graph");
    std::sort(edges.begin(), edges.end());
    if (std::adjacent_find(edges.begin(), edges.end()) != edges.end())
        throw std::runtime_error("duplicate DIMACS edge");
    g.first.assign(g.n + 1, 0);
    for (auto [a, b] : edges) {
        ++g.first[a + 1];
        ++g.first[b + 1];
    }
    for (int v = 0; v < g.n; ++v) g.first[v + 1] += g.first[v];
    g.adjacent.resize(static_cast<std::size_t>(2 * g.undirected_edges));
    auto next = g.first;
    for (auto [a, b] : edges) {
        g.adjacent[next[a]++] = b;
        g.adjacent[next[b]++] = a;
    }
    g.repair_score.resize(g.n);
    for (int v = 0; v < g.n; ++v) {
        const auto degree = g.first[v + 1] - g.first[v];
        g.repair_score[v] = static_cast<double>(g.weight[v]) / std::sqrt(1.0 + degree);
    }
    return g;
}

struct State {
    const Graph& g;
    std::vector<unsigned char> in;
    std::vector<int> blockers;
    std::vector<i64> blocker_weight;
    i64 value = 0;

    explicit State(const Graph& graph)
        : g(graph), in(graph.n, 0), blockers(graph.n, 0), blocker_weight(graph.n, 0) {}

    void clear() {
        std::fill(in.begin(), in.end(), 0);
        std::fill(blockers.begin(), blockers.end(), 0);
        std::fill(blocker_weight.begin(), blocker_weight.end(), 0);
        value = 0;
    }
    void add(int v) {
        if (in[v] || blockers[v]) throw std::runtime_error("infeasible add");
        in[v] = 1;
        value += g.weight[v];
        for (int j = g.first[v]; j < g.first[v + 1]; ++j) {
            int u = g.adjacent[j];
            ++blockers[u];
            blocker_weight[u] += g.weight[v];
        }
    }
    void remove(int v) {
        if (!in[v]) throw std::runtime_error("remove of absent vertex");
        in[v] = 0;
        value -= g.weight[v];
        for (int j = g.first[v]; j < g.first[v + 1]; ++j) {
            int u = g.adjacent[j];
            --blockers[u];
            blocker_weight[u] -= g.weight[v];
        }
    }
    void load(const std::vector<unsigned char>& bits) {
        clear();
        for (int v = 0; v < g.n; ++v)
            if (bits[v]) add(v);
    }
};

struct Search {
    Graph g;
    std::string mode;
    std::mt19937_64 rng;
    Clock::time_point start;
    Clock::time_point deadline;
    double elapsed_offset;
    std::ofstream trace;
    std::vector<unsigned char> best;
    std::vector<int> mark;
    int generation = 0;
    i64 best_value = 0;
    i64 iterations = 0;
    i64 improvements = 0;
    i64 restarts = 0;

    Search(Graph graph, std::string selected_mode, std::uint64_t seed,
           double seconds, double offset, const std::string& trace_path)
        : g(std::move(graph)), mode(std::move(selected_mode)), rng(seed),
          start(Clock::now()), deadline(start + std::chrono::duration_cast<Clock::duration>(
                                   std::chrono::duration<double>(seconds))),
          elapsed_offset(offset), trace(trace_path), best(g.n, 0), mark(g.n, 0) {
        if (!trace) throw std::runtime_error("cannot open trace");
        trace << "elapsed_seconds,value_ticks,iteration,restart\n";
    }
    double elapsed() const {
        return elapsed_offset + std::chrono::duration<double>(Clock::now() - start).count();
    }
    bool time_up() const { return Clock::now() >= deadline; }
    bool remember(const State& s) {
        if (s.value <= best_value) return false;
        best_value = s.value;
        best = s.in;
        ++improvements;
        trace << std::fixed << std::setprecision(6) << elapsed() << ',' << best_value
              << ',' << iterations << ',' << restarts << '\n';
        if ((improvements & 63) == 0) trace.flush();
        return true;
    }
    void gather_neighbours(int v, std::vector<int>& target) {
        ++generation;
        for (int j = g.first[v]; j < g.first[v + 1]; ++j) {
            int u = g.adjacent[j];
            if (mark[u] != generation) {
                mark[u] = generation;
                target.push_back(u);
            }
        }
    }
    void sort_repair(std::vector<int>& candidates) {
        std::sort(candidates.begin(), candidates.end(), [&](int a, int b) {
            if (g.repair_score[a] != g.repair_score[b])
                return g.repair_score[a] > g.repair_score[b];
            return a < b;
        });
    }
    void insert_evict_and_repair(State& s, int v) {
        std::vector<int> conflicts;
        std::vector<int> candidates;
        for (int j = g.first[v]; j < g.first[v + 1]; ++j) {
            int u = g.adjacent[j];
            if (s.in[u]) conflicts.push_back(u);
        }
        ++generation;
        for (int u : conflicts) {
            s.remove(u);
            for (int j = g.first[u]; j < g.first[u + 1]; ++j) {
                int q = g.adjacent[j];
                if (q != v && mark[q] != generation) {
                    mark[q] = generation;
                    candidates.push_back(q);
                }
            }
        }
        s.add(v);
        sort_repair(candidates);
        for (int u : candidates)
            if (!s.in[u] && !s.blockers[u]) s.add(u);
    }
    bool remove_refill_if_better(State& s, int v) {
        const i64 original = s.value;
        s.remove(v);
        std::vector<int> candidates;
        for (int j = g.first[v]; j < g.first[v + 1]; ++j) {
            int u = g.adjacent[j];
            if (!s.in[u] && !s.blockers[u]) candidates.push_back(u);
        }
        sort_repair(candidates);
        std::vector<int> added;
        for (int u : candidates) {
            if (!s.blockers[u]) {
                s.add(u);
                added.push_back(u);
            }
        }
        if (s.value > original) return true;
        for (int u : added) s.remove(u);
        s.add(v);
        return false;
    }
    void construct(State& s, int rank_window, double degree_exponent) {
        s.clear();
        std::vector<int> order(g.n);
        std::iota(order.begin(), order.end(), 0);
        std::sort(order.begin(), order.end(), [&](int a, int b) {
            double as = std::log(static_cast<double>(g.weight[a])) -
                        degree_exponent * std::log1p(g.first[a + 1] - g.first[a]);
            double bs = std::log(static_cast<double>(g.weight[b])) -
                        degree_exponent * std::log1p(g.first[b + 1] - g.first[b]);
            if (as != bs) return as > bs;
            return a < b;
        });
        // Rank-based restricted candidate list; feasibility is checked at insertion.
        std::vector<int> rcl;
        std::size_t next = 0;
        while (next < order.size() || !rcl.empty()) {
            for (std::size_t k = 0; k < rcl.size();) {
                if (s.blockers[rcl[k]]) {
                    rcl[k] = rcl.back();
                    rcl.pop_back();
                } else ++k;
            }
            while (static_cast<int>(rcl.size()) < rank_window && next < order.size()) {
                int v = order[next++];
                if (!s.blockers[v]) rcl.push_back(v);
            }
            if (rcl.empty()) continue;
            std::uniform_int_distribution<std::size_t> pick(0, rcl.size() - 1);
            std::size_t at = pick(rng);
            int v = rcl[at];
            rcl[at] = rcl.back();
            rcl.pop_back();
            if (!s.blockers[v]) s.add(v);
        }
    }
    void local_search(State& s) {
        std::vector<int> order(g.n);
        std::iota(order.begin(), order.end(), 0);
        for (;;) {
            if (time_up()) return;
            bool changed = false;
            std::shuffle(order.begin(), order.end(), rng);
            for (int v : order) {
                if (time_up()) return;
                ++iterations;
                if (!s.in[v] && g.weight[v] > s.blocker_weight[v]) {
                    insert_evict_and_repair(s, v);
                    remember(s);
                    changed = true;
                }
            }
            if (changed) continue;
            std::vector<int> selected;
            for (int v = 0; v < g.n; ++v) if (s.in[v]) selected.push_back(v);
            std::shuffle(selected.begin(), selected.end(), rng);
            for (int v : selected) {
                if (time_up()) return;
                ++iterations;
                if (remove_refill_if_better(s, v)) {
                    remember(s);
                    changed = true;
                    break;
                }
            }
            if (!changed) return;
        }
    }
    void run_grasp() {
        State s(g);
        constexpr int windows[] = {4, 8, 16};
        constexpr double exponents[] = {0.7, 1.0, 1.3};
        while (!time_up()) {
            int variation = static_cast<int>(restarts % 3);
            construct(s, windows[variation], exponents[variation]);
            remember(s);
            local_search(s);
            ++restarts;
        }
    }
    void run_sa() {
        State s(g);
        construct(s, 1, 1.0);
        remember(s);
        std::vector<i64> sorted_weights = g.weight;
        std::nth_element(sorted_weights.begin(), sorted_weights.begin() + sorted_weights.size()/2,
                         sorted_weights.end());
        const double scale = static_cast<double>(sorted_weights[sorted_weights.size()/2]);
        const double hot = 2.0 * scale;
        const double cold = 0.01 * scale;
        std::uniform_int_distribution<int> pick(0, g.n - 1);
        std::uniform_real_distribution<double> unit(0.0, 1.0);
        double last_cycle = 0.0;
        while (!time_up()) {
            const double t = elapsed() - elapsed_offset;
            // Ten fixed cooling cycles over the allotted search time.
            const double budget = std::chrono::duration<double>(deadline - start).count();
            const double cycle_length = std::max(1.0, budget / 10.0);
            const double cycle = std::floor(t / cycle_length);
            if (cycle > last_cycle) {
                s.load(best);
                last_cycle = cycle;
                ++restarts;
            }
            const double phase = std::fmod(t, cycle_length) / cycle_length;
            const double temperature = hot * std::pow(cold / hot, phase);
            int v = pick(rng);
            ++iterations;
            if (s.in[v]) continue;
            const i64 gain = g.weight[v] - s.blocker_weight[v];
            if (gain >= 0 || unit(rng) < std::exp(static_cast<double>(gain) / temperature)) {
                insert_evict_and_repair(s, v);
                remember(s);
            }
        }
    }
    void write_result(const std::string& path, std::uint64_t seed) {
        trace.flush();
        std::ofstream out(path);
        if (!out) throw std::runtime_error("cannot open result");
        out << "{\"mode\":\"" << mode << "\",\"seed\":" << seed
            << ",\"vertices\":" << g.n << ",\"edges\":" << g.undirected_edges
            << ",\"value_ticks\":" << best_value << ",\"selected\":[";
        bool first = true;
        for (int v = 0; v < g.n; ++v) if (best[v]) {
            if (!first) out << ',';
            first = false;
            out << v;
        }
        out << "],\"iterations\":" << iterations << ",\"restarts\":" << restarts
            << ",\"improvements\":" << improvements
            << ",\"native_elapsed_seconds\":" << std::fixed << std::setprecision(6)
            << (elapsed() - elapsed_offset) << "}\n";
    }
};

int main(int argc, char** argv) {
    try {
        if (argc != 15) {
            std::cerr << "usage: classical_mwis --graph DIMACS --mode grasp|sa --seed int "
                         "--seconds float --offset-seconds float --result file --trace file\n";
            return 2;
        }
        std::string graph_path, mode, result_path, trace_path;
        std::uint64_t seed = 0;
        double seconds = 0, offset = 0;
        for (int i = 1; i < argc; i += 2) {
            std::string key(argv[i]);
            std::string value(argv[i + 1]);
            if (key == "--graph") graph_path = value;
            else if (key == "--mode") mode = value;
            else if (key == "--seed") seed = std::stoull(value);
            else if (key == "--seconds") seconds = std::stod(value);
            else if (key == "--offset-seconds") offset = std::stod(value);
            else if (key == "--result") result_path = value;
            else if (key == "--trace") trace_path = value;
            else throw std::runtime_error("unknown argument: " + key);
        }
        if ((mode != "grasp" && mode != "sa") || graph_path.empty() || result_path.empty() ||
            trace_path.empty() || !std::isfinite(seconds) || seconds <= 0 ||
            !std::isfinite(offset) || offset < 0)
            throw std::runtime_error("invalid arguments");
        auto native_start = Clock::now();
        Graph graph = read_dimacs(graph_path);
        const double load_seconds = std::chrono::duration<double>(Clock::now() - native_start).count();
        if (seconds <= load_seconds) throw std::runtime_error("graph loading exhausted budget");
        Search search(std::move(graph), mode, seed, seconds - load_seconds, offset + load_seconds,
                      trace_path);
        if (mode == "grasp") search.run_grasp();
        else search.run_sa();
        search.write_result(result_path, seed);
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "classical_mwis: " << e.what() << '\n';
        return 1;
    }
}
