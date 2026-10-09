// Fixed single-configuration GRASP for exact-weight MWIS. C++17, one thread.
// RCL=8, static rank w/(1+degree), and positive-gain insert/evict descent.
// No construction variant cycling, greedy refill, or remove-one/refill moves.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
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
    i64 m = 0;
    std::vector<i64> weight;
    std::vector<int> first;
    std::vector<int> adjacent;
};

Graph read_dimacs(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open graph");
    Graph g;
    std::vector<std::pair<int, int>> edges;
    std::vector<unsigned char> weighted;
    bool header = false;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == 'c') continue;
        std::istringstream row(line);
        char tag;
        row >> tag;
        if (tag == 'p') {
            std::string kind;
            if (header || !(row >> kind >> g.n >> g.m) || kind != "edge" || g.n < 1 || g.m < 0)
                throw std::runtime_error("invalid DIMACS header");
            header = true;
            g.weight.assign(g.n, 0);
            weighted.assign(g.n, 0);
            edges.reserve(static_cast<std::size_t>(g.m));
        } else if (tag == 'n') {
            int id;
            i64 w;
            if (!header || !(row >> id >> w) || id < 1 || id > g.n || w <= 0 || weighted[id - 1])
                throw std::runtime_error("invalid DIMACS weight");
            g.weight[id - 1] = w;
            weighted[id - 1] = 1;
        } else if (tag == 'e') {
            int a, b;
            if (!header || !(row >> a >> b) || a < 1 || b < 1 || a > g.n || b > g.n || a == b)
                throw std::runtime_error("invalid DIMACS edge");
            if (a > b) std::swap(a, b);
            edges.emplace_back(a - 1, b - 1);
        } else {
            throw std::runtime_error("unexpected DIMACS record");
        }
    }
    if (!header || static_cast<i64>(edges.size()) != g.m ||
        std::any_of(weighted.begin(), weighted.end(), [](unsigned char x) { return x == 0; }))
        throw std::runtime_error("incomplete DIMACS graph");
    std::sort(edges.begin(), edges.end());
    if (std::adjacent_find(edges.begin(), edges.end()) != edges.end())
        throw std::runtime_error("duplicate DIMACS edge");
    g.first.assign(g.n + 1, 0);
    for (auto [a, b] : edges) { ++g.first[a + 1]; ++g.first[b + 1]; }
    for (int v = 0; v < g.n; ++v) g.first[v + 1] += g.first[v];
    g.adjacent.resize(static_cast<std::size_t>(2 * g.m));
    auto next = g.first;
    for (auto [a, b] : edges) {
        g.adjacent[next[a]++] = b;
        g.adjacent[next[b]++] = a;
    }
    return g;
}

struct Search {
    const Graph& g;
    std::mt19937_64 rng;
    std::vector<unsigned char> selected;
    std::vector<unsigned char> best;
    std::vector<i64> blocker_weight;
    std::vector<int> rank_order;
    i64 value = 0;
    i64 best_value = 0;
    std::uint64_t iterations = 0;
    std::uint64_t improvements = 0;
    std::uint64_t restarts = 0;
    Clock::time_point start;
    Clock::time_point deadline;
    double offset;
    std::ofstream trace;

    Search(const Graph& graph, std::uint64_t seed, double seconds, double elapsed_offset,
           const std::string& trace_path)
        : g(graph), rng(seed), selected(g.n, 0), best(g.n, 0), blocker_weight(g.n, 0),
          rank_order(g.n),
          start(Clock::now()), deadline(start + std::chrono::duration_cast<Clock::duration>(
              std::chrono::duration<double>(seconds))), offset(elapsed_offset),
          trace(trace_path) {
        if (!trace) throw std::runtime_error("cannot open trace");
        trace << "elapsed_seconds,value_ticks,iteration\n";
        trace << std::fixed << std::setprecision(6) << elapsed() << ",0,0\n";
        std::iota(rank_order.begin(), rank_order.end(), 0);
        std::sort(rank_order.begin(), rank_order.end(), [&](int a, int b) {
            const i64 da = 1 + g.first[a + 1] - g.first[a];
            const i64 db = 1 + g.first[b + 1] - g.first[b];
            const __int128 left = static_cast<__int128>(g.weight[a]) * db;
            const __int128 right = static_cast<__int128>(g.weight[b]) * da;
            return left == right ? a < b : left > right;
        });
    }
    double elapsed() const {
        return offset + std::chrono::duration<double>(Clock::now() - start).count();
    }
    double native_elapsed() const {
        return std::chrono::duration<double>(Clock::now() - start).count();
    }
    void remove(int v) {
        if (!selected[v]) throw std::runtime_error("remove of absent vertex");
        selected[v] = 0;
        value -= g.weight[v];
        for (int j = g.first[v]; j < g.first[v + 1]; ++j)
            blocker_weight[g.adjacent[j]] -= g.weight[v];
    }
    void add(int v) {
        if (selected[v] || blocker_weight[v]) throw std::runtime_error("infeasible insertion");
        selected[v] = 1;
        value += g.weight[v];
        for (int j = g.first[v]; j < g.first[v + 1]; ++j)
            blocker_weight[g.adjacent[j]] += g.weight[v];
    }
    bool time_up() const { return Clock::now() >= deadline; }
    void remember() {
        if (value <= best_value) return;
        best_value = value;
        best = selected;
        ++improvements;
        trace << std::fixed << std::setprecision(6) << elapsed() << ','
              << best_value << ',' << iterations << '\n';
        if ((improvements & 63U) == 0) trace.flush();
    }
    void construct() {
        std::fill(selected.begin(), selected.end(), 0);
        std::fill(blocker_weight.begin(), blocker_weight.end(), 0);
        value = 0;
        std::vector<int> rcl;
        rcl.reserve(8);
        std::size_t next = 0;
        while (!time_up() && (next < rank_order.size() || !rcl.empty())) {
            for (std::size_t k = 0; k < rcl.size();) {
                if (blocker_weight[rcl[k]] != 0) {
                    rcl[k] = rcl.back();
                    rcl.pop_back();
                } else {
                    ++k;
                }
            }
            while (rcl.size() < 8 && next < rank_order.size()) {
                int v = rank_order[next++];
                if (blocker_weight[v] == 0) rcl.push_back(v);
            }
            if (rcl.empty()) continue;
            std::uniform_int_distribution<std::size_t> pick(0, rcl.size() - 1);
            const std::size_t at = pick(rng);
            const int v = rcl[at];
            rcl[at] = rcl.back();
            rcl.pop_back();
            if (blocker_weight[v] == 0) {
                add(v);
                ++iterations;
            }
        }
        remember();
    }
    void local_search() {
        std::vector<int> scan = rank_order;
        while (!time_up()) {
            bool changed = false;
            std::shuffle(scan.begin(), scan.end(), rng);
            for (int v : scan) {
                if (time_up()) return;
                ++iterations;
                if (selected[v] || g.weight[v] <= blocker_weight[v]) continue;
                const i64 delta = g.weight[v] - blocker_weight[v];
                const i64 previous = value;
                for (int j = g.first[v]; j < g.first[v + 1]; ++j) {
                    const int u = g.adjacent[j];
                    if (selected[u]) remove(u);
                }
                add(v);
                if (value != previous + delta)
                    throw std::runtime_error("local move delta differs from applied objective");
                remember();
                changed = true;
            }
            if (!changed) return;
        }
    }
    void run() {
        while (!time_up()) {
            ++restarts;
            construct();
            if (!time_up()) local_search();
        }
    }
    void save(const std::string& path, std::uint64_t seed) {
        trace.flush();
        std::ofstream out(path);
        if (!out) throw std::runtime_error("cannot open result");
        out << "{\"mode\":\"grasp_plain\",\"seed\":" << seed
            << ",\"vertices\":" << g.n << ",\"edges\":" << g.m
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
            << native_elapsed() << "}\n";
    }
};

int main(int argc, char** argv) {
    try {
        if (argc != 13) throw std::runtime_error(
            "usage: plain_grasp --graph DIMACS --seed int --seconds float "
            "--offset-seconds float --result file --trace file");
        std::string graph_path, result_path, trace_path;
        std::uint64_t seed = 0;
        double seconds = 0.0, offset = 0.0;
        for (int i = 1; i < argc; i += 2) {
            const std::string key(argv[i]);
            const std::string arg(argv[i + 1]);
            if (key == "--graph") graph_path = arg;
            else if (key == "--seed") seed = std::stoull(arg);
            else if (key == "--seconds") seconds = std::stod(arg);
            else if (key == "--offset-seconds") offset = std::stod(arg);
            else if (key == "--result") result_path = arg;
            else if (key == "--trace") trace_path = arg;
            else throw std::runtime_error("unknown argument");
        }
        if (graph_path.empty() || result_path.empty() || trace_path.empty() ||
            !std::isfinite(seconds) || seconds <= 0 || !std::isfinite(offset) || offset < 0)
            throw std::runtime_error("invalid arguments");
        const auto load_start = Clock::now();
        Graph graph = read_dimacs(graph_path);
        const double load_seconds = std::chrono::duration<double>(Clock::now() - load_start).count();
        if (seconds <= load_seconds) throw std::runtime_error("graph loading exhausted budget");
        Search search(graph, seed, seconds - load_seconds, offset + load_seconds, trace_path);
        search.run();
        search.save(result_path, seed);
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "plain_grasp: " << e.what() << '\n';
        return 1;
    }
}
