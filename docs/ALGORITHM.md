# CIPHEUR C05：算法与实现说明

C05 在一个持续运行的四解原生求解器上增加异步决策层。决策层根据搜索状态、目标槽位的冲突证据和已执行计划的结果，从当次快照绑定的有限计划表中选择一个完整时序计划。主线程校验选择后执行预定义原生操作；模型通信期间，原生搜索继续推进。

本文档对应冻结的 `candidates/c05/source` 实现及八视图复现配置。实际入口是 [`cipheur_v06.plan_solver.solve`](../cipheur_v06/plan_solver.py)，C05 的模型接口是 **选择一个已注册计划的 `plan_id`**。

## 算法流程图

问题模型、统一符号和三个算法的正式表示见 [LaTeX 源文件](c05_model_algorithm.tex)、[模型与伪代码 PDF](c05_model_algorithm.pdf) 及 [中文模型说明](MODEL_AND_PSEUDOCODE.md)。流程图沿用这些符号。

- 英文论文图：[可编辑 draw.io](figures/c05-flowchart.drawio) · [矢量 PDF](figures/c05-flowchart.drawio.pdf) · [SVG](figures/c05-flowchart.drawio.svg)
- 中文算法图：[可编辑 draw.io](figures/c05-flowchart-zh.drawio) · [矢量 PDF](figures/c05-flowchart-zh.drawio.pdf) · [SVG](figures/c05-flowchart-zh.drawio.svg)

![CIPHEUR C05 algorithm flowchart](figures/c05-flowchart.png)

图采用上下两个平行流程：上方是独立的 LLM 计划选择，下方是持续种群搜索。查询 `(o_k,Q_k,H_k)` 与返回选择分别通过独立箭头传递；返回经过绑定校验后更新活动计划。没有返回时，下方仍推进现有计划。

蓝色表示观察与搜索状态更新，橙色表示计划安装和执行，紫色表示独立模型选择。黑白节点表示通用算法步骤。紫色虚线是异步消息，橙色虚线连接执行步骤与展开细节。图中的 `P`、`X*`、`Z`、`H`、`o_k`、`Q_k`、`π` 分别对应正式符号表的种群、最优解、搜索记录、计划区间、观测、有限计划集和活动计划。实验数值参数列在本文后部。

## 1. 输入、目标与持久状态

输入是原始冲突图 $G=(V,E)$。每个顶点对应一个候选接触，顶点整数权重 $w_v$ 表示其接触时长，边表示两个接触不能同时被选择。求解目标为

$$
\max_{S\subseteq V}\; F(S)=\sum_{v\in S}w_v,
\qquad (u,v)\notin E\quad\text{for all distinct }u,v\in S.
$$

运行中还保留天线、卫星、接触起止时刻及冷却间隔等元数据。资源与时间特征用于组织搜索证据和候选操作的优先次序。原图的整数权重、可行性关系和最终目标计算保持一致。实验输出以 `value_ticks` 存储目标；本数据集的 `ticks_per_second=1,000,000`，因此 `contact_seconds=value_ticks/1,000,000`。

初始化先计算 `degree_seed()` 可行解，然后建立四槽位的原生 `Engine`。四条搜索轨迹在整次求解中持续保留。历史最优解独立保留；显式定向重建禁止使用槽位 0，也禁止在进入操作时重建当前最优槽位。

## 2. 组件与职责

| 组件 | 职责 | 对应实现 |
|---|---|---|
| 原始图与目标 | 读取整数权重、冲突边和资源元数据；判断可行性并重算目标 | [`graph.py`](../cipheur_v04/graph.py) |
| 持久原生引擎 | 维护四解种群、历史最优解，执行 ILS、精确交换和槽位重建 | [`native.py`](../cipheur_v05/native.py)、`native_v05/` |
| 搜索观察器 | 汇总进展、停滞、多样性、轨迹及历史操作结果 | [`context.py`](../cipheur_v06/context.py) |
| 目标槽位证据 | 对真实接触根节点重算每个目标槽位的阻塞集合与资源关系 | [`plan_context.py`](../cipheur_v06/plan_context.py) |
| 有限计划表 | 将预定义单操作和结构化重建/恢复组合绑定到当次快照 | [`plans.py`](../cipheur_v06/plans.py) |
| 模型请求与返回 | 发送精简观察包，返回精确的快照 ID 和计划 ID | [`plan_provider.py`](../cipheur_v06/plan_provider.py)、[`compact_plans.py`](../cipheur_v06/compact_plans.py) |
| 计划合同校验 | 检查字段、快照绑定、注册计划和描述符身份 | [`plan_contracts.py`](../cipheur_v06/plan_contracts.py) |
| 主循环与时序执行 | 异步请求、接收安装、按步执行、记录计划区间、最终审计 | [`plan_solver.py`](../cipheur_v06/plan_solver.py) |
| 原生操作执行器 | 将动作和明确的槽位/根节点/模板参数映射为原生操作 | [`actions.py`](../cipheur_v06/actions.py)、[`directed_actions.py`](../cipheur_v06/directed_actions.py) |

## 3. 两个时间尺度

**快层。** 主线程独占 `Engine`。每次原生执行的预算为 `min(epoch_seconds, remaining_budget, remaining_step_dwell)`，复现配置中 `epoch_seconds=1`。一次执行包括准备、程序安装、显式干预、原生计算和效果记录。求解器在每个轮次边界检查模型返回和当前计划的步骤状态。

**慢层。** 第一次请求在 20 秒后进入提交条件；之后按 40 秒的计划时点提交，最多 8 次。只有不存在进行中的请求且剩余时间大于 `request_timeout+2` 秒时才提交。慢层得到的是分离的观察快照，不持有原生引擎。网络请求使用 45 秒超时，返回的生存期为提交后 60 秒。请求进行时继续执行当前原生计划。

计划的时间步和请求时钟分别管理。组合计划的登记时长为 24 秒，末步骤在完成登记时长后继续执行，直到新计划安装或总期限到达。实际安装时间由网络返回与主循环轮次边界共同决定。

## 4. 从原始搜索状态构造证据

观察包包含以下信息：

1. **进度与种群状态**：累计目标、剩余时间、停滞时间、多样性、各槽位目标以及与最优解的差距和重叠。
2. **近期搜索历史**：最近的连续动作区间，以及 10、40、120 秒窗口内已完成轮次的目标增益、种群总增益和搜索时间。
3. **资源与阻塞证据**：最多六个真实接触根节点，及其天线、卫星、时间结构和冲突阻塞信息。
4. **目标槽位描述**：针对每个可替换槽位，从原始邻接表和该槽位的已选集合重新计算阻塞数量、阻塞总权重、同天线/同卫星阻塞数量与根节点是否已被选中。
5. **计划执行记录**：最近八个已关闭计划区间，以及当前活动计划已观察到的增益、种群增益、持续时间和步骤位置。

阻塞数量和权重使用完整阻塞集合；用于展示的阻塞节点 ID 每个根节点最多保留 16 个，复用信息基于这些 ID 样本。完整的已选顶点集合保留在求解器侧。

模型所用的计划表采用无损因子化表示：重复的步骤序列和操作参数分别存为模板表与操作表，每行仍包含精确的计划 ID 和身份信息。压缩仅改变传输表示。

## 5. 有限候选计划

每次决策先根据完整观察构造计划表，再将该表与快照一起冻结。候选描述符是数据对象，包含 `steps` 和可选的 `operation`。其身份哈希由这两部分计算。

### 5.1 九个单操作计划

| 动作 | 具体作用 | 登记时长 |
|---|---|---:|
| `exploit` | 集中较长 ILS 切片于最优轨迹 | 16 s |
| `spread` | 轮转四条轨迹，使用较宽扰动队列 | 24 s |
| `merge` | 为较弱轨迹选择较远供体并进行精确交换评估 | 16 s |
| `relay` | 根据阻塞/邻居关系构造两层结构邻域 | 16 s |
| `antenna` | 按共享天线及时间跨度构造邻域 | 16 s |
| `satellite` | 按共享卫星及时间跨度构造邻域 | 16 s |
| `kick` | 进入时对可替换弱轨迹扰动两次，然后继续 ILS | 16 s |
| `reseed` | 进入时按权重优先重建一个可替换弱轨迹，然后继续 ILS | 16 s |
| `v05_nonllm_adaptive` | 根据多样性阈值切换原生目标/供体/队列参数；也是初始活动计划 | 16 s |

`v05_nonllm_adaptive` 在多样性低于 0.2 时使用 `worst/distant/queue=80`，否则使用 `round_robin/cyclic/queue=32`。

### 5.2 结构化时序计划

组合计划由四个明确参数构成：

- **目标槽位**：槽位 1–3，且不是当前快照中的最优槽位。
- **接触根节点**：观察中最多六个真实阻塞见证之一。
- **资源优先模板**：`antenna`、`satellite` 或 `mixed`。
- **后续搜索**：`spread` 或 `merge`。

其 ID 为 `repair_s{slot}_b{rank}_{template}_{followup}`，其中 `rank` 是该次观察中真实见证的序号。描述符的固定步骤为：

```text
directed_reseed(slot, root, template) + native search: 8 seconds
spread OR merge:                                     16 seconds
last operation continues until the next installation
```

最多三个目标槽位、六个根节点、三个模板和两种后续动作，对应最多 108 个结构化计划；加上九个单操作计划，总数上限为 117。缺失所需天线或卫星标识的组合不进入注册表；完整资源与时间元数据在原生执行入口再校验。槽位保护和可用见证也会减少实际候选数。

## 6. 定向重建的实际执行

进入结构化计划第一步时，执行器检查精确字段 `slot/root/template`、根节点范围、资源元数据与当前最佳槽位。如果检查通过，安装对应的原生优先级程序，并在所选槽位围绕精确根节点调用一次 `seed_slot`。根节点和槽位不会自动改成其他值。

单次进入重建的时间上限是 `min(0.25 s, allocated_epoch/2, remaining_epoch)`，工作量上限是 10,000,000。重建完成后立即对该槽位的结果按原图进行可行性和整数目标校验，当前轮次余下预算用于四槽位轮转的 ILS。后续轮次 `enter=False`，因此不会重复触发重建。8 秒步骤结束后转入 16 秒的 `spread` 或 `merge`。

资源模板使用有符号饱和整数 DSL。令

$$
D(a,b)=\operatorname{trunc}\left(\frac{a}{1+|b|}\right),\qquad
q(v;r)=D\left(w_v B(v,r),\;D(\operatorname{time\_distance}(v,r),\operatorname{duration}(v)+g(v))\right).
$$

`B` 和 `g` 的定义如下；每个运算均按实际原生整数语义执行：

| 模板 | 资源加权项 `B` | 间隔项 `g` |
|---|---|---|
| `antenna` | `1 + 2 × same_antenna` | `ground_gap` |
| `satellite` | `1 + 2 × same_satellite` | `satellite_gap` |
| `mixed` | `1 + same_antenna + same_satellite` | `max(ground_gap, satellite_gap)` |

此优先级用于根节点附近的重建次序。`same_*`、`time_distance` 等由原生程序按照根节点计算；最终返回目标仍按原始接触整数权重求和。

## 7. 模型选择、绑定与安装

模型返回对象必须恰好包含以下四个字段：

```json
{
  "snapshot_id": "the exact supplied snapshot ID",
  "plan_id": "an exact registered plan ID",
  "hypothesis": "a short diagnosis of the observed search state",
  "evidence": ["existing observation identifiers"]
}
```

校验顺序包括：存在有效返回、未超过 TTL、快照 ID 一致、计划 ID 位于冻结表内、字段类型与长度合法、计划描述符哈希一致、求解期限尚未到达。通过后，主线程关闭上一计划区间，安装绑定的完整描述符，并重置步骤索引和进入标志。

格式或绑定校验失败时记录拒绝结果并保持现有活动计划。原生执行时若目标槽位已经成为受保护最优槽位，则明确拒绝该次定向操作。若当前计划还有后续步骤，主循环进入后续步骤；若无后续步骤，则安装默认自适应计划。到达总期限后，清理本地待处理请求状态并记录 `deadline_discarded`，不等待未完成返回；这一操作不取消已经发出的远端模型计算。

## 8. 整段计划记录

计划被替换或总期限到达时，记录从安装开始到关闭时的整段结果：

- 历史最优目标增益 `gain_ticks`。
- 四个槽位目标和的变化 `pool_gain_ticks`。
- 区间时长、已进入/执行的步骤索引和是否为部分执行计划。
- 安装来源、绑定快照和原生操作描述符。

当前尚未结束的计划记录 `right_censored=True`。记录随后作为计划历史进入观察包。每个原生轮次还保存动作、原生循环数、多样性变化、干预边界和是否与模型等待重叠。

## 9. 主循环伪代码

```text
start the overall wall-clock deadline
load original graph; compute feasible initial solution
create persistent native engine with four slots
install the default adaptive plan

while the overall deadline has not been reached:
    poll a completed asynchronous model request
    if its frozen choice passes validation:
        close the current plan interval
        install its bound descriptor

    if the next request is due, no request is pending,
       the call cap is not reached, and enough time remains:
        snapshot search state and summarize evidence
        build and freeze all available plans
        asynchronously request one plan ID

    if the overall deadline has been reached: stop the loop
    if the current timed step has finished:
        advance to the next step, if present
        otherwise continue the final search operation
        reset the step clock; continue to the next main-loop iteration

    execute the active step within its bounded native epoch
    apply an entry intervention only once
    record exact effects and maintain the historical incumbent
    if native execution failed:
        advance to a subsequent step, if available
        otherwise install the adaptive fallback

discard pending result handling; record deadline-discarded replies without waiting
close the final plan interval
check feasibility and recompute the original integer objective
return the best feasible solution and complete execution records
```

## 10. 八视图复现参数

| 参数 | 登记值 |
|---|---|
| 物理数据源 | CP-SCALE-AU-L002 |
| 约束视图 | `g0340`、`g0680`、`g1200`、`g1800`、`gW1200_gE0340_s0150`、`gW0340_gE1200_s0150`、`gW0680_gE1200_s0150`、`gW1200_gE0680_s0150` |
| 随机种子 | 67、71、73、79、83 |
| 每位置预算 | 360 s，端到端软时间上限 |
| 种群 / 原生搜索线程 | 4 / 1 |
| 原生轮次上限 | 1 s |
| 首次请求 / 请求间隔 / 调用上限 | 20 s / 40 s / 8 |
| 网络超时 / 返回 TTL | 45 s / 60 s |
| 历史上下文长度配置 | 30 |
| 模型配置 | DeepSeek Flash；thinking=`enabled`；reasoning effort=`low`；max tokens=8192 |
| 模型臂运行数 | 8 个视图 × 5 个种子 = 40 |

端到端计时包含图读取、证据构造、原生计算、显式干预、模型通信的等待或重叠以及最终整数审计；JSON 文件持久化不包含在求解计时中。并发工作进程是相互独立的实验位置；每个位置的原生搜索线程数为一。

## English figure caption

**CIPHEUR C05: independent asynchronous plan selection and persistent population search.** The upper flow receives an observation-bound query $(o_k,Q_k,H_k)$ and selects a registered whole plan through $L_φ$. The lower flow maintains the population $P$, incumbent $X^*$, search trace $Z$, interval ledger $H$, and active plan $π$. A received choice is checked against its frozen registry before installation; the previous interval is appended to the ledger. At an eligible decision opportunity, the solver constructs a new observation and finite plan set (Algorithm 2) and sends a query without waiting. Native plan advancement and search (Algorithm 3) continue while selection is pending. Directed plans protect incumbent trajectories, rebuild an explicit target once around an observed root using a resource template, and recover through native search. Solid arrows denote algorithm flow; purple dashed arrows denote asynchronous queries and choices.
