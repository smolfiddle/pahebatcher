# Monolith-First, Refactor-Second: An AI Code Generation Methodology

*Derived from the pahebatcher v2 -> v3 transformation — 2704-line single file to 21-module package with zero regressions.*

---

## 1. The Core Insight

Traditional software engineering teaches: design architecture first, then implement.

This is wrong for AI-assisted code generation. The correct sequence is inverted:

```
Phase 1: Build a working monolith        Phase 2: Refactor into clean architecture
─────────────────────────────────────    ────────────────────────────────────────
- One file, all logic                    - Extract natural module boundaries
- No imports between modules             - Apply design patterns retroactively
- Duplicate patterns allowed             - Eliminate duplication
- Global state tolerated                 - Inject dependencies
- Type hints optional                    - Enforce strict types
- Tests are integration tests            - Recast as targeted unit tests
- Goal: correct behavior                 - Goal: correct structure
```

The monolith is not the enemy of good architecture. It is the **proof of work** that makes good architecture possible.

---

## 2. Why This Works

### 2.1 Context Window Economics

AI models have limited context windows. Building a 21-file package from scratch requires the model to hold:

- 21 file interfaces in memory simultaneously
- Import relationships between all 21 files
- Type contracts across module boundaries
- Test structures for each module
- Build configuration (pyproject.toml, Makefile)

This is 15,000-25,000 tokens of coordination overhead *before writing any logic*.

Building a single-file monolith requires holding:

- One file
- No inter-module contracts
- Zero import coordination
- Procedural flow from top to bottom

This is 500-1000 tokens of overhead. The remaining budget goes to logic.

**Result**: The monolith approach leaves 90%+ of the context window for actual problem-solving. The refactor approach lets you spend that freed capacity on architecture *after the problem is solved*.

### 2.2 Boundary Discovery Over Boundary Design

Module boundaries designed upfront are speculative. You guess where the seams should be. Sometimes you're right. Often you're wrong — you create `extractors.py` thinking all extraction logic belongs together, only to discover that Kwik extraction and M3U8 parsing share nothing except the word "extract."

Module boundaries discovered during refactoring are empirical. You look at a working 2704-line file and observe:

- Lines 119-192: Data models. None of them import each other. -> `models.py`
- Lines 195-247: Pure utility functions. No state, no side effects. -> `utils.py`
- Lines 250-266: TLS context. Exactly one function, one concern. -> `tls.py`
- Lines 270-377: Segment store. File I/O + subprocess. Distinct from HTTP. -> `store.py`
- Lines 571-781: Kwik extraction. JS unpacking, M3U8 URL search, resolution buttons. Self-contained. -> `extract/kwik.py`
- Lines 785-839: M3U8 parser. Pure parsing, no HTTP. -> `extract/m3u8.py`
- Lines 1408-1589: AnimePahe scanner. API pagination, variant discovery. -> `extract/scanner.py`

These boundaries emerged from the code, not from a whiteboard. Every module boundary drawn this way is correct by construction — it was already a natural cluster in the working code.

### 2.3 Regression Safety

Refactoring a working monolith carries a unique guarantee: the behavior is already correct. You are not building new functionality. You are reorganizing proven functionality.

The test suite anchors this:

```
v2 monolith:     13 tests against the monolith interface
v3 refactor:     88 tests against module interfaces
                    |
                    +-- Same behaviors tested, now at module granularity
```

Every refactoring step is verifiable. Move `sanitize()` from `pahe_batcher.py` to `utils.py`. Run the old tests. They pass. The refactor is correct.

Without the monolith phase, you would be building tests against code that doesn't exist yet — testing a design, not an implementation.

### 2.4 Natural Architecture Emergence

In pahebatcher v2, these patterns existed but were invisible in a flat file:

```
Section 6:  Solver class                -> solver.py
Section 7:  Kwik extraction functions   -> extract/kwik.py
Section 8:  M3U8 parser                -> extract/m3u8.py
Section 9:  Dashboard class            -> ui/dashboard.py
Section 10: HTTP helpers               -> http.py
Section 11: Episode downloader class   -> downloader.py (EpisodeDownloader)
Section 12: Batch downloader class     -> downloader.py (BatchOrchestrator)
Section 13: Scanner class              -> extract/scanner.py
Section 14: Episode selection          -> ui/prompts.py
Section 17: Stream mode                -> stream.py
Section 18: Session manager            -> sessions.py
```

The section comments in the original code (`# 6. FLARESOLVERR CLIENT`) were the developer's own recognition of boundaries. The monolith already knew how to be modular — it just hadn't been split yet.

The refactoring step doesn't invent architecture. It materializes architecture that was already latent.

---

## 3. The Methodology — Step by Step

### Phase 1: The Monolith

#### Step 1.1: Define the contract

Before writing any code, define:
- What is the input? (CLI flags, environment variables, configuration)
- What is the output? (files on disk, terminal display, exit codes)
- What external services does it talk to? (APIs, databases, subprocesses)
- What are the failure modes? (network errors, missing dependencies, invalid input)

For pahebatcher: input = AnimePahe URL + CLI flags, output = MP4 files + Rich TUI display, services = FlareSolverr + AnimePahe API + Kwik CDN + ffmpeg + MPV, failures = network + Cloudflare + missing deps + invalid URLs.

#### Step 1.2: Write the happy path as a single procedural flow

Start with the simplest case: user provides a valid URL, wants to download all episodes at 1080p. Write this as one continuous procedural flow. No classes, no abstractions — just functions called in sequence.

```python
def main():
    args = parse_args()
    check_prerequisites()
    anime = scan_series(args.url)
    episodes = select_episodes(anime, args)
    config = build_config(args)
    download_all(anime, episodes, config)
```

Prove this works end-to-end before adding any complexity.

#### Step 1.3: Add error handling around the happy path

Wrap each step in try/except. Add retry logic for network calls. Add validation for inputs. Add fallbacks for missing dependencies. The goal is resilience, not structure — the code can be ugly as long as it's correct.

#### Step 1.4: Add user experience features

Rich TUI output, progress bars, interactive prompts, configuration wizards. These are inherently coupled to the flow and belong in the monolith. Extracting them prematurely creates awkward abstractions.

#### Step 1.5: Add edge case handling

SUB vs DUB detection. Episode range parsing. Partial downloads. Session resume. Orphan cleanup. Each edge case adds 1-3 functions to the file. The monolith absorbs them without architectural overhead — no need to design module interfaces for each one.

#### Step 1.6: Write integration tests against the monolith

These tests validate behavior, not structure. They call `main()` or the key entry points with known inputs and check outputs. They don't care which file the code lives in. They will survive the refactor and become your regression safety net.

#### Stop condition for Phase 1

The monolith is done when:
- All user-facing features work correctly
- All error paths are handled
- A reasonable test suite passes
- You have used the tool yourself and found it useful

### Phase 2: The Refactor

#### Step 2.1: Audit the monolith for natural boundaries

Read the entire file and annotate every cluster. A cluster is a group of related items (classes, functions, constants) that:
- Share a common purpose (e.g., "everything about FlareSolverr")
- Have high internal cohesion (they call each other)
- Have low external coupling (they are called by few other things)

Mark each cluster with its natural module name. If you can't name it in 1-2 words, it's not a cluster yet — the boundary isn't clear.

For pahebatcher, the audit produced 21 clusters from 2704 lines. Some clusters were 11 lines (`config.py`). Some were 400 lines (`main.py`). Size doesn't determine boundary — cohesion does.

#### Step 2.2: Extract leaf modules first

Leaf modules are those that nothing else depends on. In pahebatcher:

```
Level 0 (no internal deps):  models.py, config.py, utils.py, tls.py, cache.py
Level 1 (depend on L0):     store.py, http.py, solver.py
Level 2 (depend on L0+L1):  extract/m3u8.py, extract/kwik.py, extract/scanner.py
Level 3 (depend on L0-L2):  ui/console.py, ui/dashboard.py, ui/tables.py, ui/prompts.py
Level 4 (depend on L0-L3):  downloader.py, stream.py, sessions.py
Level 5 (orchestrator):     main.py, __init__.py, __main__.py
```

Extract in dependency order. At each step, run the tests. If they pass, the extraction is correct. If they fail, you missed a dependency — check imports.

This bottom-up extraction guarantees that at no point do you have a broken intermediate state. Every commit compiles and passes tests.

#### Step 2.3: Apply design patterns retroactively

The monolith inevitably has anti-patterns that the refactor can fix:

| Monolith pattern | Refactored pattern | Example from pahebatcher |
|---|---|---|
| Module-level globals (`_solver_cache = TTLCache()`) | Instance attributes on injected objects | `Solver` gets its own cache, injectable |
| Classmethods as singletons (`Solver.request()`) | Instance methods with constructor DI | `solver = Solver(url); solver.request(...)` |
| Mixed threading + asyncio | Pure asyncio | `TTLCache` uses `asyncio.Lock`, not `threading.Lock` |
| Soft imports (`try: import aiohttp`) | Hard requirements in `pyproject.toml` | All deps required, no fallback paths |
| Inline constants (`HLS_WORKERS = 24`) | Config dataclass (`AppContext`) | All settings in one injectable object |
| `run_in_executor` for sync code | Native async implementations | `Solver` uses `aiohttp` directly |

These are not architecture decisions made upfront. They are **observed problems** in the monolith, fixed during extraction.

#### Step 2.4: Expand tests to module granularity

The monolith's integration tests prove behavior. Now add unit tests that prove module contracts:

```
Monolith tests (13):       "Does main() produce correct output?"
Module tests (88):         "Does TTLCache evict correctly?"
                           "Does parse_m3u8() extract AES keys?"
                           "Does _parse_resolution_buttons() detect DUB?"
                           "Does SegmentStore.write_seg() use atomic rename?"
```

Each module test is a micro-contract. If `store.py` passes its tests and `downloader.py` passes its tests, you can be confident that changing one module won't break the other — because they're tested independently at their interfaces.

#### Step 2.5: Add build infrastructure

A monolith needs nothing — `python script.py`. A package needs:

- `pyproject.toml` with PEP 621 metadata
- `console_scripts` entry points
- Linting configuration (ruff)
- Type checking configuration (mypy strict)
- Test configuration (pytest with asyncio auto-mode)
- Development tooling (Makefile)
- Dependency specification (required vs dev)
- `.gitignore` for build artifacts

Add these after the code is stable. Build infrastructure is worthless if the code changes next week — it should describe what exists, not what is planned.

#### Step 2.6: Delete the monolith

The final step. Once every module passes its tests, and the package installs via `pip install -e .`, and the CLI help output matches, delete the monolith. Git history preserves it forever. The working tree shows only the refactored result.

This is psychologically important. Keeping the old file "just in case" creates ambiguity — which is the real version? The answer must be unambiguous: the new one.

---

## 4. When This Methodology Applies

### Good candidates

- CLI tools and scripts that start as single files
- Data processing pipelines with clear stages
- Web scrapers with distinct extract/transform/load phases
- Small-to-medium applications (1,000-10,000 lines)
- Projects where the problem domain is understood but the code structure is not yet clear

### Poor candidates

- Systems with hard real-time constraints (architecture affects correctness)
- Multi-service architectures (RPC boundaries must exist from day one)
- Projects with >2 developers from the start (merge conflicts on a monolith are painful)
- Frameworks or libraries consumed by others (public API stability matters from v0.1)
- Projects where the module structure is legally required (compliance, auditing)

### The sweet spot

**Solo developer or solo AI agent building a tool for a known domain.** The monolith phase proves the tool works. The refactor phase makes it maintainable. No coordination overhead, no premature design, no merge conflicts.

---

## 5. Why This Beats "Architecture First" for AI Generation

### 5.1 Token Efficiency

An AI designing 21 files from scratch spends 40-60% of its context budget on coordination and contracts. An AI building a monolith spends 5-10% on coordination. The remaining 90% goes to logic, error handling, and edge cases — the things that make the tool actually work.

### 5.2 Correctness Density

Architecture-first code often has beautiful interfaces around empty implementations. The module boundaries are perfect, the type contracts are rigorous, and nothing actually works end-to-end.

Monolith-first code has ugly interfaces around working implementations. The file is too long, the functions are too coupled, and everything works end-to-end.

Refactoring the second into the first is a deterministic process. Converting the first into the second requires solving the problem again.

### 5.3 Iteration Speed

Adding a feature to a monolith: 1 file, 1 edit, 1 test.

Adding a feature to a pre-designed architecture: "Does this go in `extractors.py` or `parsers.py`? Does it need a new service? Should I inject it via constructor or pass it as a parameter? Does this break the layered architecture? Do I need a new interface class?"

The monolith lets you answer these questions by looking at the code that already exists and works. The architecture makes you answer them before you've written a line.

### 5.4 The Refactor is a Learning Step

When an AI refactors a monolith it built, it is revisiting its own code with fresh context. It sees:

- Which patterns it repeated (extract into shared utility)
- Which edge cases it missed (extract into focused module)
- Which dependencies it over-coupled (break with DI)
- Which names it should have used (rename during extraction)

This is essentially a code review by the same entity that wrote the code, performed with full understanding of the problem domain. No human reviewer achieves this depth of understanding without weeks of context building.

---

## 6. Concrete Metrics from pahebatcher

| Metric | Monolith (v2) | Refactored (v3) | Delta |
|---|---|---|---|
| Files | 1 | 21 | +20 |
| Lines of code | 2704 | 3400 | +26% |
| Test count | 13 | 88 | +577% |
| Test density | 0.48 per 100 LOC | 2.59 per 100 LOC | +440% |
| Type coverage | ~30% (partial) | ~95% (mypy strict) | +217% |
| Cyclic dependencies | N/A (single file) | 0 | Clean DAG |
| Global mutable state | 4 instances | 0 | Eliminated |
| Retry code paths | 3 (urllib, aiohttp, Solver) | 1 (HttpClient) | -67% |
| Install method | Copy-paste script | `pip install -e .` | Professional |
| Context window usage during build | ~40% coordination | 100% logic (monolith) then 60% logic / 40% coordination (refactor) | Split across phases |

The line count increased (+26%) because of:
- Module-level docstrings
- Explicit type annotations (needed for mypy strict)
- Proper import statements replacing inline imports
- `__init__.py` and `__main__.py` files
- More detailed test fixtures

None of this is bloat. It's the structural cost of modularity — paid once, amortized forever.

---

## 7. The Refactor Checklist

When transitioning from monolith to modules, verify:

- [ ] All existing tests pass unchanged
- [ ] New module tests cover all public interfaces
- [ ] `pip install -e .` succeeds
- [ ] CLI entry point produces identical `--help` output
- [ ] Linting passes on all modules (`ruff check src/`)
- [ ] Type checking passes on all modules (`mypy src/`)
- [ ] No circular imports exist (verify with import graph)
- [ ] No module-level side effects (verify by auditing `__init__.py` and top-level code)
- [ ] Old monolith file is deleted (git history preserves it)
- [ ] `.gitignore` excludes build artifacts (`*.egg-info/`, `__pycache__/`, `dist/`)
- [ ] README reflects the new install command
- [ ] All soft imports are converted to hard dependencies

---

## 8. Anti-Patterns to Avoid

### Pre-extraction without working code

Do not create `models.py`, `utils.py`, `config.py` as empty files with planned interfaces before the monolith works. Empty interfaces are speculation. Speculation is wrong in ways you can't detect until you implement.

### Premature design patterns

Do not introduce Factory, Strategy, Observer, or any GoF pattern during the monolith phase. The monolith's job is to be correct. Patterns are for the refactor phase, applied only where the monolith reveals a genuine need.

### Parallel module extraction

Do not extract 5 modules simultaneously. Extract one module, run tests, commit. Then extract the next. Serial extraction guarantees each commit is correct. Parallel extraction risks broken intermediate states that are difficult to debug.

### Keeping the monolith "just in case"

Once the refactored package installs and all tests pass, delete the monolith. Keeping it creates confusion about which version is canonical.

---

## 9. Summary

```
+---------------------+          +---------------------+
|    PHASE 1          |          |    PHASE 2          |
|    The Monolith     |   --->   |    The Refactor     |
|                     |          |                     |
| - One file          |          | - 21 files          |
| - Correct behavior  |          | - Correct structure |
| - Ugly code         |          | - Clean code        |
| - Integration tests |          | - Unit tests        |
| - Global state      |          | - Dependency inject |
| - Pattern discovery |          | - Pattern apply     |
| - 100% logic budget |          | - 60% logic budget  |
+---------------------+          +---------------------+

  Time: 60% of project           Time: 40% of project
  Risk: Low (you're proving      Risk: Low (you're reorganizing
        it works)                      proven code)
  Output: A tool that works      Output: A tool that works AND
                                        is maintainable
```

The monolith is not technical debt. It is the cheapest possible way to discover the correct architecture. Paying that debt through refactoring — with a full test suite watching — is the safest possible way to achieve clean code.

**Build ugly. Make it work. Then make it beautiful. In that order.**
