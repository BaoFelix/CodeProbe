# CodeProbe Architecture

> 这份文档讲 CodeProbe 的代码分析引擎"是什么、为什么这么设计、代价在哪"。
> 配对文档:[`LearningLog.md`](LearningLog.md) — 这一段开发过程中学到的可迁移经验。

---

## 一句话定位

把一个 C++ 代码库**转成有结构的依赖图**,然后从图里**自动认出架构核心**(orchestrator)、自动**收掉噪音**(工具类、多态实现)、自动**画出层层递进的职责树**——给人看大模块用,不是给编译器用。

---

## 数据模型

### 实体(Entity)

每一行是一个"代码里能命名的东西"。**类、结构体、方法、字段、命名空间** 都是实体。

| 字段 | 是什么 |
|---|---|
| `kind` | class / struct / interface / enum / method / field / namespace |
| `name` | 短名,如 `logger` |
| `qualified_name` | 完整路径,如 `spdlog::details::logger` |
| `parent_qname` | 它的"父亲"实体的 qualified_name(命名空间含类、类含方法) |
| `file_path`, `start_line`, `end_line` | 在源码里的位置 |
| `signature` | 方法用完整签名;字段用类型字符串;类为空 |
| `attrs` | 杂项 JSON(`abstract`、`is_virtual` ...) |

**关键设计:`parent_qname` 是字符串,不是外键 ID。**
理由:批量写入时不需要"先插父、查 id、再插子"的麻烦,任意顺序插就行。代价是没有 SQL 层的外键约束——但我们每次 `clear_graph()` 是全清重建,不会出现"孤儿"。

### 关系(Relationship)

每一条边代表"A 跟 B 有种联系"。**同一对 (A, B) 允许多条边**(不同 kind 的证据各算一条)。

6 种 kind,从弱到强:

| kind | Lv | 含义 | 触发条件 |
|---|---|---|---|
| `depends` | 0 | A 用了 B 的代码(签名层面或方法体里) | 方法参数/返回类型出现 B |
| `associates` | 1 | A 弱引用 B | 字段是裸指针 / shared_ptr / handle |
| `implements` | 2 | A 实现 B 的接口 | A 继承一个抽象基类/接口 |
| `aggregates` | 3 | A 装一堆 B 但不独占 | 字段是 `vector<B*>` 等容器装指针 |
| `composes` | 4 | A 独占 B | 字段是值类型 / unique_ptr |
| `inherits` | 5 | A 是 B 的子类(具体继承) | A 继承一个具体基类 |

**关键设计:多边并存,不是"保留最强一条"。**
旧 schema 用 `UNIQUE(source, target)`,所以 Workshop 跟 Engine 既 compose 又 aggregate 又 associate 时只能留最强一条,丢掉了 3/4 证据。新模型 4 条边都进表,LLM 看到完整证据。

**关键设计:`#include` 不算 depends。**
include 只表明"可用",不是"使用"。真正的"使用"是签名里出现该类型——这才发出 depends 边。

---

## 解析流水线

```
源文件
   ↓ (tree-sitter parser, 把宏抹掉)
CST 抽象语法树
   ↓ (跑 query)
单文件实体 + 单文件关系(同文件目标已解析)
   ↓ parse_project 跨文件聚合
所有文件汇总
   ↓ 别名展开(typedef / using)
全局短名 → qualified_name 索引
   ↓ 第二轮 resolve(填跨文件 target_qname)
   ↓ 接口/抽象重新分类(全局视角)
完整图谱
```

### 关键模块

| 文件 | 职责 |
|---|---|
| `tool/ts_parser.py` | tree-sitter 解析、实体抽取、关系抽取、跨文件解析 |
| `tool/model.py` | Entity / Relationship 数据类(7 种 kind 的唯一定义在此) |
| `tool/db.py` | SQLite schema 和读写 API(新表 entities/relationships,旧表保留兼容) |
| `tool/workflow.py` | 建图、orchestrator 打分、SCC 缩点、抽象折叠、支配树、职责树 |

---

## Workflow 分析(orchestrator 识别 + 职责树)

5 步:

```
关系列表
  → build_graph         类级有向加权图(边权按 kind)
  → fold_abstractions   抽象折叠(默认 leaves 模式)
  → condense            Tarjan SCC 缩点,环收成 cluster(A,B,...)
  → score_nodes         打分:出度+reach-入度
  → responsibility_tree 支配树 + 深度截断
```

### 边权(workflow.py)

```python
_KIND_WEIGHT = {
    'inherits':   1.0,
    'composes':   1.0,
    'aggregates': 0.8,
    'implements': 0.7,
    'associates': 0.6,
    'depends':    0.3,
}
```

可调。理由:结构关系(继承、独占)比"我用过你"(depends)更强。

### Orchestrator 打分

```
score = weighted_out_degree + 0.5 × reach − 0.8 × weighted_in_degree
```

- **出度大**:协调很多东西
- **reach 大**:能拉起一大片下游
- **入度小**:很少被别人依赖(orchestrator 是"管别人的",不是"被管的")

`classify_utility(g, n)` 反过来:**入度 ≥ 2 + 出度 = 0 + reach = 0** → 工具/基础设施,自动分流到侧栏。

### 抽象折叠(三档可配,默认 `leaves`)

| mode | 行为 | 适合 |
|---|---|---|
| `none` | 不折,原图 | 想看一切细节 |
| `leaves`(默认) | 只折"叶子类",且只折进有 ≥2 子类的"真家族" | 通用,自动适配两种代码风格 |
| `all` | 折到最顶层基类 | 浅继承的应用型代码 |

**关键设计:`≥2 子类`守卫**。否则一个 orchestrator 顺手实现了某个独家接口,会被错误地折进那个接口里(test_src 的 Workshop → ILogger 就是反例)。

### 支配树

`nx.immediate_dominators(C, root)` 算出每个节点的"直接支配者"。
**支配关系 = 职责归属**:A 支配 B → 所有依赖路径到 B 都要经过 A → B 这块职责归 A 管。
共享节点(如 Engine 被 Vehicle 和 Dashboard 都用到)**自动浮到公共支配者那一层**,不会在每个用它的人下面重复画——这是免费魔法,你一行规则没写。

### 多根森林

入度 0 的节点都是独立 workflow 的根(`find_roots`)。一个项目可以有多个根 = 多个独立工作流(Workshop / Vehicle / Outer 在 test_src 是 3 个独立故事)。每个根算一棵自己的支配树。

### 深度截断 + 层内排序

支配树建好后,`responsibility_tree(C, label, root, max_depth=k)` 在第 k 层切断,**同一层内按子树重量降序排**——浅视图永远先露出"管最多事"的那几个,这才符合"快速理解大模块"的目标。

---

## 关键设计决策汇总(选什么 + 弃什么 + 为什么)

| 决策 | 选了 | 弃了 | 为什么 |
|---|---|---|---|
| **解析器** | tree-sitter | regex / Clang | regex 不懂语法、错误率没上限;Clang 太重且不容错,我们要扫半成品/跨语言代码 |
| **数据模型** | 实体-关系图 | 类的属性表 | 旧模型方法/字段不是实体,无法回答"X 类内部有什么"和"方法 A 调方法 B" |
| **parent_qname** | 字符串 | 整数外键 | 批量插入不需要 ID 映射;调试可读;每次重建图无孤儿风险 |
| **多边** | 同对类多条边并存 | UNIQUE 留最强一条 | 不丢证据;LLM 看完整佐证;orchestrator 打分能用边数 |
| **depends 定义** | "用了 B 的代码"(签名/body) | 把 `#include` 也算上 | include 是"可用"不是"使用";include 噪音大,把每个 file 里所有类都关联到所有 include 上太脏 |
| **calls 这个 kind** | 取消,合并进 depends | 单独存在 | 当前目标(orchestrator)不需要方法级粒度;body call 抽取在 C++ 里又难又脆;真要做可用 attrs.via 区分 |
| **接口判定** | 看是否全纯虚 + 无字段 | I 前缀命名约定 | 名字会骗人(Iterator 不是接口、sink 是抽象类却没 I);规则可证伪 |
| **target_qname 歧义** | 留 NULL | 猜一个 | 猜错一次污染依赖图;留 NULL LLM 至少知道"这边不知道" |
| **跨文件 base 重判** | 全局二轮 retag | 单文件判完不动 | 单文件无法知道远方基类是否抽象;retag 是廉价的修正 |
| **抽象折叠** | 默认折叶子(`leaves`)+ ≥2 家族 | 默认折到顶 | 折到顶把 OCCT 几何分类压平成一坨;只折叶子保留中间分类层 |
| **第三方目录** | 默认排除 vendored/bundled | 一律纳入 | 内嵌的 fmt 库污染 spdlog 的 orchestrator 排名 |
| **导出宏** | 解析前抹掉 | 让 tree-sitter 直面 | 不抹会把 `SPDLOG_API` / `Standard_EXPORT` 当类名/类型,整个类体丢失 |
| **CRTP/模板风格** | 检测后告警,不强行打分 | 改打分公式覆盖两种风格 | CRTP 颠倒了"orchestrator = 高出度"的假设;两套公式让两边都半吊子;诚实告警>装作通用 |

---

## 已知局限

按"诚实承认,而非装作全知"的原则列出:

1. **方法体调用图(`calls` 边)未抽取** — 当前目标不需要;真要做需补"成员字段调用 + 参数调用"的窄子集。
2. **`.sch` 私有 DSL 未处理** — 走另一条 regex 路径,目前未实现(test_src 没用到)。
3. **C++ 模板特化**:`Container<int>` 与 `Container<float>` 视为同一个类(我们抽到的是模板名)。对架构分析够用,对类型精确分析不够。
4. **歧义短名**:跨命名空间同名类(如多个 `Plot`)留 `target_qname=NULL` 不猜,需要 include 上下文消歧——未实现。
5. **接口规则**:基于"全纯虚 + 无字段"。C++23 concepts、模板基类(SFINAE 接口)未覆盖。
6. **OCCT 风格的 namespace 命名空间共享类名**:有时同一短名在多个深层 namespace 下都存在,目前会被标 ambiguous 而不解析。
7. **Orchestrator 打分公式系数**写死(`+0.5×reach`、`-0.8×in`),没做项目自适应。
8. **CRTP / 模板元编程项目不在甜区** — Eigen 这类代码的"架构核心"是被继承的基类(高入度、低出度),跟我们打分公式的假设相反。我们用 `detect_style` 主动告警 `style='crtp'` 提示用户绕过 orchestrator 排行榜,直接看 inherits 家族。**不试图用一套公式覆盖两种风格,因为这只会让两边都半吊子。**
9. **DB 写入未接入** — 引擎能跑,但还没把 entities/relationships 真正写进 SQLite(Phase 5b 的工作)。

---

## 数据/状态边界

- **纯函数**:`parse_file`、`parse_project`、`build_graph`、`fold_abstractions`、`condense`、`responsibility_tree`、`classify_field_type`、`_innermost_type_name`、`_resolve_alias_chain` — 同一输入必然同一输出,无副作用。
- **可变状态**:只在 `_refine_class_kinds`(mutates entities in place)和 `parse_project` 内的 retag 阶段(mutates relationships)。两者都是"全图构造期"修订,对外仍是纯函数式接口。
- **磁盘 I/O**:只在 `parse_file` 的源码读取。DB 写入由调用者负责。

这个边界让引擎容易测试、可以并行(将来加并发解析时无锁可加)。
