## 1. Current Workflow

My daily driver is Claude Code inside the IDE or using the terminal, for feature work, refactoring and debugging. I also use GitHub Copilot at work, since it's the tool provided by the company, but I find myself working better with Claude Code, which is in practice the one I use the most. The habit I've built over the past year is that most of the value comes before any code is generated:

- `/init` once per repo to bootstrap a `CLAUDE.md`, then `/memory` to refine it with the conventions that actually matter: stack, commands, architectural boundaries, testing rules. A good `CLAUDE.md` removes the same correction cycle from every future session.
- `/plan` before anything non-trivial, so the strategy is reviewed and agreed while it's still cheap to change. Once the plan is solid, I move to auto-accept mode and let it execute, keeping permissions scoped.
- Model choice matched to the task, switched with `/model` rather than left on a default. For example, Opus for planning and architectural decisions where reasoning quality matters most or Sonnet as the default for everyday implementation, refactoring and debugging. This keeps cost and latency proportional to the difficulty of the task instead of paying premium-model tokens for trivial work.
- Context hygiene is essential, I use `/context` to see what's filling the window, `/compact` at milestones, fresh sessions per task to avoid drift.
- Reusable skills and subagents for the work that repeats (code review passes, migrations, test scaffolding), plus MCP servers when the model needs to read from the actual systems instead of guessing.

I use the Claude web app for the non-code half: brainstorming, comparing approaches, understanding an unfamiliar domain.

The split between AI-specific and general backend work is real. For plumbing, such as endpoints, Celery tasks, migrations, tests, I tend to delegate; the correctness criteria are explicit and the tests tell me if it's wrong. For an LLM system such as a RAG pipeline, I still sketch the flow on paper first: chunking strategy, retrieval and reranking, the failure modes, where a human or a fallback belongs. Only then do I formalise it in plan mode. The reason is simple: in an LLM pipeline the design is the hard part, and correctness isn't a passing test suite: it's an eval set. I'd rather own the architecture and the evaluation criteria and delegate the implementation.

When it comes to the development workflow itself, I have Copilot handle code review directly on GitHub at pull request time, so it acts as an automated first pass before human review.

## 2. The Good & The Bad

The biggest value is the reallocation of attention. Trivial and mechanical work such as boilerplate, glue code, wiring, one-off scripts, tests, collapses from hours to minutes, which leaves energy for the parts that genuinely need a human, like understanding what the user actually needs, designing the pipeline, making the interface usable, deciding what *not* to build.

The risk is code nobody truly owns. This code runs, it passes review at a glance, and it hides inefficiency, security holes or bugs that cost more to find later than the code took to write. Speed of generation makes review the bottleneck, so review is exactly where I refuse to cut.

That risk compounds when the system is consumed by another agent rather than a human. A person tolerates a vague error message or an odd field name; an agent silently misinterprets both. So contracts stop being documentation and become part of the prompt: strict schemas, explicit and unambiguous tool descriptions, errors that tell the caller what to do next, idempotency for retries, hard limits on cost and loops. And because the consumer is non-deterministic, "it worked when I tried it" proves nothing: you need evals, tracing and an offline judge before anything ships. That's the point of [a talk I gave recently at Speck&Tech](https://www.youtube.com/live/2aAuw03LzIw?si=0rEJFKpApphlIh-L&t=5385), *Build Fast Judge Slow*: generation can be fast precisely because the judgement layer is slow and deliberate.

## 3. The Future

Over the next few years, I think the balance shifts from writing and line-by-line reviewing code toward specifying, orchestrating and verifying it. I expect to spend less time producing implementations myself and more time deciding which implementation actually deserves to exist, then proving that it behaves.

The skills I'm betting on start with problem framing: translating a real user need into a system, and spotting the gap in the market that's actually worth filling. As execution gets cheaper, the bottleneck moves upstream to knowing what to build in the first place. Closely tied to that is taste and differentiation, once everyone has access to the same assistant, generic prompts just produce generic products, so having real judgement about what makes a solution distinctive becomes a genuine competitive asset rather than a nice-to-have.

Then there's the practical skill of directing agents well: decomposing work into pieces they can actually handle, writing specs precise enough to remove ambiguity, building the context and guardrails an agent needs to succeed, and reviewing its output fast without slipping into rubber-stamping. Underneath all of that sits evaluation as a core engineering skill in its own right: designing eval sets, judges and observability for systems that are inherently non-deterministic. This is the piece I don't think gets automated away, because it's precisely what tells you whether the automation actually worked.

And finally, I want to turn the same approach on myself: automating my own overhead, letting agents handle status updates, routine communication and low-signal coordination, so the hours I do spend go toward the work that actually compounds.